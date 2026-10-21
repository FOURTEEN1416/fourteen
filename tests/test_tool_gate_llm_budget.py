"""工具闸 L1 预算与模型预算的一致性阶梯（2026-10-21 FIX-1）。

缺陷：agnes 读超时已提到 40s（`config/llm_providers.json` → providers.agnes.
request_timeout）、主链外层已提到 60s，但**工具终审那一跳**仍是写死的 15s
（`orchestrator/optimized_orchestrator.py` 的 `_review_call`：
``resp = await asyncio.wait_for(resp, timeout=15.0)``，v1.53 与当前同值）。

后果：慢 agnes 在工具闸白烧 15s 被杀（且失败不重试），随后同一轮又进主链等
第二次 —— 单轮多出一段纯浪费的串行时间。生产 17:46:13 的 `[tool_gate] L1 判决`
与该轮 `[prompt] total=7976` 同秒落盘，证明 L1 确实在关键路径上。

修法：抽具名常量并写成显式规则 ——
``PROVIDER_READ_TIMEOUT_SECONDS ≤ TOOL_GATE_LLM_TIMEOUT_SECONDS ≤ MAIN_CHAIN_LLM_TIMEOUT_SECONDS``
（工具闸消费供应商读预算，对已知慢供应商即 agnes 40s 设阈）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

_QUERY = "提醒我明天早上七点起床"  # 强时间信号 + 托付动词 → L0 必晋级
_PROVIDER_FAIL_MARK = "工具终审 provider 失败"


class _Registry:
    def get_tools_by_permission(self, level):  # noqa: ANN001, ANN201
        return []

    def get(self, name):  # noqa: ANN001, ANN201
        return None


class _FakeLLM:
    """按给定延迟返回 L1 终审响应（tool_calls 为空 = 模型判无需工具）。

    content 必须**非空**：``_is_error_reply("") is True``（空内容是错误哨兵，
    网关契约如此），空串会被正确判成 ProviderFailure 而不是「判无需工具」。
    """

    def __init__(self, delay: float, resp: dict | None = None) -> None:
        self.delay = delay
        self.resp = resp if resp is not None else {"tool_calls": [], "content": "闲聊回复"}
        self.calls = 0

    async def chat_with_tools(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.resp


def _make_orch(llm) -> object:  # noqa: ANN001
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    orch = OptimizedOrchestrator.__new__(OptimizedOrchestrator)
    orch.components = {
        "tools": SimpleNamespace(registry=_Registry()),
        "llm": llm,
        "memory": None,
    }
    return orch


def _run_gate(orch, llm) -> tuple[str, str]:  # noqa: ANN001
    return asyncio.run(
        orch._run_tools_if_needed(
            llm, _QUERY, "system", [], session_key="u1",
        )
    )


def _l0_escalates() -> bool:
    from orchestrator import tool_gate

    return bool(tool_gate.should_escalate(_QUERY))


# ── 1. 行为级：预算必须真的被消费（不是写死字面量）──────────


def test_tool_gate_budget_is_consumed(monkeypatch, caplog):
    """把具名预算调小后，慢于它的响应必须被切断 → 证明调用点读的是常量。

    修复前调用点是字面量 15.0：把常量调小不会影响它，1.0s 的响应会正常返回
    （verdict 不是 provider_fail）→ 本用例红。
    """
    import orchestrator.optimized_orchestrator as om

    assert _l0_escalates(), "前提：该 query 必须能让 L0 晋级，否则用例失去意义"
    monkeypatch.setattr(om, "TOOL_GATE_LLM_TIMEOUT_SECONDS", 0.2, raising=True)

    llm = _FakeLLM(delay=1.0)
    with caplog.at_level(logging.INFO):
        out = _run_gate(_make_orch(llm), llm)

    assert out == ("", ""), "provider 失败必须原样降级主链（保留现场）"
    assert _PROVIDER_FAIL_MARK in caplog.text, (
        f"预算 0.2s 未切断 1.0s 响应——工具闸预算没有真正被消费：{caplog.text!r}"
    )


def test_tool_gate_budget_admits_fast_response(monkeypatch, caplog):
    """预算够大时响应必须被接受（verdict 走 no_tool，而非 provider_fail）。"""
    import orchestrator.optimized_orchestrator as om

    monkeypatch.setattr(om, "TOOL_GATE_LLM_TIMEOUT_SECONDS", 5.0, raising=True)

    llm = _FakeLLM(delay=0.05)
    with caplog.at_level(logging.INFO):
        out = _run_gate(_make_orch(llm), llm)

    assert out == ("", "")
    assert "verdict=no_tool" in caplog.text, f"响应被误杀：{caplog.text!r}"
    assert _PROVIDER_FAIL_MARK not in caplog.text
    assert llm.calls == 1, "不该重复调用（失败不重试语义不变）"


# ── 2. 常量阶梯不变量 ───────────────────────────────────────


def test_timeout_ladder_invariant():
    """工具闸预算 ≤ 主链预算，且两者都 ≥ 供应商读超时。"""
    import orchestrator.optimized_orchestrator as om

    provider = om.PROVIDER_READ_TIMEOUT_SECONDS
    gate = om.TOOL_GATE_LLM_TIMEOUT_SECONDS
    main = om.MAIN_CHAIN_LLM_TIMEOUT_SECONDS
    assert gate <= main, f"工具闸预算 {gate}s 超过主链预算 {main}s"
    assert gate >= provider, f"工具闸预算 {gate}s 低于供应商读超时 {provider}s"
    assert main >= provider, f"主链预算 {main}s 低于供应商读超时 {provider}s"


def test_provider_read_timeout_matches_tracked_config():
    """PROVIDER_READ_TIMEOUT_SECONDS 必须与 tracked 配置里首选供应商的读超时一致。

    常量是配置值的镜像 —— 配置改了而常量没跟，阶梯断言就会失去意义。
    """
    import orchestrator.optimized_orchestrator as om

    cfg_path = Path(__file__).resolve().parent.parent / "config" / "llm_providers.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    providers = data.get("providers") or {}
    # 首选 = sort_order 最小且 enabled 的（与网关建链口径一致）
    enabled = [
        (int(v.get("sort_order") or 99), k, v)
        for k, v in providers.items()
        if isinstance(v, dict) and v.get("enabled", True)
    ]
    assert enabled, "tracked 配置里没有启用的供应商"
    _, key, primary = sorted(enabled)[0]
    raw = primary.get("request_timeout")
    assert raw is not None, f"首选供应商 {key} 未声明 request_timeout"
    assert float(raw) == om.PROVIDER_READ_TIMEOUT_SECONDS, (
        f"常量 {om.PROVIDER_READ_TIMEOUT_SECONDS}s 与配置 {key}.request_timeout={raw} 不一致"
    )


# ── 3. 两处调用点必须使用具名常量（防再次写死）───────────────


def test_callsites_use_named_constants():
    """源码级：工具闸与主链的预算都必须是具名常量，不得回退字面量。

    ⚠️ 2026-10-21 FIX-3 连带改写（原断言钉的是「wait_for 的 timeout 实参**字面
    等于** TOOL_GATE_LLM_TIMEOUT_SECONDS」这个**写法**）：工具闸预算现在要先与
    **轮次剩余预算**取小，再收进具名局部量 ``gate_timeout``（见
    ``tests/test_turn_llm_budget.py::test_tool_gate_timeout_is_capped_by_remaining_budget``，
    那条负责钉「取小」语义）。此处退回语义断言：源码必须引用该具名常量、且
    任何 wait_for 都不得出现写死的 15.0。
    """
    import ast
    import inspect
    import textwrap

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    def _wait_for_timeouts(func) -> list[str]:  # noqa: ANN001
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

    gate_src = inspect.getsource(OptimizedOrchestrator._run_tools_if_needed)
    assert "TOOL_GATE_LLM_TIMEOUT_SECONDS" in gate_src, (
        "工具闸预算必须引具名常量（旧实现写死 15.0）"
    )
    gate_timeouts = _wait_for_timeouts(OptimizedOrchestrator._run_tools_if_needed)
    assert "15.0" not in gate_timeouts, "工具闸预算不得回退字面量 15.0"

    main_timeouts = _wait_for_timeouts(OptimizedOrchestrator.process_message)
    assert any("MAIN_CHAIN_LLM_TIMEOUT_SECONDS" in t for t in main_timeouts), (
        f"主链预算必须引具名常量，实得 {main_timeouts}"
    )
