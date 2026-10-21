"""「按需检索」= L0 晋级 → L1 终审 → `memory` 工具（2026-10-21 R3 复核后修订）。

**为什么不是「主链带 tools + 二次调用」**（复核 2026-10-21 三条致命项，逐条落账）：
1. `openai_compatible_provider._chat` 在 `content` 为空时直接抛错（`:491-498`），
   即使 message 带 `tool_calls` → 实测 `RuntimeError 模型请求失败，未生成角色回复`；
2. `stream_enabled=true`（生产）时 `chat()` 走 `_chat_aggregated`，`_stream_chat`
   只 yield `delta["content"]`（`:591-597`），**丢弃 delta.tool_calls**；
3. `MultiProviderGateway.chat` 只看文本判成败（`:520-542`），tool_calls-only
   响应被判失败并触发熔断记账 → 加剧质量悬崖。
且 provider 层不在本任务允许清单内。

**为什么是「启用 memory 工具」而不是新别名**（复核条目 #3 实探）：
`ToolRegistry.register` 以 `tool.name` 为键（`tools/base_tool.py:78-81`），
`MemoryTool.name == "memory"`（`tools/builtin/extra_tools.py:33`）——加
`"memory_query": MemoryTool` 只会注册成 `memory`，`dispatch("memory_query")`
返回 `Tool not found`，且 `/api/tools` 会把它标成 `unavailable`。故本批**按真实
键名 `memory` 启用**（`config/system.yaml` 的 `builtin_tools` 追加 `memory`），
`query_profile` 本就在册（复核实测 `has query_profile=True`）。

**隔离不变量**（复核条目 #8，是真风险但不是阻断项）：
- `MemoryTool` 的 `user_key` 只来自服务端注入的 `_meta.session_key`
  （`extra_tools.py:65-74`），LLM 自填一律无视；
- 但返回体里**带 `user_key`**（`:103`）——本批新增出站守卫把它从**回复正文**里
  剥掉（模型回显内部会话键），并断言信封里仍带（内部材料不受影响）；
- `increment_fact_access` 是真实写库（`structured_memory.py:1110-1122`），
  不是「只读副作用」——本批不改它，但在用例里钉住「它只被这次检索触发一次」。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

# ── 1. 配置：memory 必须真的在册（否则 L1 的规则是空话）──────

def test_memory_tool_declared_in_config():
    import yaml

    from utils.project_paths import project_path

    cfg = yaml.safe_load(project_path("config", "system.yaml").read_text(encoding="utf-8"))
    declared = cfg["tools"]["builtin_tools"]
    assert "memory" in declared, (
        "config/system.yaml 未启用 memory 工具 —— L1 规则「调用 memory 工具」是空话"
    )
    assert "query_profile" in declared, "query_profile 本就在册，不得被本次改动挤掉"
    # 既有 13 项一条不得丢
    for name in (
        "weather", "search", "calendar", "calculator", "set_reminder",
        "query_reminders", "manage_reminder", "time_awareness", "character_card",
        "update_user_profile", "remember_facts", "forget_facts", "query_profile",
    ):
        assert name in declared, f"既有启用项 {name} 被本次改动挤掉"


def test_memory_tool_is_registered_when_declared_and_sm_present():
    """注册循环按真实键名注册：`registry.get("memory")` 必须非 None。"""
    from orchestrator._init_mixin import _InitPhasesMixin
    from tools.base_tool import ToolRegistry

    class _Sm:
        def user_key_from_session(self, s):
            return s

    registry = ToolRegistry()
    orch = SimpleNamespace(
        components={"memory": SimpleNamespace(structured_memory=_Sm())},
    )
    cfg = SimpleNamespace(
        tools=SimpleNamespace(
            enabled=True,
            builtin_tools=["memory"],
            execution_timeout_seconds=10.0,
            rate_limit_per_tool_per_minute=3,
        ),
    )
    _InitPhasesMixin._init_tools(orch, cfg)  # type: ignore[arg-type]
    assert registry.get("memory") is None  # 局部 registry 只是对照
    registered = orch.components["tool_registry"]
    assert registered.get("memory") is not None, "memory 工具未按真实键名注册"
    assert registered.get("memory").name == "memory"


def test_no_alias_key_leaks_into_registry():
    """防漂移：不得出现 `memory_query` 这类不存在的键（复核条目 #3 的事故形态）。"""
    import yaml

    from utils.project_paths import project_path

    cfg = yaml.safe_load(project_path("config", "system.yaml").read_text(encoding="utf-8"))
    declared = cfg["tools"]["builtin_tools"]
    assert "memory_query" not in declared, (
        "memory_query 不是真实工具名（MemoryTool.name == 'memory'）；"
        "写进配置只会让 /api/tools 显示 unavailable"
    )


