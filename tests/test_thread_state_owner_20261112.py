"""原子 C1：ThreadState 成为话题计划读写唯一入口（2026-11-12）。

契约：
- ``proactive/scheduler.py`` 的 4 处 take / 2 处 save / 1 处 mark 全部经
  ``speaker.ThreadState``，归属键 ``(session_key, character_id)`` 显式传；
- ``take_plan(consume=False)`` 是纯读；``consume=True`` 只发生在投递成功
  （或 window_expired 丢弃陈旧计划）；
- ``save_plan`` 保留单槽覆盖语义，``planned_at`` 必须是本拍 ``now``；
- 失败降级结果不变（ThreadState 内部 fail-open，返回 None/False）。

本文件只做两类钉死：
1. 运行时：真实驱动 ``ProactiveScheduler._llm_proactive_one_user``，用
   ``speaker.ThreadState`` 间谍类 + ``utils.topic_state`` 记录器，断言每次
   计划读写都发生在 ThreadState 方法栈内且参数完整；
2. 静态：源码不得再出现 ``_topic_state.take_next_plan`` /
   ``save_next_plan`` / ``mark_topic_recalled``，且所有 plan 方法调用的
   接收者必须是 ThreadState。
"""

from __future__ import annotations

import ast
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from utils import topic_state as ts

FIXED = datetime(2026, 11, 12, 10, 0, 0)
KEY = "4:web:c1c1c1c1"
CHAR = "card_c1"

SCHEDULER_PATH = Path(__file__).resolve().parents[1] / "proactive" / "scheduler.py"


# ---------------------------------------------------------------------------
# 运行时脚手架：真实 _llm_proactive_one_user + 全替身 + ThreadState 间谍
# ---------------------------------------------------------------------------


class _FakeEng:
    def __init__(self) -> None:
        self._last_delivery_time = None
        self._unanswered_streak = 0
        self._last_user_message = ""
        self._knowledge_character_id = CHAR
        self.urgency = type("U", (), {"total": 0.0})()
        self.tick_calls: list[bool] = []
        self.commit_calls: list[dict] = []

    def _hours_since_last_chat(self) -> float:
        return 5.0

    def tick(self, hours, dry_run=False):  # noqa: ANN001
        self.tick_calls.append(dry_run)

    def commit_sent(self, result: dict) -> None:
        self.commit_calls.append(result)


class _FakeHub:
    def __init__(self, eng: _FakeEng) -> None:
        self._eng = eng

    def get(self, key: str):
        return self._eng if key == KEY else None

    def known_user_keys(self):
        return [KEY]


