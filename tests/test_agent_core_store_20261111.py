"""agent_core store 域（events / threads / commitments）—— B-1 红测。

契约：``docs`` 外的已批准实施契约（2026-11-11 store 域）。本文件只覆盖
store 三层的**落库语义与 fail-open 零行为**；``replay`` 与四个挂载点
（connector / orchestrator / scheduler）由同批其它文件负责。

三条硬要求在本文件的钉子：

1. **ROWID 不进索引**（``test_schema_script_is_syntactically_valid``）——
   原契约把 ``rowid`` 写进 ``CREATE INDEX``，实测 ``no such column: rowid``，
   会让 ``executescript`` 整体抛错 ⇒ 三表永远建不出来 ⇒ 全链静默零事件。
2. **``last_event_at`` 必须在 DDL 里**（``test_open_thread_uses_last_event_at_and_reopens``）
   —— 原 DDL 缺列而 UPDATE 要用它 ⇒ ``no such column``。
3. **``user_key`` 是完整会话键**（``test_user_key_matches_structured_memory_authority``）
   —— 与 ``shisi/memory/legacy/structured_memory.py:482-492`` 逐字对齐。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from uuid import NAMESPACE_URL, uuid5

import pytest

USER_KEY = "2:o9cq80@im.wechat"
OTHER_KEY = "4:o9cq80other@im.wechat"
OWNER_ID = 2

EXPECTED_INDEXES = 9


# ---------------------------------------------------------------------------
# 夹具 / 工具
# ---------------------------------------------------------------------------


def _modules():
    from agent_core import commitments, events, threads

    return events, threads, commitments


def _reset(events) -> None:
    events._schema_ready.clear()
    events._warned.clear()
    events._fail_counts.clear()
    events._shadow_ready_logged = False


def _query(events, sql: str, params=()) -> list[dict]:
    conn = sqlite3.connect(str(events._DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """开启态 + 沙箱库（conftest 不在本批边界内，沙箱必须写在文件里）。"""
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    events, threads, commitments = _modules()
    monkeypatch.setattr(events, "_DB_PATH", tmp_path / "agent_core.db")
    _reset(events)
    yield events, threads, commitments
    _reset(events)


@pytest.fixture()
def store_off(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_CORE_EVENTS", raising=False)
    events, threads, commitments = _modules()
    monkeypatch.setattr(events, "_DB_PATH", tmp_path / "agent_core_off.db")
    _reset(events)
    yield events, threads, commitments
    _reset(events)


# ---------------------------------------------------------------------------
# 开关与 schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes"])
def test_enabled_true_values(store_off, monkeypatch, value):
    events, _t, _c = store_off
    monkeypatch.setenv("AGENT_CORE_EVENTS", value)
    assert events.enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "", "off", "no", "  "])
def test_enabled_false_values(store_off, monkeypatch, value):
    events, _t, _c = store_off
    monkeypatch.setenv("AGENT_CORE_EVENTS", value)
    assert events.enabled() is False


def test_enabled_reads_env_every_call_without_caching(store_off, monkeypatch):
    events, _t, _c = store_off
    assert events.enabled() is False
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    assert events.enabled() is True
    monkeypatch.setenv("AGENT_CORE_EVENTS", "0")
    assert events.enabled() is False


def test_module_importable_and_schema_creates_three_tables(store):
    events, _threads, _commitments = store
    assert events.ensure_schema() is True
    conn = sqlite3.connect(str(events._DB_PATH))
    try:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        indexes = [
            r[0] for r in conn.execute("select name from sqlite_master where type='index' and name like 'idx_agent_%'")
        ]
    finally:
        conn.close()
    assert {"agent_events", "agent_threads", "agent_commitments"} <= tables
    assert len(indexes) == EXPECTED_INDEXES


def test_schema_script_is_syntactically_valid(store):
    """独立内存库连跑两次：任何 rowid 进索引列清单都会在这里红。"""
    events, _threads, _commitments = store
    for _ in range(2):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(events._SCHEMA)
            tables = {
                r[0] for r in conn.execute("select name from sqlite_master where type='table' and name like 'agent_%'")
            }
            indexes = [
                r[0]
                for r in conn.execute("select name from sqlite_master where type='index' and name like 'idx_agent_%'")
            ]
        finally:
            conn.close()
        assert len(tables) == 3
        assert len(indexes) == EXPECTED_INDEXES


def test_schema_is_idempotent_across_calls(store):
    events, _threads, _commitments = store
    for _ in range(3):
        assert events.ensure_schema() is True
    conn = sqlite3.connect(str(events._DB_PATH))
    try:
        indexes = conn.execute(
            "select count(*) from sqlite_master where type='index' and name like 'idx_agent_%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert indexes == EXPECTED_INDEXES


def test_disabled_by_default_creates_no_db(store_off, tmp_path):
    events, threads, commitments = store_off
    assert events.enabled() is False
    assert events.record_event("user_msg", session_key=USER_KEY) == ""
    assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY) == ""
    assert commitments.add_commitment("提醒", user_key=USER_KEY) == ""
    assert not (tmp_path / "agent_core_off.db").exists()


# ---------------------------------------------------------------------------
# turn_key —— 轮次身份唯一 owner
# ---------------------------------------------------------------------------


def test_turn_key_prefers_explicit_and_falls_back_to_contextvar(store):
    from utils.inbound_context import bind_message_id

    events, _threads, _commitments = store
    with bind_message_id("ctx-1"):
        assert events.turn_key() == "m:ctx-1"
        assert events.turn_key(" m2 ") == "m:m2"
        assert events.turn_key("   ") == "m:ctx-1"
    assert events.turn_key() == ""


# ---------------------------------------------------------------------------
# record_event
# ---------------------------------------------------------------------------


def test_record_event_roundtrip_attribution(store):
    events, threads, _commitments = store
    tid = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, topic="早饭")
    payload = {
        "msg_id": "m1",
        "text_len": 4,
        "nested": {"a": [1, 2], "b": {"c": True}},
    }
    event_id = events.record_event(
        "user_msg",
        user_key=USER_KEY,
        session_key=USER_KEY,
        character_id="lin",
        turn_id="m:abc",
        thread_id=tid,
        role="user",
        source="wechat",
        payload=payload,
    )
    assert event_id
    rows = _query(events, "select * from agent_events")
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == event_id
    assert row["user_key"] == USER_KEY
    assert row["session_key"] == USER_KEY
    assert row["owner_user_id"] == OWNER_ID
    assert row["character_id"] == "lin"
    assert row["turn_id"] == "m:abc"
    assert row["thread_id"] == tid
    assert row["kind"] == "user_msg"
    assert row["role"] == "user"
    assert row["source"] == "wechat"
    assert json.loads(row["payload_json"]) == payload


def test_record_event_drops_unattributable(store):
    events, _threads, _commitments = store
    assert events.record_event("user_msg", session_key="") == ""
    assert events.record_event("user_msg", session_key="wxid@im.wechat") == ""
    events.ensure_schema()
    assert _query(events, "select * from agent_events") == []


def test_record_event_rejects_user_key_session_mismatch(store):
    events, _threads, _commitments = store
    assert events.record_event("user_msg", user_key=OTHER_KEY, session_key=USER_KEY) == ""


def test_record_event_dedupe_is_atomic(store):
    events, _threads, _commitments = store
    first = events.record_event("user_msg", session_key=USER_KEY, dedupe_key="user_msg:m1")
    second = events.record_event("user_msg", session_key=USER_KEY, dedupe_key="user_msg:m1")
    assert first == second
    assert first == str(uuid5(NAMESPACE_URL, f"agent_core:{USER_KEY}|user_msg:m1"))
    assert len(_query(events, "select * from agent_events")) == 1

    third = events.record_event("user_msg", session_key=USER_KEY, dedupe_key="user_msg:m2")
    assert third and third != first
    assert len(_query(events, "select * from agent_events")) == 2

    non_dedup_a = events.record_event("user_msg", session_key=USER_KEY)
    non_dedup_b = events.record_event("user_msg", session_key=USER_KEY)
    assert non_dedup_a and non_dedup_b and non_dedup_a != non_dedup_b


def test_record_event_swallows_errors(store, monkeypatch):
    events, _threads, _commitments = store

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(events, "connect", _boom)
    assert events.record_event("user_msg", session_key=USER_KEY) == ""

    events.ensure_schema()
    monkeypatch.setattr(
        events,
        "connect",
        lambda *_a, **_k: sqlite3.connect(f"file:{events._DB_PATH}?mode=ro", uri=True, isolation_level=None),
    )
    assert events.record_event("user_msg", session_key=USER_KEY) == ""


def test_created_at_format_and_injection(store):
    events, _threads, _commitments = store
    events.record_event(
        "user_msg",
        session_key=USER_KEY,
        created_at="2026-11-11 09:00:00",
    )
    events.record_event("user_msg", session_key=USER_KEY)
    rows = _query(events, "select created_at from agent_events order by id")
    assert rows[0]["created_at"] == "2026-11-11 09:00:00"
    stamp = rows[1]["created_at"]
    assert len(stamp) == 19 and stamp[4] == "-" and stamp[10] == " " and stamp[16] == ":"
    from utils.local_time import now_local

    assert stamp[:16] == now_local().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# threads
# ---------------------------------------------------------------------------


def test_open_thread_uses_last_event_at_and_reopens(store, monkeypatch):
    events, threads, _commitments = store
    monkeypatch.setattr(events, "_now_ts", lambda: "2026-11-11 10:00:00")
    tid = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, topic="早饭吃什么")
    assert tid == str(uuid5(NAMESPACE_URL, f"{USER_KEY}|{USER_KEY}"))

    monkeypatch.setattr(events, "_now_ts", lambda: "2026-11-11 10:05:00")
    assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY) == tid
    rows = _query(events, "select * from agent_threads")
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["turn_count"] == 2
    assert rows[0]["last_event_at"] == "2026-11-11 10:05:00"
    assert rows[0]["opened_at"] == "2026-11-11 10:00:00"

    assert threads.close_thread(tid, user_key=USER_KEY, reason="done") is True
    closed = _query(events, "select * from agent_threads")[0]
    assert closed["status"] == "closed"
    assert closed["closed_at"] == "2026-11-11 10:05:00"
    assert closed["close_reason"] == "done"

    assert threads.close_thread(tid, user_key=USER_KEY, reason="again") is False

    monkeypatch.setattr(events, "_now_ts", lambda: "2026-11-11 11:00:00")
    assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY) == tid
    reopened = _query(events, "select * from agent_threads")[0]
    assert reopened["status"] == "open"
    assert reopened["closed_at"] == ""
    assert reopened["close_reason"] == ""
    assert reopened["turn_count"] == 3
    assert reopened["last_event_at"] == "2026-11-11 11:00:00"


def test_thread_is_session_scoped_not_character_scoped(store):
    events, threads, _commitments = store
    tid_a = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="lin")
    tid_b = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="su")
    assert tid_a == tid_b
    events.record_event("user_msg", user_key=USER_KEY, session_key=USER_KEY, thread_id=tid_a, character_id="lin")
    events.record_event("user_msg", user_key=USER_KEY, session_key=USER_KEY, thread_id=tid_b, character_id="su")
    rows = _query(events, "select character_id from agent_events where thread_id=?", (tid_a,))
    assert {r["character_id"] for r in rows} == {"lin", "su"}


def test_close_thread_requires_scope_and_unknown_false(store):
    events, threads, _commitments = store
    tid = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY)
    assert threads.close_thread(tid, user_key=OTHER_KEY) is False
    assert threads.close_thread("nope", user_key=USER_KEY) is False
    assert threads.close_thread("", user_key=USER_KEY) is False
    assert _query(events, "select * from agent_threads")[0]["status"] == "open"


def test_open_thread_drops_unattributable(store):
    events, threads, _commitments = store
    assert threads.open_thread() == ""
    assert threads.open_thread(session_key="wxid@im.wechat") == ""


# ---------------------------------------------------------------------------
# commitments
# ---------------------------------------------------------------------------


def test_add_commitment_dedupes_by_reminder_ref(store):
    events, _threads, commitments = store
    detail = "叫\u0000我起床\n\n记得早点睡 " + "啊" * 300
    first = commitments.add_commitment(
        detail,
        user_key=USER_KEY,
        session_key=USER_KEY,
        due_at="2026-11-12 06:00",
        ref="reminder:12",
        dedupe_key="reminder:12",
    )
    second = commitments.add_commitment(
        detail,
        user_key=USER_KEY,
        session_key=USER_KEY,
        due_at="2026-11-12 06:00",
        ref="reminder:12",
        dedupe_key="reminder:12",
    )
    assert first == second
    assert first == str(uuid5(NAMESPACE_URL, f"agent_core:{USER_KEY}|reminder:12"))
    rows = _query(events, "select * from agent_commitments")
    assert len(rows) == 1
    row = rows[0]
    assert row["owner_user_id"] == OWNER_ID
    assert row["kind"] == "remind"
    assert row["status"] == "open"
    assert len(row["detail"]) == 200
    assert "\u0000" not in row["detail"]
    assert "\n" not in row["detail"]
    assert row["due_at"] == "2026-11-12 06:00:00"
    assert row["ref"] == "reminder:12"


def test_add_commitment_drops_unattributable(store):
    events, _threads, commitments = store
    assert commitments.add_commitment("提醒", user_key="") == ""
    assert commitments.add_commitment("提醒", user_key="wxid@im.wechat") == ""


def test_list_open_commitments_scope_and_order(store):
    events, _threads, commitments = store
    early = commitments.add_commitment("早点", user_key=USER_KEY, due_at="2026-11-12 06:00:00")
    late = commitments.add_commitment("晚点", user_key=USER_KEY, due_at="2026-11-20 06:00:00")
    none_due = commitments.add_commitment("无期限", user_key=USER_KEY)
    done = commitments.add_commitment("已完成", user_key=USER_KEY, due_at="2026-11-10 06:00:00")
    stale = commitments.add_commitment("将过期", user_key=USER_KEY, due_at="2026-11-09 06:00:00")
    other = commitments.add_commitment("别人的", user_key=OTHER_KEY, due_at="2026-11-11 00:00:00")
    assert commitments.fulfil_commitment(done, user_key=USER_KEY, owner_user_id=OWNER_ID)
    assert commitments.expire_commitments("2026-11-11 00:00:00", user_key=USER_KEY) == 1

    rows = commitments.list_open_commitments(USER_KEY)
    assert [r["commitment_id"] for r in rows] == [early, late, none_due]
    assert all(r["user_key"] == USER_KEY and r["status"] == "open" for r in rows)
    assert {stale, other}.isdisjoint({r["commitment_id"] for r in rows})

    assert [r["commitment_id"] for r in commitments.list_open_commitments(USER_KEY, limit=2)] == [
        early,
        late,
    ]
    assert commitments.list_open_commitments(USER_KEY, session_key=USER_KEY)
    assert commitments.list_open_commitments(USER_KEY, session_key="2:nope") == []
    assert commitments.list_open_commitments("") == []
    assert [
        r["commitment_id"] for r in commitments.list_open_commitments(USER_KEY, due_before="2026-11-13 00:00:00")
    ] == [early]


def test_fulfil_commitment_requires_user_key_and_is_idempotent(store):
    events, _threads, commitments = store
    cid = commitments.add_commitment("叫我起床", user_key=USER_KEY, due_at="2026-11-12 06:00")
    with pytest.raises(TypeError):
        commitments.fulfil_commitment(cid)  # user_key 是必填关键字（水平越权写防线）
    assert commitments.fulfil_commitment(cid, user_key="") is False
    assert commitments.fulfil_commitment(cid, user_key=OTHER_KEY, owner_user_id=OWNER_ID) is False
    assert commitments.fulfil_commitment(cid, user_key=USER_KEY, owner_user_id=OWNER_ID) is True
    row = _query(events, "select * from agent_commitments")[0]
    assert row["status"] == "fulfilled"
    assert row["fulfilled_at"]
    first_fulfilled_at = row["fulfilled_at"]

    assert commitments.fulfil_commitment(cid, user_key=USER_KEY, owner_user_id=OWNER_ID) is False
    again = _query(events, "select * from agent_commitments")[0]
    assert again["fulfilled_at"] == first_fulfilled_at

    by_ref = commitments.add_commitment("按 ref 定位", user_key=USER_KEY, ref="reminder:99", dedupe_key="reminder:99")
    assert commitments.fulfil_commitment("", user_key=USER_KEY, owner_user_id=OWNER_ID, ref="reminder:99") is True
    assert commitments.fulfil_commitment("", user_key=USER_KEY, owner_user_id=OWNER_ID) is False
    row_by_ref = _query(events, "select * from agent_commitments where commitment_id=?", (by_ref,))[0]
    assert row_by_ref["status"] == "fulfilled"


def test_expire_commitments_only_overdue_open(store):
    events, _threads, commitments = store
    overdue = commitments.add_commitment("过期了", user_key=USER_KEY, due_at="2026-11-10 06:00:00")
    future = commitments.add_commitment("还没到", user_key=USER_KEY, due_at="2030-01-01 06:00:00")
    expiring = commitments.add_commitment("后来失效", user_key=USER_KEY, expires_at="2026-11-12 00:00:00")
    done = commitments.add_commitment("已完成的不动", user_key=USER_KEY, due_at="2026-11-01 06:00:00")
    assert commitments.fulfil_commitment(done, user_key=USER_KEY, owner_user_id=OWNER_ID)

    assert commitments.expire_commitments("2026-11-11 00:00:00", user_key=USER_KEY) == 1
    assert commitments.expire_commitments("2026-11-13 00:00:00", user_key=USER_KEY) == 1
    status = {
        r["commitment_id"]: r["status"] for r in _query(events, "select commitment_id, status from agent_commitments")
    }
    assert status[overdue] == "expired"
    assert status[expiring] == "expired"
    assert status[future] == "open"
    assert status[done] == "fulfilled"


# ---------------------------------------------------------------------------
# 连接生命周期与失败可观测性
# ---------------------------------------------------------------------------


class _CountingConn:
    """包一层只统计 close 的连接（透传 execute/row_factory）。"""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "closed", False)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        if name in {"_conn", "closed"}:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self):
        object.__setattr__(self, "closed", True)
        object.__getattribute__(self, "_conn").close()


def test_connect_is_short_lived_autocommit_with_row_factory(store):
    from contextlib import closing

    events, _threads, _commitments = store
    events.ensure_schema()
    with closing(events.connect()) as conn:
        assert conn.row_factory is sqlite3.Row
        assert conn.isolation_level is None
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 50
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    with closing(events.connect(busy_timeout_ms=3000)) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3000


def test_thread_and_commitment_writes_swallow_errors(store, monkeypatch):
    events, threads, commitments = store
    events.ensure_schema()

    def _boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(events, "connect", _boom)
    assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY) == ""
    assert threads.close_thread("tid", user_key=USER_KEY) is False
    assert commitments.add_commitment("x", user_key=USER_KEY) == ""
    assert commitments.list_open_commitments(USER_KEY) == []
    assert commitments.fulfil_commitment("cid", user_key=USER_KEY, owner_user_id=OWNER_ID) is False
    assert commitments.expire_commitments(user_key=USER_KEY) == 0
    assert events.failure_count("shadow_failed") >= 4


def test_no_connection_leak_and_budget_constants(store, monkeypatch):
    events, threads, commitments = store
    made: list[_CountingConn] = []
    real_connect = events.connect

    def _spy(*args, **kwargs):
        conn = _CountingConn(real_connect(*args, **kwargs))
        made.append(conn)
        return conn

    monkeypatch.setattr(events, "connect", _spy)

    events.record_event("user_msg", session_key=USER_KEY, dedupe_key="k1")
    threads.open_thread(user_key=USER_KEY, session_key=USER_KEY)
    commitments.add_commitment("x", user_key=USER_KEY, due_at="2026-11-12 06:00")
    commitments.list_open_commitments(USER_KEY)

    assert made
    assert all(c.closed for c in made)
    assert events._WRITE_BUSY_TIMEOUT_MS <= 50
    assert events._SCHEMA_LOCK_TIMEOUT_S <= 0.05


def test_schema_failure_warns_once_and_returns_empty(store, monkeypatch, caplog):
    events, threads, commitments = store
    monkeypatch.setattr(events, "_SCHEMA", "CREATE TABLE oops (")
    with caplog.at_level(logging.WARNING):
        assert events.record_event("user_msg", session_key=USER_KEY) == ""
        assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY) == ""
        assert commitments.add_commitment("x", user_key=USER_KEY) == ""
        assert events.record_event("user_msg", session_key=USER_KEY) == ""

    assert events.failure_count("schema_failed") >= 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "schema_failed" in r.getMessage()]
    assert len(warnings) == 1
    assert not events._schema_ready


def test_user_key_matches_structured_memory_authority(store):
    events, _threads, _commitments = store
    from shisi.memory.legacy.structured_memory import StructuredMemory

    for key in ["", USER_KEY, "2:web:deadbeef", "2:3:web:8hex", "wxid@im.wechat"]:
        assert events._user_key_from_session(key) == StructuredMemory.user_key_from_session(key)