# ── 2. L0 晋级：回忆意图必须进 L1 ──────────────────────────

@pytest.mark.parametrize("text", [
    "你还记得我说过我喜欢吃什么吗",
    "记得我说过什么吗",
    "关于我的记忆里有什么",
    "我的偏好你还记得吧",
])
def test_recall_intent_escalates(text: str):
    from orchestrator import tool_gate

    assert tool_gate.should_escalate(text) is True


@pytest.mark.parametrize("text", [
    "今天好累啊，也不想动",
    "你觉得这部电影怎么样？",
    "我记得你说过这个",   # 陈述自己的记忆，不是索取长期记忆
])
def test_non_recall_does_not_escalate(text: str):
    from orchestrator import tool_gate

    assert tool_gate.should_escalate(text) is False


def test_l1_rule_names_real_tool():
    """L1 规则点名的工具必须在 registry 里真实存在（否则模型拿到无 schema）。"""
    from orchestrator.tool_gate import build_review_messages
    from tools.builtin.extra_tools import MemoryTool

    assert MemoryTool.name == "memory"
    system, _ = build_review_messages("你还记得我说过什么吗", "S", [])
    import re

    assert re.search(r"(?<![\w-])memory(?![\w-])", system)


# ── 3. 真实链路：L1 判定 memory → 执行 → untrusted 信封 ─────


class _Sm:
    """最小 StructuredMemory 替身（隔离语义与生产一致：键即会话键）。"""

    def __init__(self, facts=None):
        self.facts = facts or []
        self.searched: list[tuple[str, str]] = []
        self.access_ids: list[list] = []

    def user_key_from_session(self, session):
        return str(session).strip()

    def search_facts(self, query, user_key=None):
        self.searched.append((query, user_key))
        return [f for f in self.facts if f.get("user_key") == user_key]

    def increment_fact_access(self, ids):
        self.access_ids.append(list(ids))


def _orch_with_registry(sm):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator
    from tools.base_tool import ToolDispatcher, ToolRegistry
    from tools.builtin.extra_tools import MemoryTool

    registry = ToolRegistry()
    registry.register(MemoryTool(sm))
    orch = OptimizedOrchestrator()
    orch.components = {
        "tools": ToolDispatcher(registry, timeout=5.0, rate_limit_per_minute=60),
        "memory": SimpleNamespace(structured_memory=sm),
    }
    return orch


def _llm_returning_memory_call():
    async def chat_with_tools(**kwargs):
        names = [s["function"]["name"] for s in kwargs.get("tools") or []]
        assert "memory" in names, "L1 未把 memory schema 交给模型"
        return {
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "memory", "arguments": '{"query": "吃什么"}'},
            }],
            "content": "",
        }

    return SimpleNamespace(chat_with_tools=chat_with_tools)


def test_memory_call_goes_through_l1_and_returns_untrusted_envelope():
    sm = _Sm([
        {"id": 1, "fact": "喜欢吃辣", "user_key": "2:alice@im.wechat"},
        {"id": 9, "fact": "别人家的事", "user_key": "3:bob@im.wechat"},
    ])
    orch = _orch_with_registry(sm)
    results, direct = asyncio.run(orch._run_tools_if_needed(
        _llm_returning_memory_call(),
        "你还记得我喜欢吃什么吗",
        "S",
        [],
        session_key="2:alice@im.wechat",
        user_id=2,
        turn_id="t-1",
        character_id="c1",
    ))
    assert direct == ""
    assert "喜欢吃辣" in results
    assert "别人家的事" not in results, "跨用户事实泄漏进 tool_results"
    assert "<context trust=\"untrusted\">" in results, "工具结果必须走 untrusted 信封"
    # 隔离不变量（正向）：检索被限定到本会话键
    assert sm.searched and sm.searched[0][1] == "2:alice@im.wechat"
    assert sm.access_ids and sm.access_ids[0] == [1]


