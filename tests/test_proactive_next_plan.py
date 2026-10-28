"""主动线 next_plan 全周期测试（2026-10-27 话题状态机 Q3/Q4）。

覆盖：决策 JSON 新字段 → skip 存计划 → 模拟重启 → 到点注入 → 发送成功消费
→ 投递失败保留 → window_expired 丢弃 → 用户/角色隔离。

调度器级用例直接驱动 ``ProactiveScheduler._llm_proactive_one_user``（真实
装配路径），外部依赖（LLM/记忆/画像/事件账本）全部以 fake 注入；topic_state
的 DB/配置路径由本文件 fixture + 全局 sandbox 双重隔离，不触碰宿主真实库。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from proactive import post_draft_judge
from proactive.llm_proactive import build_proactive_context, parse_decision
from utils import topic_state as ts

FIXED = datetime(2026, 10, 28, 10, 0, 0)
KEY = "4:web:deadbeef"
CHAR = "default"


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    cfg = tmp_path / "scheduler_config.json"
    cfg.write_text(
        json.dumps(
            {
                "topic_state": {"enabled": True},
                "llm_proactive": {"judge_enabled": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ts, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(ts, "_DB_PATH", tmp_path / "topic_state.db")
    monkeypatch.setattr(post_draft_judge, "_CONFIG_PATH", cfg)
    return ts, cfg


class _FakeEng:
    def __init__(self) -> None:
        self._last_delivery_time = None
        self._unanswered_streak = 0
        self._last_user_message = ""
        self._knowledge_character_id = CHAR
        self.urgency = type("U", (), {"total": 2.5})()
        self.tick_calls: list[bool] = []
        self.commit_calls: list[dict] = []

    def _hours_since_last_chat(self) -> float:
        return 90.0

    def tick(self, hours, dry_run=False):
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


def _plan_row(key: str = KEY, character_id: str = CHAR) -> dict | None:
    conn = sqlite3.connect(str(ts._DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM topic_plan WHERE session_key=? AND character_id=?",
            (key, character_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _make_sched(monkeypatch, *, now: datetime = FIXED, decision: dict | None = None, deliver: bool = True):
    import proactive.ase_engine as ase
    import proactive.llm_proactive as lp
    import proactive.proactive_context as pctx
    import proactive.proactive_hooks as hooks_mod
    import shisi.agent_plane.runtime as rt
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(ase, "_local_now", lambda: now)
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda cid: "")
    monkeypatch.setattr(lp, "recent_messages_for_dedup", lambda hub, key, eng: [])
    monkeypatch.setattr(lp, "is_duplicate_proactive", lambda *a, **k: False)
    monkeypatch.setattr(pctx, "load_full_persona_hint", lambda cid, uk=None: "")
    monkeypatch.setattr(
        hooks_mod,
        "load_live_hooks",
        lambda key, memory: type("H", (), {"section": "", "keywords": ()})(),
    )
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: None)

    eng = _FakeEng()
    hub = _FakeHub(eng)

    sched = ProactiveScheduler(ase_engine=None)
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._paused = False
    sched._is_quiet_hours = lambda: False
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
    calls: list[dict] = []

    def _decide(llm, ctx):
        calls.append({"ctx": ctx})
        return dict(decision)

    monkeypatch.setattr(lp, "decide_proactive", _decide)
    return sched, hub, captured, calls


# ── parse_decision ───────────────────────────────────────────────


def test_parse_decision_extracts_next_fields():
    raw = json.dumps(
        {
            "should_contact": False,
            "wait_minutes": 45,
            "message": "",
            "reason": "等面试结果",
            "next_topic": "面" * 50,
            "next_intent": "问" * 200,
        }
    )
    d = parse_decision(raw)
    assert d["next_topic"] == "面" * 40
    assert d["next_intent"] == "问" * 120
    assert d["wait_minutes"] == 45


def test_parse_decision_defaults_empty():
    for raw in ("", "not a json", "{}", '{"should_contact": false, "reason": "x"}'):
        d = parse_decision(raw)
        assert "next_topic" in d and "next_intent" in d
        assert d["next_topic"] == "" and d["next_intent"] == ""


# ── topic_plan 数据层 ─────────────────────────────────────────────


def test_save_and_take_plan_roundtrip(plan_env):
    ts.save_next_plan(KEY, CHAR, "面试", "问问结果", wait_minutes=30, planned_at=FIXED)
    plan = ts.take_next_plan(KEY, CHAR, now=FIXED + timedelta(minutes=31))
    assert plan is not None
    assert plan["topic"] == "面试"
    assert plan["intent"] == "问问结果"
    assert plan["next_at"] == "2026-10-28 10:30:00"


def test_take_does_not_consume_by_default(plan_env):
    ts.save_next_plan(KEY, CHAR, "面试", "问问结果", wait_minutes=0, planned_at=FIXED)
    first = ts.take_next_plan(KEY, CHAR, now=FIXED)
    second = ts.take_next_plan(KEY, CHAR, now=FIXED)
    assert first is not None and second is not None
    assert first["topic"] == second["topic"] == "面试"


def test_take_consume_marks_consumed(plan_env):
    ts.save_next_plan(KEY, CHAR, "面试", "问问结果", wait_minutes=0, planned_at=FIXED)
    assert ts.take_next_plan(KEY, CHAR, now=FIXED, consume=True) is not None
    assert ts.take_next_plan(KEY, CHAR, now=FIXED) is None


def test_plan_survives_new_connection(plan_env):
    ts.save_next_plan(KEY, CHAR, "房租", "问房东答复", wait_minutes=0, planned_at=FIXED)
    # 新建独立连接读取（等价于进程重启后打开同一 DB）
    conn = sqlite3.connect(str(ts._DB_PATH))
    try:
        row = conn.execute(
            "SELECT topic, intent FROM topic_plan WHERE session_key=? AND character_id=?",
            (KEY, CHAR),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("房租", "问房东答复")
    assert ts.take_next_plan(KEY, CHAR, now=FIXED)["intent"] == "问房东答复"


def test_plan_expires(plan_env):
    ts.save_next_plan(KEY, CHAR, "面试", "问问结果", wait_minutes=0, planned_at=FIXED)
    assert ts.take_next_plan(KEY, CHAR, now=FIXED + timedelta(hours=25)) is None


def test_take_not_before_next_at(plan_env):
    ts.save_next_plan(KEY, CHAR, "面试", "问问结果", wait_minutes=45, planned_at=FIXED)
    assert ts.take_next_plan(KEY, CHAR, now=FIXED + timedelta(minutes=44)) is None
    assert ts.take_next_plan(KEY, CHAR, now=FIXED + timedelta(minutes=45)) is not None


def test_consume_then_save_then_take(plan_env):
    ts.save_next_plan(KEY, CHAR, "旧计划", "旧意图", wait_minutes=0, planned_at=FIXED)
    assert ts.take_next_plan(KEY, CHAR, now=FIXED, consume=True) is not None
    ts.save_next_plan(KEY, CHAR, "新计划", "新意图", wait_minutes=0, planned_at=FIXED)
    plan = ts.take_next_plan(KEY, CHAR, now=FIXED)
    assert plan is not None
    assert plan["topic"] == "新计划"
    assert plan["consumed_at"] is None


def test_user_isolation(plan_env):
    ts.save_next_plan("4:web:aaaa1111", CHAR, "A 的事", "A 意图", wait_minutes=0, planned_at=FIXED)
    ts.save_next_plan("5:web:bbbb2222", CHAR, "B 的事", "B 意图", wait_minutes=0, planned_at=FIXED)
    a = ts.take_next_plan("4:web:aaaa1111", CHAR, now=FIXED)
    b = ts.take_next_plan("5:web:bbbb2222", CHAR, now=FIXED)
    assert a["topic"] == "A 的事"
    assert b["topic"] == "B 的事"
    assert a["topic"] != b["topic"]


def test_character_isolation(plan_env):
    ts.save_next_plan(KEY, "default", "默认角色计划", "x", wait_minutes=0, planned_at=FIXED)
    ts.save_next_plan(KEY, "other", "另一个角色计划", "y", wait_minutes=0, planned_at=FIXED)
    assert ts.take_next_plan(KEY, "default", now=FIXED)["topic"] == "默认角色计划"
    assert ts.take_next_plan(KEY, "other", now=FIXED)["topic"] == "另一个角色计划"


# ── 上下文渲染 ───────────────────────────────────────────────────


def test_context_includes_next_plan():
    prompt = build_proactive_context(
        session_key=KEY,
        local_time="2026-10-28 10:46",
        next_plan={"topic": "面试", "intent": "问问二面结果"},
    )
    assert "【上次决定的下一步】" in prompt
    assert "面试" in prompt and "问问二面结果" in prompt


def test_context_empty_hooks_suppressed_when_plan():
    prompt = build_proactive_context(
        session_key=KEY,
        local_time="2026-10-28 10:46",
        hooks_text="",
        next_plan={"topic": "面试", "intent": "问问二面结果"},
    )
    assert "（空——本轮没有任何具体由头" not in prompt


# ── 调度器级全周期 ───────────────────────────────────────────────


def test_skip_decision_saves_plan(plan_env, monkeypatch):
    sched, hub, _captured, _calls = _make_sched(monkeypatch)
    sched._llm_proactive_one_user(hub, KEY)
    row = _plan_row()
    assert row is not None
    assert row["topic"] == "面试"
    assert row["intent"] == "问问二面结果"
    assert row["next_at"] == "2026-10-28 10:45:00"
    assert row["consumed_at"] is None


def test_skip_with_empty_next_consumes_injected_plan(plan_env, monkeypatch):
    """复核修复：模型写空 = 放弃本轮已注入的旧计划，不能留到下一 tick 再注入。"""
    now = datetime.now().replace(microsecond=0)
    ts.save_next_plan(KEY, CHAR, "旧计划", "旧意图", wait_minutes=0, planned_at=now - timedelta(minutes=5))
    decision = {
        "should_contact": False,
        "message": "",
        "reason": "不想聊",
        "wait_minutes": 30,
        "next_topic": "",
        "next_intent": "",
    }
    sched, hub, captured, _calls = _make_sched(monkeypatch, now=now, decision=decision)
    sched._llm_proactive_one_user(hub, KEY)
    assert captured[-1]["next_plan"] is not None  # 旧计划确实被注入过
    row = _plan_row()
    assert row is not None and row["consumed_at"] is not None


def test_delivery_success_consumes_plan(plan_env, monkeypatch):
    now = datetime.now().replace(microsecond=0)
    ts.save_next_plan(KEY, CHAR, "旧计划", "旧意图", wait_minutes=0, planned_at=now - timedelta(minutes=10))
    decision = {
        "should_contact": True,
        "message": "面试结果应该出来了吧，我一直惦记着这事",
        "reason": "承接旧计划",
        "wait_minutes": None,
        "next_topic": "面试结果",
        "next_intent": "问问二面结果",
    }
    consume_flags: list[bool] = []
    _orig_take = ts.take_next_plan

    def _take_spy(*args, **kwargs):
        consume_flags.append(bool(kwargs.get("consume")))
        return _orig_take(*args, **kwargs)

    monkeypatch.setattr(ts, "take_next_plan", _take_spy)
    sched, hub, _captured, _calls = _make_sched(monkeypatch, now=now, decision=decision, deliver=True)
    sched._llm_proactive_one_user(hub, KEY)
    row = _plan_row()
    assert True in consume_flags  # 成功分支确实消费旧计划
    assert row is not None
    assert row["consumed_at"] is None  # 新计划可读（upsert 复位）
    assert row["topic"] == "面试结果"
    assert row["intent"] == "问问二面结果"
    assert row["next_at"] == now.strftime("%Y-%m-%d %H:%M:%S")


def test_delivery_failure_keeps_plan(plan_env, monkeypatch):
    now = datetime.now().replace(microsecond=0)
    ts.save_next_plan(KEY, CHAR, "旧计划", "旧意图", wait_minutes=0, planned_at=now - timedelta(minutes=10))
    decision = {
        "should_contact": True,
        "message": "面试结果应该出来了吧，我一直惦记着这事",
        "reason": "承接旧计划",
        "wait_minutes": None,
        "next_topic": "面试结果",
        "next_intent": "问问二面结果",
    }
    sched, hub, _captured, _calls = _make_sched(monkeypatch, now=now, decision=decision, deliver=False)
    sched._llm_proactive_one_user(hub, KEY)
    row = _plan_row()
    assert row is not None
    assert row["consumed_at"] is None
    assert row["topic"] == "旧计划"
    assert row["intent"] == "旧意图"


def test_window_expired_drops_plan(plan_env, monkeypatch):
    now = datetime.now().replace(microsecond=0)
    ts.save_next_plan(KEY, CHAR, "旧计划", "旧意图", wait_minutes=0, planned_at=now - timedelta(minutes=5))
    sched, hub, _captured, calls = _make_sched(monkeypatch)
    sched._llm_proactive_next_ok[KEY] = time.time() - 3600
    sched._proactive_window_config = lambda: (True, 30)
    sched._llm_proactive_one_user(hub, KEY)
    row = _plan_row()
    assert row is not None and row["consumed_at"] is not None
    assert calls == []  # 过期不补：不烧决策调用


def test_scheduler_restart_reads_plan_from_db(plan_env, monkeypatch):
    # 第一次：skip 存计划（10:00，wait 45）
    sched_a, hub_a, _c1, _calls1 = _make_sched(monkeypatch, now=FIXED)
    sched_a._llm_proactive_one_user(hub_a, KEY)
    assert _plan_row()["next_at"] == "2026-10-28 10:45:00"

    # 模拟重启：新实例、空内存账本；10:46 到点后决策上下文必须看到 next_plan
    monkeypatch.setattr("proactive.ase_engine._local_now", lambda: FIXED + timedelta(minutes=46))
    sched_b, hub_b, captured, _calls2 = _make_sched(
        monkeypatch,
        now=FIXED + timedelta(minutes=46),
        decision={"should_contact": False, "message": "", "reason": "还早", "wait_minutes": 30,
                  "next_topic": "", "next_intent": ""},
    )
    sched_b._llm_proactive_one_user(hub_b, KEY)
    assert captured, "决策上下文未构造"
    plan = captured[-1]["next_plan"]
    assert plan is not None
    assert plan["topic"] == "面试"
    assert plan["intent"] == "问问二面结果"
    assert plan["next_at"] == "2026-10-28 10:45:00"


def test_plan_not_injected_before_due(plan_env, monkeypatch):
    ts.save_next_plan(KEY, CHAR, "面试", "问问二面结果", wait_minutes=45, planned_at=FIXED)
    sched, hub, captured, _calls = _make_sched(monkeypatch, now=FIXED + timedelta(minutes=20))
    sched._llm_proactive_one_user(hub, KEY)
    assert captured[-1]["next_plan"] is None


def test_plan_not_injected_for_other_user(plan_env, monkeypatch):
    """用户隔离：A 的计划不会出现在 B 的决策上下文。"""
    ts.save_next_plan("5:web:bbbb2222", CHAR, "B 的事", "B 意图", wait_minutes=0, planned_at=FIXED)
    sched, hub, captured, _calls = _make_sched(monkeypatch, now=FIXED + timedelta(minutes=1))
    sched._llm_proactive_one_user(hub, KEY)
    assert captured[-1]["next_plan"] is None


def test_deliver_resolves_session_judge_llm(monkeypatch):
    """Q3 第三闸修复：provider 为空时 _deliver 必须用 session resolver 的 LLM 判。"""
    from proactive.post_draft_judge import JudgeVerdict
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._llm_provider = None
    sched._session_llm_resolver = lambda key: "SESSION_LLM"
    sched._judge_recent = {}
    sched._judge_topic = {"": "面试（8.0 小时前）"}
    sched._send_targeted = lambda message, session_key=None: True
    sched._record_outbound = lambda *a, **k: None
    sched._run_blocking = lambda fn: fn()
    sched.get_quiet_hours = lambda: None
    seen: dict = {}

    def _capture(message, **kwargs):
        seen.update(kwargs)
        return JudgeVerdict(True, "ok")

    monkeypatch.setattr("proactive.post_draft_judge.judge_proactive_message", _capture)
    assert sched._deliver("面试结果出来了吗", session_key="") is True
    assert seen["llm"] == "SESSION_LLM"
    assert seen["topic_context"] == "面试（8.0 小时前）"


def test_deliver_fail_closed_when_resolver_returns_none(plan_env):
    """resolver 也解析不到 judge → parked 复提必须 drop（不能 not_configured 放行）。"""
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._llm_provider = None
    sched._session_llm_resolver = lambda key: None
    sched._judge_recent = {}
    sched._judge_topic = {"": "面试（8.0 小时前）"}
    sent: list[str] = []
    sched._send_targeted = lambda message, session_key=None: sent.append(message) or True
    sched._record_outbound = lambda *a, **k: None
    sched._run_blocking = lambda fn: fn()
    sched.get_quiet_hours = lambda: None
    assert sched._deliver("面试结果出来了吗", session_key="") is False
    assert sent == []
