"""话题级追问限红测（2026-11-21 主动线治理批）。

生产审计：同话题追问只有账号级 ``MAX_UNANSWERED_PROACTIVE=4``，没有 topic 级
状态；生产已出现同一话题 3 连问（chat_history 5860-5862）与 2 连问
（5798/5801）。本批契约：

- ``topic_state`` 增 ``followup_count`` / ``last_followup_at``；
- 未回复同话题最多 1 次追问；用户任何一轮发言即清零（回复行为打破未回复态）；
- 判定点在 ``_llm_proactive_one_user`` 取到到期计划之后、调用决策 LLM 之前：
  超限则消费计划并不烧 provider。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from utils import topic_state as ts

#: 存储口径 UTC；调度器 ``_local_now`` 返回北京墙钟（UTC+8）naive。
BASE_UTC = datetime(2026, 11, 21, 2, 0, 0)
BASE_LOCAL = datetime(2026, 11, 21, 10, 0, 0)
KEY = "9:web:abc12345"
CHAR = "default"


@pytest.fixture()
def topic_env(tmp_path, monkeypatch):
    cfg = tmp_path / "scheduler_config.json"
    cfg.write_text(
        json.dumps({"topic_state": {"enabled": True}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(ts, "_CONFIG_PATH", cfg, raising=False)
    monkeypatch.setattr(ts, "_DB_PATH", tmp_path / "topic_state.db", raising=False)
    return tmp_path


def _create_topic() -> str:
    result = ts.update_after_turn(
        KEY, CHAR, "我明天下午要去面试", now=BASE_UTC
    )
    assert result["action"] in ("new", "continue", "switch"), result
    snap = ts.get_topic_snapshot(KEY, CHAR, now=BASE_UTC)
    assert snap["active"], "夹具必须先产出一个 active 话题"
    return str(snap["active"][0]["title"])


def _raw_topic() -> dict:
    conn = sqlite3.connect(str(ts._DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM topic_state WHERE session_key=? AND character_id=?",
            (KEY, CHAR),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# 1. 数据层
# ═══════════════════════════════════════════════════════════


def test_followup_columns_migrated_on_existing_db(topic_env):
    """存量库（无两列）首次读写必须幂等补列，不得整段降级。"""
    # 先手工造一个旧 schema 的 topic_state 表
    conn = sqlite3.connect(str(ts._DB_PATH))
    conn.executescript(
        """
        CREATE TABLE topic_state (
            session_key TEXT NOT NULL, character_id TEXT NOT NULL,
            topic_id TEXT NOT NULL, title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            salience REAL NOT NULL DEFAULT 0.5,
            mention_count INTEGER NOT NULL DEFAULT 0,
            turn_count INTEGER NOT NULL DEFAULT 0,
            anchors TEXT NOT NULL DEFAULT '[]',
            idle_turns INTEGER NOT NULL DEFAULT 0,
            recall_count INTEGER NOT NULL DEFAULT 0,
            last_recalled_at TEXT NOT NULL DEFAULT '',
            last_active_at TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            PRIMARY KEY (session_key, character_id, topic_id)
        );
        """
    )
    conn.commit()
    conn.close()
    assert ts.topic_followup_allowed(KEY, CHAR, "旧话题", now=BASE_UTC) is True
    cols = {r[1] for r in sqlite3.connect(str(ts._DB_PATH)).execute(
        "PRAGMA table_info(topic_state)"
    )}
    assert {"followup_count", "last_followup_at"} <= cols


def test_followup_count_increments_and_limit_is_one(topic_env):
    title = _create_topic()
    assert ts.topic_followup_allowed(KEY, CHAR, title, now=BASE_UTC) is True

    assert ts.note_topic_followup(KEY, CHAR, title, now=BASE_UTC) is True
    row = _raw_topic()
    assert int(row["followup_count"]) == 1
    assert str(row["last_followup_at"])

    assert ts.topic_followup_allowed(KEY, CHAR, title, now=BASE_UTC) is False
    # 再记一次也不允许越过上限
    assert ts.note_topic_followup(KEY, CHAR, title, now=BASE_UTC) is True
    assert ts.topic_followup_allowed(KEY, CHAR, title, now=BASE_UTC) is False


def test_user_turn_resets_followup(topic_env):
    title = _create_topic()
    ts.note_topic_followup(KEY, CHAR, title, now=BASE_UTC)
    assert ts.topic_followup_allowed(KEY, CHAR, title, now=BASE_UTC) is False

    ts.update_after_turn(KEY, CHAR, "刚面试完，累死了", now=BASE_UTC + timedelta(minutes=1))
    assert int(_raw_topic()["followup_count"]) == 0
    assert ts.topic_followup_allowed(KEY, CHAR, title, now=BASE_UTC) is True


def test_followup_isolated_by_session_and_character(topic_env):
    title = _create_topic()
    ts.note_topic_followup(KEY, CHAR, title, now=BASE_UTC)
    assert ts.topic_followup_allowed("10:web:other", CHAR, title, now=BASE_UTC) is True
    assert ts.topic_followup_allowed(KEY, "other", title, now=BASE_UTC) is True


# ═══════════════════════════════════════════════════════════
# 2. 调度器计划消费点
# ═══════════════════════════════════════════════════════════


def _make_sched(monkeypatch, *, now_local: datetime = BASE_LOCAL):
    import proactive.ase_engine as ase
    import proactive.llm_proactive as lp
    import proactive.proactive_context as pctx
    import proactive.proactive_hooks as hooks_mod
    import shisi.agent_plane.runtime as rt
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(ase, "_local_now", lambda: now_local)
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
    ledger: list[dict] = []
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: ledger.append(kw))

    class _FakeEng:
        def __init__(self):
            self._last_delivery_time = None
            self._unanswered_streak = 0
            self._last_user_message = ""
            self._knowledge_character_id = CHAR
            self.urgency = type("U", (), {"total": 2.5})()
            self.commit_calls: list[dict] = []

        def _hours_since_last_chat(self):
            return 90.0

        def tick(self, hours, dry_run=False):
            return None

        def commit_sent(self, result):
            self.commit_calls.append(result)

    eng = _FakeEng()
    hub = type("Hub", (), {"get": lambda self, k: eng if k == KEY else None})()

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
    sched._deliver = lambda message, session_key=None, character_id=None: True

    decision = {
        "should_contact": False,
        "message": "",
        "reason": "先不打扰",
        "wait_minutes": 30,
        "next_topic": "",
        "next_intent": "",
    }
    calls: list[dict] = []

    def _decide(llm, ctx):
        calls.append({"ctx": ctx})
        return dict(decision)

    monkeypatch.setattr(lp, "decide_proactive", _decide)
    return sched, hub, calls, ledger


def test_second_unanswered_followup_is_blocked_before_llm(topic_env, monkeypatch):
    title = _create_topic()
    ts.note_topic_followup(KEY, CHAR, title, now=BASE_UTC)
    ts.save_next_plan(
        KEY, CHAR, title, "问问面试结果", wait_minutes=0, planned_at=BASE_UTC
    )
    sched, hub, calls, ledger = _make_sched(monkeypatch)

    sched._llm_proactive_one_user(hub, KEY)

    assert calls == [], "话题追问超限必须在决策 LLM 之前短路"
    assert ts.take_next_plan(KEY, CHAR, now=BASE_UTC) is None, "超限计划必须被消费"
    assert [e.get("reason") for e in ledger] == ["topic_followup_limit"]


def test_first_followup_still_reaches_decision(topic_env, monkeypatch):
    title = _create_topic()
    ts.save_next_plan(
        KEY, CHAR, title, "问问面试结果", wait_minutes=0, planned_at=BASE_UTC
    )
    sched, hub, calls, _ledger = _make_sched(monkeypatch)
    sched._llm_proactive_one_user(hub, KEY)
    assert len(calls) == 1, "第一次追问必须放行到决策（只限第二次）"
