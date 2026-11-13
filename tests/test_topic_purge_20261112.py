"""原子 C2：topic_meta 清场 + 生命周期 purge（2026-11-12）。

钉死：
- ``purge_expired`` 三表同事务：state/plan 按 ``expires_at < now``，meta 按
  ``last_llm_at < cutoff`` 且无未过期 state/plan；
- ``enabled=false`` 且 ``force=false`` → 0（不连库、不误删）；
- ``purge_owner(session_keys)`` 按会话键清空三表、返回总行数，空列表/空键
  返回 0 且不误删他人；
- 生命周期 ``_topic_state_purge/_topic_state_verify`` 幂等（临时库）。
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from utils import topic_state as ts

FIXED = datetime(2026, 11, 12, 10, 0, 0)


@pytest.fixture()
def topic_env(tmp_path, monkeypatch):
    db = tmp_path / "topic_state.db"
    cfg = tmp_path / "scheduler_config.json"
    cfg.write_text(json.dumps({"topic_state": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(ts, "_DB_PATH", db)
    monkeypatch.setattr(ts, "_CONFIG_PATH", cfg)
    with closing(ts._connect(ensure_schema=True)) as conn:
        conn.execute("SELECT 1")  # 触发表创建
    return {"db": db, "cfg": cfg}


def _set_enabled(cfg_path, enabled: bool) -> None:
    cfg_path.write_text(
        json.dumps({"topic_state": {"enabled": enabled}}), encoding="utf-8"
    )


def _seed_state(conn, session_key: str, character_id: str, *, expires_at: datetime) -> None:
    conn.execute(
        "INSERT INTO topic_state "
        "(session_key, character_id, topic_id, title, summary, status, salience, "
        " mention_count, turn_count, anchors, idle_turns, recall_count, last_recalled_at, "
        " last_active_at, created_at, updated_at, expires_at) "
        "VALUES (?, ?, 't1', '话题', '', 'active', 0.5, 1, 1, '[]', 0, 0, '', ?, ?, ?, ?)",
        (
            session_key,
            character_id,
            ts._ts(FIXED),
            ts._ts(FIXED),
            ts._ts(FIXED),
            ts._ts(expires_at),
        ),
    )


def _seed_plan(conn, session_key: str, character_id: str, *, expires_at: datetime) -> None:
    conn.execute(
        "INSERT INTO thread_plan "
        "(session_key, character_id, topic, intent, planned_at, next_at, expires_at, consumed_at) "
        "VALUES (?, ?, '话题', '意图', ?, ?, ?, NULL)",
        (
            session_key,
            character_id,
            ts._ts(FIXED),
            ts._ts(FIXED),
            ts._ts(expires_at),
        ),
    )


def _seed_meta(conn, session_key: str, character_id: str, *, last_llm_at: datetime) -> None:
    conn.execute(
        "INSERT INTO topic_meta "
        "(session_key, character_id, turns_since_llm, llm_calls, last_llm_at, last_turn_id) "
        "VALUES (?, ?, 0, 0, ?, '')",
        (session_key, character_id, ts._ts(last_llm_at)),
    )


# ---------------------------------------------------------------------------
# purge_expired
# ---------------------------------------------------------------------------


def test_purge_expired_deletes_orphan_meta_and_keeps_active(topic_env):
    old = FIXED - timedelta(days=8)
    future = FIXED + timedelta(days=3)
    with closing(ts._connect(ensure_schema=True)) as conn:
        # A：未过期 state + 老 meta → meta 必须保留（有活跃话题）
        _seed_state(conn, "1:active", "charA", expires_at=future)
        _seed_meta(conn, "1:active", "charA", last_llm_at=old)
        # B：过期 state + 老 meta → 两者都删
        _seed_state(conn, "1:expired", "charB", expires_at=old)
        _seed_meta(conn, "1:expired", "charB", last_llm_at=old)
        # C：过期 plan + 老 meta → 两者都删
        _seed_plan(conn, "1:plan", "charC", expires_at=old)
        _seed_meta(conn, "1:plan", "charC", last_llm_at=old)
        # D：孤儿 meta → 删
        _seed_meta(conn, "1:orphan", "charD", last_llm_at=old)
        # E：孤儿但 last_llm_at 新鲜 → 保留
        _seed_meta(conn, "1:recent", "charE", last_llm_at=FIXED - timedelta(days=1))
        # F：未过期 plan + 老 meta → meta 保留
        _seed_plan(conn, "1:planned", "charF", expires_at=future)
        _seed_meta(conn, "1:planned", "charF", last_llm_at=old)

    # 1 expired state + 1 expired plan + 3 old meta（B/C/D）
    assert ts.purge_expired(now=FIXED) == 5

    with closing(ts._connect(ensure_schema=False)) as conn:
        state_keys = {row[0] for row in conn.execute("SELECT session_key FROM topic_state")}
        plan_keys = {row[0] for row in conn.execute("SELECT session_key FROM thread_plan")}
        meta_keys = {row[0] for row in conn.execute("SELECT session_key FROM topic_meta")}
    assert state_keys == {"1:active"}
    assert plan_keys == {"1:planned"}
    assert meta_keys == {"1:active", "1:recent", "1:planned"}


def test_purge_expired_disabled_returns_zero_but_force_runs(topic_env):
    _set_enabled(topic_env["cfg"], False)
    old = FIXED - timedelta(days=8)
    with closing(ts._connect(ensure_schema=True)) as conn:
        _seed_state(conn, "1:expired", "charA", expires_at=old)
        _seed_meta(conn, "1:expired", "charA", last_llm_at=old)

    assert ts.purge_expired(now=FIXED) == 0
    with closing(ts._connect(ensure_schema=False)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_state").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM topic_meta").fetchone()[0] == 1

    assert ts.purge_expired(now=FIXED, force=True) == 2
    with closing(ts._connect(ensure_schema=False)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_state").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM topic_meta").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# purge_owner
# ---------------------------------------------------------------------------


def test_purge_owner_clears_three_tables_and_isolates_others(topic_env):
    old = FIXED + timedelta(days=3)  # 不参与 purge_expired，纯粹验证按会话键删
    with closing(ts._connect(ensure_schema=True)) as conn:
        for key in ("1:wx_a", "2:wx_b"):
            _seed_state(conn, key, "charX", expires_at=old)
            _seed_plan(conn, key, "charX", expires_at=old)
            _seed_meta(conn, key, "charX", last_llm_at=FIXED)

    assert ts.purge_owner(["1:wx_a"]) == 3
    assert ts.count_owner(["1:wx_a"]) == 0
    assert ts.count_owner(["2:wx_b"]) == 3
    with closing(ts._connect(ensure_schema=False)) as conn:
        assert {r[0] for r in conn.execute("SELECT session_key FROM topic_state")} == {"2:wx_b"}
        assert {r[0] for r in conn.execute("SELECT session_key FROM thread_plan")} == {"2:wx_b"}
        assert {r[0] for r in conn.execute("SELECT session_key FROM topic_meta")} == {"2:wx_b"}

    # 空列表 / 空白键：0 且不误删他人。
    assert ts.purge_owner([]) == 0
    assert ts.purge_owner([""]) == 0
    assert ts.purge_owner(["", "   "]) == 0
    assert ts.count_owner(["2:wx_b"]) == 3


# ---------------------------------------------------------------------------
# 生命周期接入
# ---------------------------------------------------------------------------


def test_lifecycle_topic_purge_verify_idempotent(topic_env):
    from api.lifecycle import AccountScope, _topic_state_purge, _topic_state_verify

    scope = AccountScope(
        user_id=1,
        wxids=["wx_a@im.wechat"],
        session_keys=["1:wx_a@im.wechat", "1:web:deadbeef"],
        sqlite_db=topic_env["db"],
    )
    future = FIXED + timedelta(days=3)
    with closing(ts._connect(ensure_schema=True)) as conn:
        # 显式会话键
        for key in ("wx_a@im.wechat", "1:wx_a@im.wechat", "1:web:deadbeef"):
            _seed_state(conn, key, "charA", expires_at=future)
            _seed_plan(conn, key, "charA", expires_at=future)
            _seed_meta(conn, key, "charA", last_llm_at=FIXED)
        # 未被 scope.session_keys 枚举到的 web 会话（owner 前缀兜底必须清掉）
        _seed_state(conn, "1:web:unknownhex", "charA", expires_at=future)
        _seed_plan(conn, "1:web:unknownhex", "charA", expires_at=future)
        _seed_meta(conn, "1:web:unknownhex", "charA", last_llm_at=FIXED)
        for key in ("wx_b@im.wechat", "2:wx_b@im.wechat"):
            _seed_state(conn, key, "charB", expires_at=future)
            _seed_plan(conn, key, "charB", expires_at=future)
            _seed_meta(conn, key, "charB", last_llm_at=FIXED)

    assert _topic_state_purge(scope) == {"topic_rows": 12}
    assert _topic_state_verify(scope) == {"topic_rows": 0}
    # 幂等：再跑一次不报错、无新删除，且他人数据不受影响。
    assert _topic_state_purge(scope) == {"topic_rows": 0}
    assert _topic_state_verify(scope) == {"topic_rows": 0}
    assert ts.count_owner(["wx_b@im.wechat", "2:wx_b@im.wechat"]) == 6


def test_lifecycle_topic_preview_uses_verify(topic_env):
    """preview 走 step.verify（只读计数），不得误删。"""
    from api.lifecycle import _OWNER_STEPS, _STEP_ORDER, AccountScope, _topic_state_verify

    scope = AccountScope(
        user_id=1, session_keys=["1:wx_a"], wxids=[], sqlite_db=topic_env["db"],
    )
    with closing(ts._connect(ensure_schema=True)) as conn:
        _seed_state(conn, "1:wx_a", "charA", expires_at=FIXED + timedelta(days=3))
        _seed_plan(conn, "1:wx_a", "charA", expires_at=FIXED + timedelta(days=3))
        _seed_meta(conn, "1:wx_a", "charA", last_llm_at=FIXED)

    assert "topic_state" in _STEP_ORDER
    assert _OWNER_STEPS["topic_state"].verify is _topic_state_verify
    assert _topic_state_verify(scope) == {"topic_rows": 3}
    assert ts.count_owner(["1:wx_a"]) == 3


def test_lifecycle_topic_uses_scope_sqlite_db_not_global(topic_env, tmp_path, monkeypatch):
    """2026-11-12 复核修复：生命周期必须清 scope 指定的库，不能误删全局库。"""
    from api.lifecycle import AccountScope, _topic_state_purge, _topic_state_verify

    scope_db = topic_env["db"]
    with closing(ts._connect(ensure_schema=True, db_path=scope_db)) as conn:
        _seed_state(conn, "1:wx_a", "charA", expires_at=FIXED + timedelta(days=3))
        _seed_plan(conn, "1:wx_a", "charA", expires_at=FIXED + timedelta(days=3))
        _seed_meta(conn, "1:wx_a", "charA", last_llm_at=FIXED)

    # 全局库指向另一个空库：若实现忽略 scope.sqlite_db，删的会是这个空库，
    # 目标库三行仍在 → 断言失败。
    monkeypatch.setattr(ts, "_DB_PATH", tmp_path / "global-empty.db")
    scope = AccountScope(user_id=1, session_keys=["1:wx_a"], wxids=[], sqlite_db=scope_db)

    assert _topic_state_purge(scope) == {"topic_rows": 3}
    assert _topic_state_verify(scope) == {"topic_rows": 0}


def test_purge_count_owner_raise_on_db_error(tmp_path):
    """2026-11-12 复核修复：DB 异常不得被吞成 0（verify 会谎报已清干净）。"""
    import sqlite3

    bad_path = tmp_path  # 目录不能作为 sqlite 文件
    with pytest.raises(sqlite3.OperationalError):
        ts.count_owner(["1:wx_a"], db_path=bad_path)
    with pytest.raises(sqlite3.OperationalError):
        ts.purge_owner(["1:wx_a"], db_path=bad_path)