def test_memory_tool_without_session_key_is_rejected_not_global():
    """无归属即拒（`missing_session_key`），不得回落全库检索。"""
    sm = _Sm([{"id": 1, "fact": "x", "user_key": "2:a"}])
    orch = _orch_with_registry(sm)
    results, _ = asyncio.run(orch._run_tools_if_needed(
        _llm_returning_memory_call(),
        "你还记得什么",
        "S",
        [],
        session_key="",   # 无会话键
        user_id=None,
        turn_id="t-2",
    ))
    assert sm.searched == [], "无会话键竟发起了检索"
    assert "missing_session_key" in results, "失败原因必须可见（不得假装查到了）"


def test_memory_envelope_carries_user_key_but_reply_must_not():
    """信封里带 user_key（内部材料）；**回复正文**里不得出现（出站剥离）。

    复核条目 #8(a)：`MemoryTool` 返回体含 `user_key`（`extra_tools.py:103`），
    模型回显时会把它写进回复——出站守卫原先不拦。

    ⚠️ 归属说明：该规则的自然 owner 是 `utils/prompt_sanitize.terminal_reply_guard`，
    但该文件不在本任务允许清单内，故本批落在编排层 `_finalize_reply`（流式与非流式
    共用的唯一出口），并在 `strip_internal_identifiers` docstring 登记「下批迁入
    prompt_sanitize 单一真源」。**不得**在别处再造第二份实现。
    """
    from orchestrator.optimized_orchestrator import strip_internal_identifiers

    envelope_sample = "我在记忆里找到了：喜欢吃辣（user_key=2:alice@im.wechat）"
    cleaned = strip_internal_identifiers(envelope_sample)
    assert "2:alice@im.wechat" not in cleaned
    assert "喜欢吃辣" in cleaned
    # 正常文本零改动（不得误伤「你是我的唯一」这类正常句子）
    assert strip_internal_identifiers("好呀，今天冷多穿点。") == "好呀，今天冷多穿点。"
    assert strip_internal_identifiers("我现在在 keys 旁边") == "我现在在 keys 旁边"


def test_finalize_reply_strips_internal_identifiers_end_to_end():
    """行为级：`_finalize_reply` 是唯一出口，必须真的过这道剥离。"""
    import asyncio  # noqa: F401  (保持与其它用例同风格)
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    orch = OptimizedOrchestrator()
    orch.components = {
        "safety": SimpleNamespace(
            check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
            safe_alternative=lambda category: "[拦截]",
        ),
        "persona": None,
    }
    out = orch._finalize_reply(
        "记忆里写着 user_key=2:alice@im.wechat 。",
        "default",
        user_msg="你还记得什么",
        session_id="2:alice@im.wechat",
    )
    assert "2:alice@im.wechat" not in out


def test_memory_does_not_bypass_rate_limit():
    """同一轮同工具一次上限仍生效（limit_tool_calls 未被新路径绕过）。"""
    from orchestrator import tool_gate

    calls = [
        {"function": {"name": "memory", "arguments": "{}"}},
        {"function": {"name": "memory", "arguments": "{}"}},
    ]
    kept = tool_gate.limit_tool_calls(calls)
    assert len(kept) == 1


# ── 4. 与能力声明同口径：不得声称「模型自己会查」───────────

def test_capability_block_does_not_promise_self_service_recall():
    """能力段与 L1 链路口径一致：检索是**系统**在你开口前发起的。

    复核条目 #6：`tests/test_memory_usage_rule.py:35-36` 明令不得教模型
    「主动索取记忆」；本批能力段必须同向，否则两段 prompt 互相打架。
    """
    from shisi.application.persona_service import CAPABILITY_DECLARATION

    assert "系统" in CAPABILITY_DECLARATION
    assert "<search" not in CAPABILITY_DECLARATION
    assert "主动索取" not in CAPABILITY_DECLARATION
