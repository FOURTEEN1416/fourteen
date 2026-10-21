"""轮次级 LLM 预算总账（2026-10-21 FIX-3）。

缺陷：单轮至少三跳串行 LLM —— 并行段情绪引擎（`my_character/emotion_engine.py`）、
工具闸 L1（`optimized_orchestrator.py` 的 `_review_call`）、主链
（`optimized_orchestrator.py` 的主对话）—— 但「一轮回复最多能花多少 LLM 时间」
**没有单一 owner**：各跳各自为政（工具闸/主链/流式/画像同步/记忆检索各有各的
硬编码值）。结果是「提了一个阈值，整体耗时反而可能更长」。

修法：在 orchestrator 内建轮次级预算常量 ``TOTAL_TURN_LLM_BUDGET``，各跳从
**剩余预算**里取（deadline 递降传递），并补齐
``工具闸预算 + 主链预算 ≤ 轮次预算`` 的不变量断言，让预算不一致立刻变红。
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

_QUERY = "提醒我明天早上七点起床"  # 强时间信号 + 托付动词 → L0 必晋级
_PROVIDER_FAIL_MARK = "工具终审 provider 失败"


class _Registry:
    def get_tools_by_permission(self, level):  # noqa: ANN001, ANN201
        return []

    def get(self, name):  # noqa: ANN001, ANN201
        return None


class _FakeLLM:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def chat_with_tools(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {"tool_calls": [], "content": "闲聊回复"}


def _make_orch(llm):  # noqa: ANN001
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    orch = OptimizedOrchestrator.__new__(OptimizedOrchestrator)
    orch.components = {
        "tools": SimpleNamespace(registry=_Registry()),
        "llm": llm,
        "memory": None,
    }
    return orch


def _run_gate(orch, llm, **kwargs) -> tuple[str, str]:  # noqa: ANN001
    return asyncio.run(
        orch._run_tools_if_needed(
            llm, _QUERY, "system", [], session_key="u1", **kwargs,
        )
    )


# ── 1. 总账不变量 ───────────────────────────────────────────


def test_turn_budget_covers_all_hops():
    """工具闸 + 主链 ≤ 轮次预算，且每一跳都 ≤ 轮次预算。"""
    import orchestrator.optimized_orchestrator as om

    total = om.TOTAL_TURN_LLM_BUDGET
    gate = om.TOOL_GATE_LLM_TIMEOUT_SECONDS
    main = om.MAIN_CHAIN_LLM_TIMEOUT_SECONDS
    assert gate + main <= total, (
        f"轮次预算 {total}s 装不下 工具闸 {gate}s + 主链 {main}s —— "
        "提了单跳阈值却让整轮更长的典型形态"
    )
    assert gate <= total and main <= total


def test_remaining_budget_helper():
    """deadline 递降传递的取数函数：无 deadline 给整额，过期给 0，未过期给差值。"""
    import orchestrator.optimized_orchestrator as om

    assert om.remaining_turn_llm_budget(None) == om.TOTAL_TURN_LLM_BUDGET
    assert om.remaining_turn_llm_budget(time.monotonic() - 5.0) == 0.0
    left = om.remaining_turn_llm_budget(time.monotonic() + 3.0)
    assert 2.0 < left <= 3.0, f"剩余预算计算错误：{left}"


# ── 2. 行为级：工具闸从剩余预算里取 ─────────────────────────


def test_tool_gate_shrinks_to_remaining_budget(monkeypatch, caplog):
    """剩余预算 0.2s（而单跳额度 40s）→ 1.0s 的响应必须被切掉。

    修复前工具闸只读自己的常量、完全不看轮次 deadline：把 deadline 设成快过期
    也不会影响它 → 1.0s 响应正常返回（verdict=no_tool）→ 本用例红。
    """
    llm = _FakeLLM(delay=1.0)
    with caplog.at_level(logging.INFO):
        out = _run_gate(
            _make_orch(llm), llm,
            llm_deadline=time.monotonic() + 0.2,
        )

    assert out == ("", "")
    assert _PROVIDER_FAIL_MARK in caplog.text, (
        f"工具闸未按剩余预算收紧（deadline 未生效）：{caplog.text!r}"
    )


def test_tool_gate_keeps_full_budget_without_deadline(monkeypatch, caplog):
    """不传 deadline（后台/老调用方）→ 保持单跳整额（向后兼容）。"""
    llm = _FakeLLM(delay=0.05)
    with caplog.at_level(logging.INFO):
        out = _run_gate(_make_orch(llm), llm)

    assert out == ("", "")
    assert "verdict=no_tool" in caplog.text, f"无 deadline 时被误收紧：{caplog.text!r}"


# ── 3. 接线：各跳必须共用同一真源 ───────────────────────────


def _wait_for_timeouts(func) -> list[str]:  # noqa: ANN001
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    out: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "wait_for"
        ):
            for kw in node.keywords:
                if kw.arg == "timeout":
                    out.append(ast.unparse(kw.value))
    return out


def test_tool_gate_timeout_is_capped_by_remaining_budget():
    """AST：工具闸的额度必须是「单跳额度 与 剩余预算 取小」，且真的用于 wait_for。

    额度被收在一个具名局部量里（``gate_timeout``）——这是刻意的：让「本跳能花
    多久」在源码里可读。故断言分两步：①存在
    ``X = min(TOOL_GATE_LLM_TIMEOUT_SECONDS, remaining_turn_llm_budget(...))``
    形式的赋值；②``wait_for`` 的 timeout 用的正是该变量。
    """
    import ast
    import inspect
    import textwrap

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(OptimizedOrchestrator._run_tools_if_needed))
    )
    capped: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        text = ast.unparse(node.value)
        if (
            "remaining_turn_llm_budget" in text
            and "TOOL_GATE_LLM_TIMEOUT_SECONDS" in text
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    capped[target.id] = text
    assert capped, (
        "工具闸未把「单跳额度」与「轮次剩余预算」取小（各跳各自为政）"
    )
    timeouts = _wait_for_timeouts(OptimizedOrchestrator._run_tools_if_needed)
    assert set(capped) & set(timeouts), (
        f"取小后的额度未被 wait_for 使用：capped={sorted(capped)} timeouts={timeouts}"
    )


def test_main_chain_timeout_is_capped_by_remaining_budget():
    """AST：主链的 timeout 同样必须是「单跳额度 与 剩余预算 取小」。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    timeouts = _wait_for_timeouts(OptimizedOrchestrator.process_message)
    joined = " | ".join(timeouts)
    assert "MAIN_CHAIN_LLM_TIMEOUT_SECONDS" in joined, f"主链未接单跳额度：{timeouts}"
    assert "remaining_turn_llm_budget" in joined, (
        f"主链未按轮次剩余预算取小（各跳各自为政）：{timeouts}"
    )


def test_stream_path_uses_shared_budget():
    """源码级：流式那跳不得继续写死 30s，必须走同一预算真源。"""
    import inspect

    from orchestrator import _stream_mixin

    src = inspect.getsource(_stream_mixin)
    assert "asyncio.timeout(30)" not in src, "流式仍写死 30s（与总账脱钩）"
    assert "remaining_turn_llm_budget" in src or "TURN_LLM" in src, (
        "流式未接入轮次预算真源"
    )