def _make_sched(
    monkeypatch,
    *,
    now: datetime = FIXED,
    decision: dict | None = None,
    deliver: bool = True,
    plan: dict | None = None,
    recall_items: list[dict] | None = None,
):
    """装配真实 scheduler，记录 plan API 调用与 ThreadState 实例。"""
    import proactive.ase_engine as ase
    import proactive.llm_proactive as lp
    import proactive.proactive_context as pctx
    import proactive.proactive_hooks as hooks_mod
    import proactive.topic_recall as topic_recall_mod
    import shisi.agent_plane.runtime as rt
    import speaker as speaker_pkg
    from proactive.scheduler import ProactiveScheduler
    from speaker.thread_state import ThreadState as RealThreadState

    calls: list[dict] = []
    instances: list[tuple[str, str]] = []

    class _SpyThreadState(RealThreadState):
        """记录构造参数，并标记「当前是否处在 ThreadState 方法栈内」。"""

        depth = 0

        def __init__(self, session_key: str, character_id: str = ""):
            super().__init__(session_key, character_id)
            instances.append((self.session_key, self.character_id))

        def take_plan(self, *args, **kwargs):  # noqa: ANN002, ANN003
            type(self).depth += 1
            try:
                return super().take_plan(*args, **kwargs)
            finally:
                type(self).depth -= 1

        def save_plan(self, *args, **kwargs):  # noqa: ANN002, ANN003
            type(self).depth += 1
            try:
                return super().save_plan(*args, **kwargs)
            finally:
                type(self).depth -= 1

        def mark_recalled(self, *args, **kwargs):  # noqa: ANN002, ANN003
            type(self).depth += 1
            try:
                return super().mark_recalled(*args, **kwargs)
            finally:
                type(self).depth -= 1

    # 只有经 ThreadState 才会触达下面三个记录器（静态守卫另行钉死无直调）。
    def _fake_take(session_key, character_id="default", *, now=None, consume=False):
        assert _SpyThreadState.depth > 0, "take_next_plan 必须经 ThreadState.take_plan"
        calls.append(
            {
                "api": "take",
                "session_key": session_key,
                "character_id": character_id,
                "now": now,
                "consume": consume,
            }
        )
        return dict(plan) if (plan is not None and not consume) else None

    def _fake_save(
        session_key, character_id, topic, intent, *, wait_minutes=0, planned_at=None
    ):
        assert _SpyThreadState.depth > 0, "save_next_plan 必须经 ThreadState.save_plan"
        calls.append(
            {
                "api": "save",
                "session_key": session_key,
                "character_id": character_id,
                "topic": topic,
                "intent": intent,
                "wait_minutes": wait_minutes,
                "planned_at": planned_at,
            }
        )
        return {"topic": topic, "intent": intent}

    def _fake_mark(session_key, character_id="default", topic_id="", *, now=None):
        assert _SpyThreadState.depth > 0, "mark_topic_recalled 必须经 ThreadState.mark_recalled"
        calls.append(
            {
                "api": "mark",
                "session_key": session_key,
                "character_id": character_id,
                "topic_id": topic_id,
                "now": now,
            }
        )
        return True

    monkeypatch.setattr(speaker_pkg, "ThreadState", _SpyThreadState)
    monkeypatch.setattr(ts, "take_next_plan", _fake_take)
    monkeypatch.setattr(ts, "save_next_plan", _fake_save)
    monkeypatch.setattr(ts, "mark_topic_recalled", _fake_mark)

    monkeypatch.setattr(ase, "_local_now", lambda: now)
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda cid: "")
    monkeypatch.setattr(lp, "recent_messages_for_dedup", lambda hub, key, eng: [])
    monkeypatch.setattr(lp, "is_duplicate_proactive", lambda *a, **k: False)
    monkeypatch.setattr(lp, "should_skip_proactive_decision", lambda **kw: (False, ""))
    monkeypatch.setattr(pctx, "load_full_persona_hint", lambda cid, uk=None: "")
    monkeypatch.setattr(
        hooks_mod,
        "load_live_hooks",
        lambda key, memory: type("H", (), {"section": "", "keywords": ()})(),
    )
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: None)
    monkeypatch.setattr(
        topic_recall_mod,
        "build_topic_recall",
        lambda *a, **k: SimpleNamespace(
            items=list(recall_items or []), judge_context=""
        ),
    )

    eng = _FakeEng()
    hub = _FakeHub(eng)

    sched = ProactiveScheduler(ase_engine=None)
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._paused = False
    sched._is_quiet_hours = lambda: False
    sched._is_quiet_hours_for = lambda cfg: False
    sched._primary_model_ok = lambda: True
    sched.get_quiet_hours = lambda: None
    sched._resolve_character_id = lambda sk: CHAR
    sched._resolve_proactive_llm = lambda eng=None: object()
    sched._session_llm_resolver = lambda key: object()
    sched._resolve_memory = lambda: None
    sched._persist_throttle_ledger = lambda: None
    sched._proactive_window_config = lambda: (False, 0)
    sched._deliver = lambda message, session_key=None, character_id=None: deliver

    captured: list[dict] = []

    def _capture_ctx(**kwargs):
        captured.append(kwargs)
        return "CTX"

    monkeypatch.setattr(lp, "build_proactive_context", _capture_ctx)

    if decision is None:
        decision = {
            "should_contact": False,
            "message": "",
            "reason": "再观察一下",
            "wait_minutes": 45,
            "next_topic": "面试",
            "next_intent": "问问二面结果",
        }

    def _decide(llm, ctx, **kwargs):  # noqa: ANN001
        return dict(decision)

    monkeypatch.setattr(lp, "decide_proactive", _decide)
    return sched, hub, calls, instances, captured


