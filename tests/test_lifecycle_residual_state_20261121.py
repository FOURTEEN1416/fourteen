"""账号生命周期补齐红测（2026-11-21 主动线治理批）。

审计实况：``api/lifecycle.py`` 的删号步骤漏了
``runtime_plane`` 全部表、``user_character_settings``、``emotion_stage_state``、
``vital_signs_state``；脚本路径的节流文件清理只覆盖 3 个字典段 + 日期集，
漏了 ``deliver_fail_counts_ts`` / ``wait_window_event_day`` / ``quiet_event_day``
/ ``model_degraded_event_day`` / ``gate_skip_event_day``（4 个账本段 + sidecar）。

本文件只测 owner 级 purge/verify 与步骤注册，不跑完整删号 API。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from api.lifecycle import (
    _OWNER_STEPS,
    _STEP_ORDER,
    AccountScope,
    _purge_throttle_file,
    _runtime_plane_purge,
    _runtime_plane_verify,
    _scheduler_throttle_verify,
    _user_state_tables_purge,
    _user_state_tables_verify,
)
from proactive import runtime_plane


@pytest.fixture()
def state_db(tmp_path) -> Path:
    path = tmp_path / "sqlite.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE user_character_settings (
            user_key TEXT NOT NULL, character_id TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
            PRIMARY KEY (user_key, character_id)
        );
        CREATE TABLE emotion_stage_state (
            character_id TEXT PRIMARY KEY, current_stage TEXT NOT NULL DEFAULT '陌生',
            stage_index INTEGER NOT NULL DEFAULT 0,
            affinity_value REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE vital_signs_state (
            character_id TEXT PRIMARY KEY, heart_rate REAL NOT NULL DEFAULT 72,
            temperature REAL NOT NULL DEFAULT 36.5,
            breath_rate REAL NOT NULL DEFAULT 16,
            last_emotion TEXT NOT NULL DEFAULT 'neutral', updated_at TEXT NOT NULL
        );
        CREATE TABLE agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            user_key TEXT NOT NULL, session_key TEXT NOT NULL,
            owner_user_id INTEGER NOT NULL, character_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'assistant',
            source TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL DEFAULT '{}',
            dedupe_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE agent_threads (
            thread_id TEXT PRIMARY KEY, user_key TEXT NOT NULL,
            session_key TEXT NOT NULL, owner_user_id INTEGER NOT NULL,
            character_id TEXT NOT NULL DEFAULT '', topic TEXT NOT NULL DEFAULT '',
            last_utterance TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            opened_at TEXT NOT NULL DEFAULT '', last_event_at TEXT NOT NULL DEFAULT '',
            closed_at TEXT, close_reason TEXT NOT NULL DEFAULT '',
            turn_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE agent_commitments (
            commitment_id TEXT PRIMARY KEY, user_key TEXT NOT NULL,
            session_key TEXT NOT NULL, owner_user_id INTEGER NOT NULL,
            character_id TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
            due_at TEXT NOT NULL DEFAULT '', expires_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open', source_turn_id TEXT NOT NULL DEFAULT '',
            ref TEXT NOT NULL DEFAULT '', dedupe_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
            fulfilled_at TEXT, meta_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.executemany(
        "INSERT INTO user_character_settings VALUES (?,?,?,?)",
        [
            ("7:web:abc", "default", "{}", "2026-11-21"),
            ("7:wxid@im.wechat", "card7", "{}", "2026-11-21"),
            ("8:web:def", "default", "{}", "2026-11-21"),
        ],
    )
    conn.executemany(
        "INSERT INTO emotion_stage_state VALUES (?,?,?,?,?)",
        [
            ("7:web:abc::default", "熟悉", 2, 30.0, "2026-11-21"),
            ("7:card7", "陌生", 0, 0.0, "2026-11-21"),
            ("8:web:def::default", "亲密", 3, 70.0, "2026-11-21"),
        ],
    )
    conn.executemany(
        "INSERT INTO vital_signs_state VALUES (?,?,?,?,?,?)",
        [
            ("7:web:abc::default", 80, 36.6, 18, "happy", "2026-11-21"),
            ("8:web:def::default", 70, 36.5, 16, "neutral", "2026-11-21"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_events (event_id,user_key,session_key,owner_user_id,kind,created_at) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("e7", "7:web:abc", "7:web:abc", 7, "chat", "2026-11-21"),
            ("e8", "8:web:def", "8:web:def", 8, "chat", "2026-11-21"),
        ],
    )
    conn.executemany(
        "INSERT INTO agent_threads (thread_id,user_key,session_key,owner_user_id) "
        "VALUES (?,?,?,?)",
        [("t7", "7:web:abc", "7:web:abc", 7), ("t8", "8:web:def", "8:web:def", 8)],
    )
    conn.executemany(
        "INSERT INTO agent_commitments (commitment_id,user_key,session_key,owner_user_id) "
        "VALUES (?,?,?,?)",
        [("c7", "7:web:abc", "7:web:abc", 7), ("c8", "8:web:def", "8:web:def", 8)],
    )
    conn.commit()
    conn.close()
    return path


def _scope(db: Path, uid: int = 7) -> AccountScope:
    return AccountScope(
        user_id=uid,
        session_keys=[f"{uid}:web:abc"],
        wxids=[],
        sqlite_db=db,
    )


def test_user_state_tables_purge_and_verify(state_db):
    scope = _scope(state_db)
    receipt = _user_state_tables_purge(scope)
    assert receipt["user_character_settings"] == 2
    assert receipt["emotion_stage_state"] == 2
    assert receipt["vital_signs_state"] == 1
    assert receipt["agent_events"] == 1
    assert receipt["agent_threads"] == 1
    assert receipt["agent_commitments"] == 1
    assert _user_state_tables_verify(scope) == {
        "user_character_settings": 0,
        "emotion_stage_state": 0,
        "vital_signs_state": 0,
        "agent_events": 0,
        "agent_threads": 0,
        "agent_commitments": 0,
    }

    conn = sqlite3.connect(str(state_db))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM user_character_settings WHERE user_key LIKE '8:%'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM emotion_stage_state WHERE character_id LIKE '8:%'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM vital_signs_state WHERE character_id LIKE '8:%'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM agent_events WHERE owner_user_id=8"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_user_state_purge_idempotent(state_db):
    scope = _scope(state_db)
    _user_state_tables_purge(scope)
    second = _user_state_tables_purge(scope)
    assert all(v == 0 for v in second.values())


def test_runtime_plane_all_tables_purged(tmp_path, monkeypatch):
    db = tmp_path / "runtime_plane.db"
    runtime_plane.set_db_path(db)
    try:
        # 建表
        with runtime_plane._conn():
            pass
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO channel_desired_state VALUES (7,0,'connected',1)")
        conn.execute("INSERT INTO channel_desired_state VALUES (8,0,'connected',1)")
        conn.execute("INSERT INTO channel_presence VALUES (7,0,'pid',1)")
        conn.execute("INSERT INTO channel_presence VALUES (8,0,'pid',1)")
        conn.execute(
            "INSERT INTO outbound_commands (kind,channel,session_key,owner_id,message,status,created_at) "
            "VALUES ('proactive','websocket','7:web:abc',7,'x','pending',1)"
        )
        conn.execute(
            "INSERT INTO outbound_commands (kind,channel,session_key,owner_id,message,status,created_at) "
            "VALUES ('proactive','websocket','8:web:def',8,'x','pending',1)"
        )
        conn.execute(
            "INSERT INTO control_commands (kind,session_key,status,created_at) "
            "VALUES ('proactive_manual','7:web:abc','pending',1)"
        )
        conn.execute(
            "INSERT INTO inbound_seen (message_id,session_key,status,ts) "
            "VALUES ('m7','7:web:abc','done',1)"
        )
        conn.execute(
            "INSERT INTO followup_budget (session_key,day,used,updated_at) "
            "VALUES ('7:web:abc','2026-11-21',2,1)"
        )
        conn.execute(
            "INSERT INTO effect_dedup (dedup_key,result,ts) "
            "VALUES ('set_reminder:7:web:abc:m7:abc','1',1)"
        )
        conn.commit()
        conn.close()

        receipt = runtime_plane.purge_owner(7, ["7:web:abc"])
        assert receipt["channel_desired_state"] == 1
        assert receipt["channel_presence"] == 1
        assert receipt["outbound_commands"] == 1
        assert receipt["control_commands"] == 1
        assert receipt["inbound_seen"] == 1
        assert receipt["followup_budget"] == 1
        assert receipt["effect_dedup"] == 1
        assert runtime_plane.count_owner(7, ["7:web:abc"]) == 0

        conn = sqlite3.connect(str(db))
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM outbound_commands WHERE owner_id=8"
            ).fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        runtime_plane.set_db_path(None)


def test_runtime_plane_step_purges_and_verifies(tmp_path):
    runtime_plane.set_db_path(tmp_path / "runtime_plane.db")
    try:
        runtime_plane.enqueue_send(
            kind="proactive", channel="websocket", session_key="7:web:abc",
            owner_id=7, message="x",
        )
        runtime_plane.enqueue_send(
            kind="proactive", channel="websocket", session_key="8:web:def",
            owner_id=8, message="x",
        )
        scope = _scope(tmp_path / "sqlite.db")
        assert _runtime_plane_verify(scope)["rows"] == 1
        receipt = _runtime_plane_purge(scope)
        assert receipt["outbound_commands"] == 1
        assert _runtime_plane_verify(scope)["rows"] == 0
    finally:
        runtime_plane.set_db_path(None)


def test_throttle_file_purges_all_ledger_segments(monkeypatch, tmp_path):
    from proactive.scheduler import ProactiveScheduler

    cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg, raising=False)
    payload = {
        "throttle": {
            "llm_proactive_next_ok": {"7:web:abc": 1.0, "8:web:def": 1.0},
            "deliver_fail_counts": {"7:web:abc": 5, "8:web:def": 1},
            "deliver_fail_counts_ts": {"7:web:abc": 1.0, "8:web:def": 1.0},
            "disabled_event_day": {"7:web:abc": "2026-11-21", "8:web:def": "2026-11-21"},
            "wait_window_event_day": {"7:web:abc": "2026-11-21", "8:web:def": "2026-11-21"},
            "quiet_event_day": {"7:web:abc": "2026-11-21", "8:web:def": "2026-11-21"},
            "model_degraded_event_day": {"7:web:abc": "2026-11-21", "8:web:def": "2026-11-21"},
            "gate_skip_event_day": {
                "7:web:abc|unanswered_limit": "2026-11-21",
                "8:web:def|unanswered_limit": "2026-11-21",
            },
            "deliver_circuit_open": {"7:web:abc": 9.0, "8:web:def": 9.0},
            "important_dates_sent": [
                "2026-11-21|7:web:abc|生日",
                "2026-11-21|8:web:def|生日",
            ],
        }
    }
    cfg.write_text(json.dumps(payload), encoding="utf-8")

    _purge_throttle_file(7, ["7:web:abc"])
    data = json.loads(cfg.read_text(encoding="utf-8"))["throttle"]
    for section in (
        "llm_proactive_next_ok", "deliver_fail_counts", "deliver_fail_counts_ts",
        "disabled_event_day", "wait_window_event_day", "quiet_event_day",
        "model_degraded_event_day", "gate_skip_event_day", "deliver_circuit_open",
    ):
        assert "7:web:abc" not in data[section]
        if section == "gate_skip_event_day":
            assert "8:web:def|unanswered_limit" in data[section]
        else:
            assert "8:web:def" in data[section]
    assert data["important_dates_sent"] == ["2026-11-21|8:web:def|生日"]

    class _Scope:
        user_id = 7
        session_keys = ["7:web:abc"]
        wxids: list[str] = []

    assert _scheduler_throttle_verify(_Scope())["entries"] == 0


def test_new_steps_registered_before_users_db():
    assert "runtime_plane" in _OWNER_STEPS
    assert "user_state_tables" in _OWNER_STEPS
    assert _STEP_ORDER.index("runtime_plane") < _STEP_ORDER.index("users_db")
    assert _STEP_ORDER.index("user_state_tables") < _STEP_ORDER.index("users_db")
