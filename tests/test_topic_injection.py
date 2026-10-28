"""话题状态注入测试（2026-10-27 话题状态机 Task 2/3）。

覆盖用户验收口径：
- 关闭态 / 无状态：prompt 与旧 `TOPIC_CONTINUITY_RULE` **逐字一致**；
- 开启态：含状态数据块 + 软化接续规则，且不含旧的「至少 2~3 轮」硬约束；
- 会话/角色隔离：A 的状态块不出现在 B 的 prompt；
- 状态块 ≤160 字、不含用户原文全文；
- 后台更新不被同步等待（`_after_process` 立即返回），稀疏 judge 走独立池。

所有数据合成；conftest 已把 `utils.topic_state` 的库/配置重定向到沙箱。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

import pytest

from utils import topic_state as ts
from utils.prompt_sanitize import TOPIC_CONTINUITY_RULE, TOPIC_CONTINUITY_RULE_SOFT

BASE = datetime(2026, 10, 27, 10, 0, 0)
KEY_A = "2:a@im.wechat"
KEY_B = "2:b@im.wechat"

STATE_TEXT = (
    "## 当前话题状态（这是你自己在追的事，不是任务清单）\n"
    "- 正在聊（最多 3 条）：面试准备（2轮）\n"
    "- 你记得的细节：周四二面\n"
    "- 之前没聊完：搬家"
)


@pytest.fixture(autouse=True)
def _isolate_topic_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "_DB_PATH", tmp_path / "topic_state.db", raising=False)
    monkeypatch.setattr(ts, "_CONFIG_PATH", tmp_path / "scheduler_config.json", raising=False)
    return tmp_path


def _enable(tmp_path, **over) -> None:
    block = dict(ts.DEFAULTS)
    block.update(enabled=True, **over)
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"topic_state": block}, ensure_ascii=False), encoding="utf-8"
    )


def _prompt(memory_context=None, character_id: str = "default") -> str:
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    return ps.build_system_prompt(
        character_id=character_id,
        user_message="在吗",
        memory_context=memory_context,
    )


# ── 1. 关闭态 / 无状态：逐字回落旧规则 ─────────────────────────────────


def test_disabled_prompt_segment_equals_old_rule():
    prompt = _prompt()
    assert TOPIC_CONTINUITY_RULE.strip() in prompt
    assert TOPIC_CONTINUITY_RULE_SOFT.strip() not in prompt
    assert "# 当前话题状态" not in prompt


def test_disabled_full_prompt_unchanged():
    """`None` 与空状态块必须产生完全相同的 prompt（关闭态零行为变化）。"""
    assert _prompt(None) == _prompt({"topic_state_text": ""})


def test_no_state_fallback_with_other_memory_keys():
    prompt = _prompt({"facts": ["用户喜欢猫"], "topic_state_text": ""})
    assert TOPIC_CONTINUITY_RULE.strip() in prompt
    assert TOPIC_CONTINUITY_RULE_SOFT.strip() not in prompt
    assert "# 当前话题状态" not in prompt


# ── 2. 开启态：状态块 + 软化规则 ────────────────────────────────────────


def test_enabled_prompt_contains_state_headings():
    prompt = _prompt({"topic_state_text": STATE_TEXT})
    assert "# 当前话题状态" in prompt
    assert "正在聊" in prompt
    assert "你记得的细节" in prompt
    assert "之前没聊完" in prompt


def test_enabled_prompt_contains_softened_rule():
    prompt = _prompt({"topic_state_text": STATE_TEXT})
    assert "同一话题通常自然聊 2~3 轮" in prompt
    assert "对方给出明确新信息或主动换题时" in prompt
    assert "不要硬拽回旧话题" in prompt


def test_enabled_prompt_does_not_contain_hard_old_rule():
    prompt = _prompt({"topic_state_text": STATE_TEXT})
    assert "至少 2~3 轮内守住同一话题" not in prompt
    assert "沿着话题说下去" not in prompt


def test_topic_state_wrapped_untrusted():
    prompt = _prompt({"topic_state_text": STATE_TEXT})
    assert '<context trust="untrusted" source="topic_state">' in prompt
    assert prompt.count('<context trust="untrusted" source="topic_state">') == 1
    assert "</context>" in prompt


def test_state_block_position_after_capability_before_emoji():
    prompt = _prompt({"topic_state_text": STATE_TEXT})
    assert prompt.index("# 你的能力") < prompt.index("# 当前话题状态")
    assert prompt.index("# 当前话题状态") < prompt.index("# 微信表情使用守则")


def test_enabled_prompt_from_real_snapshot(tmp_path):
    """真实状态机 → 快照 → _attach_topic_state → prompt 的端到端链路。"""
    from orchestrator.optimized_orchestrator import _attach_topic_state

    _enable(tmp_path)
    ts.update_after_turn(KEY_A, "default", "我明天去面试", now=BASE, turn_id="t1")
    snap = ts.get_topic_snapshot(KEY_A, now=BASE)
    assert snap["text"]
    prompt = _prompt(_attach_topic_state({}, snap))
    assert "面试" in prompt
    assert "同一话题通常自然聊 2~3 轮" in prompt


# ── 3. 状态块体积与隐私边界 ─────────────────────────────────────────────


def test_state_text_never_exceeds_160(tmp_path):
    _enable(tmp_path)
    long_message = "我明天要去参加一个特别重要的面试，地点在城西的科技园，面试官是部门总监"
    for i, message in enumerate(
        [
            long_message,
            "面试要准备的项目经历还没整理完，晚上得加班弄",
            "另外房东说月底前要给答复，续租还是搬家还没定",
            "周末还想去看一下新出的那部电影，听说评价不错",
        ]
    ):
        ts.update_after_turn(
            KEY_A, "default", message, now=BASE + timedelta(minutes=i), turn_id=f"t{i}"
        )
    snap = ts.get_topic_snapshot(KEY_A, now=BASE)
    assert len(snap["text"]) <= 160
    # 不写用户原文全文：长消息只能以 ≤20 字标题/≤40 字锚点出现
    assert long_message not in snap["text"]


# ── 4. 会话 / 角色隔离 ──────────────────────────────────────────────────


def test_topic_state_isolated_per_session(tmp_path):
    from orchestrator.optimized_orchestrator import _attach_topic_state

    _enable(tmp_path)
    ts.update_after_turn(KEY_A, "default", "我明天去面试", now=BASE, turn_id="a1")
    ts.update_after_turn(KEY_B, "default", "周末去露营", now=BASE, turn_id="b1")
    snap_a = ts.get_topic_snapshot(KEY_A, now=BASE)
    snap_b = ts.get_topic_snapshot(KEY_B, now=BASE)
    assert "面试" in snap_a["text"] and "面试" not in snap_b["text"]
    assert "露营" in snap_b["text"] and "露营" not in snap_a["text"]
    # prompt 层同样不得串台
    assert "露营" not in _prompt(_attach_topic_state({}, snap_a))
    assert "面试" not in _prompt(_attach_topic_state({}, snap_b))


def test_topic_state_isolated_per_character(tmp_path):
    _enable(tmp_path)
    ts.update_after_turn(KEY_A, "char-one", "我明天去面试", now=BASE, turn_id="c1")
    other = ts.get_topic_snapshot(KEY_A, "char-two", now=BASE)
    assert other["text"] == ""
    assert other["active"] == []


# ── 5. 预算内挂载（计划 §6.4：apply_budget 之前合并）────────────────────


def test_attach_topic_state_skips_without_text():
    from orchestrator.optimized_orchestrator import _attach_topic_state

    memory = {"facts": ["用户喜欢猫"]}
    assert _attach_topic_state(memory, {"text": ""}) is memory
    assert _attach_topic_state(memory, None) is memory
    assert _attach_topic_state(memory, "not-a-dict") is memory


def test_attach_topic_state_sets_text_when_enabled():
    from orchestrator.optimized_orchestrator import _attach_topic_state

    original = {"facts": []}
    out = _attach_topic_state(original, {"text": STATE_TEXT})
    assert out["topic_state_text"] == STATE_TEXT
    assert out["facts"] == []
    # 原 dict 不被原地修改（快照是只读数据）
    assert "topic_state_text" not in original


def test_attach_topic_state_does_not_break_string_memory():
    from orchestrator.optimized_orchestrator import _attach_topic_state

    assert _attach_topic_state("旧记忆字符串", {"text": STATE_TEXT}) == "旧记忆字符串"


def test_memory_slot_priority_includes_topic_state():
    """槽位顺序：relationship_facts 之后、reflections 之前（计划 §6.4）。"""
    from orchestrator.context_budget import MEMORY_SLOT_PRIORITY

    assert "topic_state_text" in MEMORY_SLOT_PRIORITY
    assert MEMORY_SLOT_PRIORITY.index("relationship_facts") < MEMORY_SLOT_PRIORITY.index(
        "topic_state_text"
    )
    assert MEMORY_SLOT_PRIORITY.index("topic_state_text") < MEMORY_SLOT_PRIORITY.index(
        "reflections"
    )


def test_budget_counts_topic_state_length():
    """topic_state_text 是记忆槽位：计入 memory 长度与 memory_chars_max 扣减。"""
    from orchestrator.context_budget import DEFAULT_BUDGET, apply_budget

    out = apply_budget(
        memory_context={"facts": ["用户喜欢猫"], "topic_state_text": STATE_TEXT},
        budget=DEFAULT_BUDGET,
    )
    assert out["memory_context"]["topic_state_text"] == STATE_TEXT
    assert out["lengths"]["memory"] >= len(STATE_TEXT)


def test_budget_clips_topic_state_with_facts():
    """facts 接近 memory_chars_max 时状态块让位，总 memory 长度不超过 cap。"""
    from orchestrator.context_budget import DEFAULT_BUDGET, apply_budget

    out = apply_budget(
        memory_context={"facts": ["记" * 2500], "topic_state_text": STATE_TEXT},
        budget=DEFAULT_BUDGET,
    )
    assert out["lengths"]["memory"] <= DEFAULT_BUDGET.memory_chars_max
    assert out["memory_context"]["topic_state_text"] == ""


class _CtxMemory:
    """`_prepare_context` 的最小记忆替身（只实现被调用的三个读方法）。"""

    def __init__(self, facts):
        self._facts = list(facts)

    def get_recent_context(self, n, session_id="", character_id=""):
        return ""

    def retrieve_context(self, query, session_id, top_k, character_id=""):
        return {"facts": list(self._facts)}

    def get_chat_context(self, session_id, character_id=""):
        return [], ""


def _make_prepare_orch(captured: dict, facts):
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    orch = OptimizedOrchestrator()
    orch._initialized = True
    orch.components = {
        "memory": _CtxMemory(facts),
        "rag": SimpleNamespace(retrieve=lambda query, character_id="": {"results": []}),
        "emotion": SimpleNamespace(analyze=lambda msg, ctx: None),
        "persona": SimpleNamespace(
            build_system_prompt=lambda **kw: captured.update(kw) or "sys"
        ),
    }
    return orch


@pytest.mark.asyncio
async def test_prepare_context_attaches_topic_state_before_budget(monkeypatch):
    """快照在 apply_budget **之前**并入：计入 memory_chars_max 与 memory 埋点。"""
    captured: dict = {}
    orch = _make_prepare_orch(captured, ["用户喜欢猫"])
    monkeypatch.setattr(
        ts,
        "get_topic_snapshot",
        lambda *a, **k: {
            "enabled": True,
            "text": STATE_TEXT,
            "active": [],
            "parked": [],
            "next_plan": None,
        },
    )
    ctx = await orch._prepare_context("我明天去面试", KEY_A, "default")
    assert captured["memory_context"]["topic_state_text"] == STATE_TEXT
    assert ctx["context_lengths"]["topic_state"] == len(STATE_TEXT)
    assert ctx["context_lengths"]["memory"] >= len(STATE_TEXT)
    assert ctx["context_lengths"]["memory"] <= 2500


@pytest.mark.asyncio
async def test_prepare_context_snapshot_timeout_degrades(monkeypatch):
    """快照超时只降级为空：不抛、不阻塞、不产生状态块。"""
    captured: dict = {}
    orch = _make_prepare_orch(captured, [])

    def _slow_snapshot(*_a, **_k):
        time.sleep(1.5)
        return {"enabled": True, "text": STATE_TEXT}

    monkeypatch.setattr(ts, "get_topic_snapshot", _slow_snapshot)
    from orchestrator.optimized_orchestrator import TOPIC_SNAPSHOT_TIMEOUT_SECONDS

    began = time.monotonic()
    ctx = await orch._prepare_context("我明天去面试", KEY_A, "default")
    elapsed = time.monotonic() - began
    # 必须锁住真实超时上限（0.2s），而不是「只要小于 1.2s」——后者把
    # 常量改成 1.0s 仍然绿（2026-10-28 复核 medium）。
    assert elapsed < TOPIC_SNAPSHOT_TIMEOUT_SECONDS + 0.3, (
        f"快照超时未生效，prepare 耗时 {elapsed:.2f}s，上限 {TOPIC_SNAPSHOT_TIMEOUT_SECONDS}s"
    )
    assert "topic_state_text" not in (captured["memory_context"] or {})
    assert "topic_state" not in ctx["context_lengths"]


# ── 6. 后台更新与稀疏 judge：绝不增加首字延迟 ──────────────────────────


class _FakeMemory:
    def __init__(self) -> None:
        self.after_chat_calls: list[dict] = []

    def after_chat(self, **kwargs) -> None:
        self.after_chat_calls.append(kwargs)


def test_background_topic_update_not_synchronously_awaited(monkeypatch):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    started = threading.Event()
    release = threading.Event()

    def _slow_update(**_kwargs):
        started.set()
        release.wait(5.0)
        return {"enabled": True, "action": "continue", "should_judge": False, "snapshot": {}}

    monkeypatch.setattr(ts, "update_after_turn", _slow_update)

    orch = OptimizedOrchestrator()
    memory = _FakeMemory()
    orch.components["memory"] = memory
    try:
        began = time.monotonic()
        result = orch._after_process("你好", "在的", None, "", "default", turn_id="turn-1")
        elapsed = time.monotonic() - began
        assert elapsed < 0.5, f"_after_process 被状态更新阻塞了 {elapsed:.2f}s"
        assert result == ""
        assert started.wait(2.0), "后台状态更新没有启动"
        assert release.is_set() is False
    finally:
        release.set()
        orch._get_background_executor().shutdown(wait=True)
        orch.shutdown()


def test_sparse_judge_uses_dedicated_pool_and_does_not_block_after_chat(monkeypatch):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    judge_started = threading.Event()
    judge_release = threading.Event()
    after_chat_done = threading.Event()

    monkeypatch.setattr(
        ts,
        "update_after_turn",
        lambda **_kwargs: {
            "enabled": True,
            "action": "switch",
            "should_judge": True,
            "snapshot": {},
        },
    )

    def _slow_judge(**_kwargs):
        judge_started.set()
        judge_release.wait(5.0)
        return None

    monkeypatch.setattr(ts, "run_llm_judge", _slow_judge)

    class _Memory(_FakeMemory):
        def after_chat(self, **kwargs) -> None:
            super().after_chat(**kwargs)
            after_chat_done.set()

    orch = OptimizedOrchestrator()
    orch.components["memory"] = _Memory()
    try:
        began = time.monotonic()
        orch._after_process("你好", "在的", None, "", "default", turn_id="turn-2")
        assert time.monotonic() - began < 0.5
        assert after_chat_done.wait(2.0), "after_chat 应先于 judge 完成"
        assert judge_started.wait(2.0), "稀疏 judge 没有启动"
        # judge 卡住时，after_chat 池仍空闲（独立池，不互相堵）
        probe = threading.Event()
        orch._get_background_executor().submit(probe.set)
        assert probe.wait(2.0), "judge 阻塞了 after_chat 串行池"
    finally:
        judge_release.set()
        orch._get_topic_judge_executor().shutdown(wait=True)
        orch._get_background_executor().shutdown(wait=True)
        orch.shutdown()


# ── 7. judge 的 LLM 归属：显式 per-user 配置错误不回落平台凭证 ──────────


class _PipelineStub:
    def __init__(self, resolver):
        self._session_llm_resolver = resolver


class _ResolvedMemory(_FakeMemory):
    def __init__(self, resolver):
        super().__init__()
        self._pipeline = _PipelineStub(resolver)


def _drive_judge(monkeypatch, memory, platform_llm=None) -> dict:
    """跑一轮 `_after_process` 并捕获 `run_llm_judge` 收到的 llm。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    captured: dict = {}
    called = threading.Event()

    def _capture(**kwargs):
        captured.update(kwargs)
        called.set()

    monkeypatch.setattr(
        ts,
        "update_after_turn",
        lambda **_kwargs: {
            "enabled": True,
            "action": "switch",
            "should_judge": True,
            "snapshot": {},
        },
    )
    monkeypatch.setattr(ts, "run_llm_judge", _capture)

    orch = OptimizedOrchestrator()
    orch.components["memory"] = memory
    if platform_llm is not None:
        orch.components["llm"] = platform_llm
    try:
        orch._after_process("你好", "在的", None, "", "default", turn_id="judge-turn")
        assert called.wait(2.0), "judge 未启动"
        return dict(captured)
    finally:
        orch._get_topic_judge_executor().shutdown(wait=True)
        orch._get_background_executor().shutdown(wait=True)
        orch.shutdown()


def test_judge_resolver_none_does_not_use_platform_llm(monkeypatch):
    """BYOK 未配置/返回 None → 本轮不判，绝不借用平台凭证。"""
    platform = object()
    captured = _drive_judge(
        monkeypatch, _ResolvedMemory(lambda _sid: None), platform_llm=platform
    )
    assert captured["llm"] is None


def test_judge_resolver_error_does_not_use_platform_llm(monkeypatch):
    """BYOK 解析抛错（如 403/DB 故障）→ 本轮不判，绝不借用平台凭证。"""

    def _raise(_sid):
        raise RuntimeError("byok denied")

    platform = object()
    captured = _drive_judge(
        monkeypatch, _ResolvedMemory(_raise), platform_llm=platform
    )
    assert captured["llm"] is None


def test_judge_without_resolver_uses_platform_llm(monkeypatch):
    """没有 per-user 解析器（非 BYOK 部署）时才允许平台 LLM。"""
    platform = object()
    captured = _drive_judge(monkeypatch, _FakeMemory(), platform_llm=platform)
    assert captured["llm"] is platform