def _take_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["api"] == "take"]


def _save_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["api"] == "save"]


def _mark_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["api"] == "mark"]


# ---------------------------------------------------------------------------
# 运行时：决策前读 + skip 保存
# ---------------------------------------------------------------------------


def test_skip_save_goes_through_thread_state_with_full_params(monkeypatch):
    sched, hub, calls, instances, _captured = _make_sched(monkeypatch)
    sched._llm_proactive_one_user(hub, KEY)

    takes = _take_calls(calls)
    assert len(takes) == 1, "决策前只允许一次纯读"
    assert takes[0] == {
        "api": "take",
        "session_key": KEY,
        "character_id": CHAR,
        "now": FIXED,
        "consume": False,
    }

    saves = _save_calls(calls)
    assert len(saves) == 1
    assert saves[0] == {
        "api": "save",
        "session_key": KEY,
        "character_id": CHAR,
        "topic": "面试",
        "intent": "问问二面结果",
        "wait_minutes": 45,
        "planned_at": FIXED,
    }
    # 两次调用各构造一个 ThreadState，归属键完整。
    assert instances == [(KEY, CHAR), (KEY, CHAR)]


def test_skip_with_empty_next_consumes_injected_plan(monkeypatch):
    decision = {
        "should_contact": False,
        "message": "",
        "reason": "不想聊",
        "wait_minutes": 30,
        "next_topic": "",
        "next_intent": "",
    }
    sched, hub, calls, instances, captured = _make_sched(
        monkeypatch, decision=decision, plan={"topic": "旧计划", "intent": "旧意图"}
    )
    sched._llm_proactive_one_user(hub, KEY)

    assert captured[-1]["next_plan"] == {"topic": "旧计划", "intent": "旧意图"}
    takes = _take_calls(calls)
    assert [(t["consume"], t["now"]) for t in takes] == [(False, FIXED), (True, FIXED)]
    # 纯读 + 放弃消费；不得保存新计划。
    assert _save_calls(calls) == []
    assert instances == [(KEY, CHAR), (KEY, CHAR)]


def test_plan_read_failure_is_fail_open(monkeypatch):
    """ThreadState 内部 catch 后返回 None：决策与后续保存不得被阻断。"""
    sched, hub, calls, _instances, captured = _make_sched(monkeypatch)

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("db down")

    monkeypatch.setattr(ts, "take_next_plan", _boom)
    sched._llm_proactive_one_user(hub, KEY)

    assert captured, "计划读取失败不得阻断决策"
    assert len(_save_calls(calls)) == 1, "读取失败后仍应保存本轮 next_plan"


# ---------------------------------------------------------------------------
# 运行时：投递成功才消费 + 重存 + 复提记账
# ---------------------------------------------------------------------------


def test_delivery_success_consume_save_and_recall_go_through_thread_state(monkeypatch):
    decision = {
        "should_contact": True,
        "message": "面试结果应该出来了吧，我一直惦记着这事",
        "reason": "承接旧计划",
        "wait_minutes": None,
        "next_topic": "面试结果",
        "next_intent": "问问二面结果",
    }
    sched, hub, calls, instances, _captured = _make_sched(
        monkeypatch,
        decision=decision,
        plan={"topic": "旧计划", "intent": "旧意图"},
        recall_items=[{"topic_id": "tp-1"}, {"topic_id": "tp-2"}],
    )
    sched._llm_proactive_one_user(hub, KEY)

    takes = _take_calls(calls)
    # 第一拍纯读；投递成功后消费。
    assert [(t["consume"], t["now"]) for t in takes] == [(False, FIXED), (True, FIXED)]

    saves = _save_calls(calls)
    assert len(saves) == 1
    assert saves[0]["session_key"] == KEY
    assert saves[0]["character_id"] == CHAR
    assert saves[0]["topic"] == "面试结果"
    assert saves[0]["intent"] == "问问二面结果"
    assert saves[0]["wait_minutes"] == 0
    assert saves[0]["planned_at"] == FIXED

    marks = _mark_calls(calls)
    assert [(m["topic_id"], m["now"]) for m in marks] == [
        ("tp-1", FIXED),
        ("tp-2", FIXED),
    ]
    assert all(m["session_key"] == KEY and m["character_id"] == CHAR for m in marks)
    # 预读 1 + 成功消费/保存共用 1 + 记账 1。
    assert instances == [(KEY, CHAR)] * 3


def test_delivery_failure_does_not_consume_or_save(monkeypatch):
    decision = {
        "should_contact": True,
        "message": "面试结果应该出来了吧，我一直惦记着这事",
        "reason": "承接旧计划",
        "wait_minutes": None,
        "next_topic": "面试结果",
        "next_intent": "问问二面结果",
    }
    sched, hub, calls, _instances, _captured = _make_sched(
        monkeypatch,
        decision=decision,
        deliver=False,
        plan={"topic": "旧计划", "intent": "旧意图"},
        recall_items=[{"topic_id": "tp-1"}],
    )
    sched._llm_proactive_one_user(hub, KEY)

    assert [(t["consume"], t["now"]) for t in _take_calls(calls)] == [(False, FIXED)]
    assert _save_calls(calls) == []
    assert _mark_calls(calls) == []


# ---------------------------------------------------------------------------
# 运行时：window_expired 丢弃陈旧计划
# ---------------------------------------------------------------------------


def test_window_expired_consumes_plan_through_thread_state(monkeypatch):
    sched, hub, calls, instances, captured = _make_sched(monkeypatch)
    sched._llm_proactive_next_ok[KEY] = time.time() - 3600
    sched._proactive_window_config = lambda: (True, 30)
    sched._llm_proactive_one_user(hub, KEY)

    assert captured == [], "过期不补：不得烧决策调用"
    takes = _take_calls(calls)
    assert len(takes) == 1
    assert takes[0]["session_key"] == KEY
    assert takes[0]["character_id"] == CHAR
    assert takes[0]["consume"] is True
    # 该路径沿用原实现：不传 now，由 topic_state 按当前时刻判定到期。
    assert takes[0]["now"] is None
    assert _save_calls(calls) == []
    assert instances == [(KEY, CHAR)]


# ---------------------------------------------------------------------------
# 静态守卫
# ---------------------------------------------------------------------------


def _scheduler_source() -> str:
    return SCHEDULER_PATH.read_text(encoding="utf-8")


def test_scheduler_has_no_direct_legacy_plan_api_calls():
    src = _scheduler_source()
    for forbidden in (
        "_topic_state.take_next_plan",
        "_topic_state.save_next_plan",
        "_topic_state.mark_topic_recalled",
        "topic_state.take_next_plan",
        "topic_state.save_next_plan",
        "topic_state.mark_topic_recalled",
    ):
        assert forbidden not in src, f"scheduler 仍在直调 {forbidden}"
    assert "from speaker import ThreadState" in src


def test_all_plan_method_calls_receive_thread_state():
    """AST 级：take_plan/save_plan/mark_recalled 的接收者必须是 ThreadState。"""
    tree = ast.parse(_scheduler_source())
    plan_methods = {"take_plan", "save_plan", "mark_recalled"}
    found = 0

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        bound_names: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == "ThreadState":
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bound_names.add(target.id)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in plan_methods:
                continue
            found += 1
            owner = node.func.value
            direct = (
                isinstance(owner, ast.Call)
                and isinstance(owner.func, ast.Name)
                and owner.func.id == "ThreadState"
            )
            via_bound = isinstance(owner, ast.Name) and owner.id in bound_names
            assert direct or via_bound, (
                f"{SCHEDULER_PATH}:{node.lineno} {node.func.attr} 的接收者不是 ThreadState"
            )

    # 4 take + 2 save + 1 mark（原子 C1 的清点）。
    assert found >= 7, f"只找到 {found} 个 plan 方法调用，少于契约清点"
