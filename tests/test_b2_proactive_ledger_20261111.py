"""B-2 承诺账本「读侧消费」—— agent_core 域红测。

B-2 只做「新增」：承诺账本从**只写**升级为**读侧消费**。本文件钉住 agent_core
侧的全部消费原语；``proactive/scheduler.py`` 的五个挂载点（去重键、每 sweep
一次 expire、投递后提取写账、上下文注入 + hooks 只剔由头、入站闭环）由主动线
owner 在同批接线——本文件用**等价的模块级流程**证明这些原语组合后语义正确。

硬钉子：

1. ``list_open_commitments`` 严格零写：不建表、不建库文件、不调 ``_ensure_ready``
   （旧实现会顺手建表，读接口带写副作用）；
2. ``close_commitment`` 幂等，且只在 ``open -> fulfilled`` 真转换时返回 ``True``；
3. ``_resolve_scope`` 缺失必须计 ``unattributable``——把「开关关着（不计数）」与
   「事件被丢（计数）」区分开；
4. ``open_thread`` 的 UPDATE 回填 ``character_id``（传空保持原值）；
5. ``from agent_core import replay`` 包级可见（B-1 docstring 的「不在本批范围」
   是漂移描述，本批已改正）。

⚠️ 沙箱：``tests/conftest.py`` 不在本批边界内，夹具必须自己 monkeypatch
``events._DB_PATH``，否则会写真实 ``data/sqlite.db``。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agent_core import commitment_gate as gate

USER_KEY = "2:o9cq80@im.wechat"
OTHER_KEY = "4:o9cq80other@im.wechat"


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
    monkeypatch.setenv("AGENT_CORE_EVENTS", "1")
    events, threads, commitments = _modules()
    monkeypatch.setattr(events, "_DB_PATH", tmp_path / "b2_ledger.db")
    _reset(events)
    yield events, threads, commitments
    _reset(events)


@pytest.fixture()
def store_off(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_CORE_EVENTS", raising=False)
    events, threads, commitments = _modules()
    monkeypatch.setattr(events, "_DB_PATH", tmp_path / "b2_ledger_off.db")
    _reset(events)
    yield events, threads, commitments
    _reset(events)


# ---------------------------------------------------------------------------
# 1. list_open_commitments 严格零写
# ---------------------------------------------------------------------------


def test_list_creates_no_db_file(store):
    events, _threads, commitments = store
    assert commitments.list_open_commitments(USER_KEY) == []
    assert not Path(events._DB_PATH).exists()


def test_list_missing_table_returns_empty_without_creating_tables(store):
    events, _threads, commitments = store
    conn = sqlite3.connect(str(events._DB_PATH))
    try:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
    finally:
        conn.close()

    assert commitments.list_open_commitments(USER_KEY) == []
    tables = {r["name"] for r in _query(events, "select name from sqlite_master where type='table'")}
    assert "agent_commitments" not in tables
    assert tables == {"unrelated"}


def test_list_never_calls_ensure_ready(store, monkeypatch):
    events, _threads, commitments = store
    assert events.ensure_schema() is True
    cid = commitments.add_commitment("叫我起床", user_key=USER_KEY, due_at="2026-11-12 06:00")

    def _forbidden():
        raise AssertionError("list_open_commitments 不得触发建表/写路径")

    monkeypatch.setattr(events, "_ensure_ready", _forbidden)
    rows = commitments.list_open_commitments(USER_KEY)
    assert [r["commitment_id"] for r in rows] == [cid]


def test_list_disabled_does_no_io(store_off):
    events, _threads, commitments = store_off
    assert commitments.list_open_commitments(USER_KEY) == []
    assert not Path(events._DB_PATH).exists()


# ---------------------------------------------------------------------------
# 2. close_commitment
# ---------------------------------------------------------------------------


def test_close_commitment_fulfils_once_and_stamps(store):
    events, _threads, commitments = store
    cid = commitments.add_commitment("叫你起床", user_key=USER_KEY)
    assert commitments.close_commitment(cid, reason="user_receipt") is True

    row = _query(events, "select * from agent_commitments where commitment_id=?", (cid,))[0]
    assert row["status"] == "fulfilled"
    assert row["fulfilled_at"]
    assert row["updated_at"]
    assert json.loads(row["meta_json"])["close_reason"] == "user_receipt"

    # 幂等：已 fulfilled → 本次没有状态转换
    assert commitments.close_commitment(cid) is False
    assert commitments.close_commitment(cid, reason="again") is False


def test_close_commitment_user_key_predicate_blocks_cross_user(store):
    """L1 复核修复：拿到别家的 commitment_id 也不能跨用户销账。"""
    _events, _threads, commitments = store
    cid = commitments.add_commitment("我帮你整理体检报告", kind="deliver", session_key=USER_KEY)

    assert commitments.close_commitment(cid, reason="user_receipt", user_key=OTHER_KEY) is False
    assert [c["commitment_id"] for c in commitments.list_open_commitments(USER_KEY)] == [cid]

    assert commitments.close_commitment(cid, reason="user_receipt", user_key=USER_KEY) is True
    assert commitments.list_open_commitments(USER_KEY) == []


def test_connector_inbound_anchor_wires_close_commitment():
    """M3 复核修复：契约 9.5 的锚点必须在 wechat_connector 入站侧真实存在。"""
    src = Path("wechat_direct/wechat_connector.py").read_text(encoding="utf-8")
    assert "commitment_closed_inbound" in src
    assert "list_open_commitments(str(session_key))" in src
    assert "close_commitment(" in src
    assert "hook_inbound_close_skipped" in src


def test_close_commitment_refuses_expired_unknown_and_empty(store):
    events, _threads, commitments = store
    stale = commitments.add_commitment("过期了", user_key=USER_KEY, due_at="2026-11-09 06:00")
    assert commitments.expire_commitments("2026-11-11 00:00:00", user_key=USER_KEY) == 1
    assert commitments.close_commitment(stale) is False
    assert commitments.close_commitment("not-a-real-id") is False
    assert commitments.close_commitment("") is False

    row = _query(events, "select status from agent_commitments where commitment_id=?", (stale,))[0]
    assert row["status"] == "expired"


def test_close_commitment_swallows_errors(store, monkeypatch):
    events, _threads, commitments = store
    cid = commitments.add_commitment("x", user_key=USER_KEY)

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(events, "connect", _boom)
    assert commitments.close_commitment(cid) is False


def test_close_commitment_disabled_is_false_without_io(store_off):
    events, _threads, commitments = store_off
    assert commitments.close_commitment("any-id") is False
    assert not Path(events._DB_PATH).exists()


# ---------------------------------------------------------------------------
# 3. unattributable 计数（区分「开关关着」与「事件被丢」）
# ---------------------------------------------------------------------------


def test_unattributable_is_counted_for_three_write_entries(store):
    events, threads, commitments = store
    base = events.failure_count("unattributable")
    assert base == 0

    assert events.record_event("user_msg", session_key="") == ""
    assert threads.open_thread() == ""
    assert commitments.add_commitment("提醒") == ""

    assert events.failure_count("unattributable") == 3
    assert events.failure_count("schema_locked") == 0  # 不是「没抢到建表锁」那类丢弃


def test_unattributable_is_not_counted_when_switch_is_off(store_off):
    events, threads, commitments = store_off
    base = events.failure_count("unattributable")
    assert events.record_event("user_msg", session_key="") == ""
    assert threads.open_thread() == ""
    assert commitments.add_commitment("提醒") == ""
    assert events.failure_count("unattributable") == base


# ---------------------------------------------------------------------------
# 4. open_thread character_id 回填
# ---------------------------------------------------------------------------


def test_open_thread_backfills_character_id_only_on_non_empty(store):
    events, threads, _commitments = store
    tid = threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="")
    row = _query(events, "select * from agent_threads where thread_id=?", (tid,))[0]
    assert row["character_id"] == ""

    assert threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="lin") == tid
    row = _query(events, "select * from agent_threads where thread_id=?", (tid,))[0]
    assert row["character_id"] == "lin"

    # 传空不回填（保持原值），不得把已解析的角色抹掉
    threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="")
    row = _query(events, "select * from agent_threads where thread_id=?", (tid,))[0]
    assert row["character_id"] == "lin"

    threads.open_thread(user_key=USER_KEY, session_key=USER_KEY, character_id="other")
    row = _query(events, "select * from agent_threads where thread_id=?", (tid,))[0]
    assert row["character_id"] == "other"


# ---------------------------------------------------------------------------
# 5. 包级导出
# ---------------------------------------------------------------------------


def test_replay_is_exported_from_package():
    import agent_core

    assert "replay" in agent_core.__all__
    assert hasattr(agent_core, "replay")
    from agent_core import replay

    assert callable(replay.replay_turn) and callable(replay.replay_session)


# ---------------------------------------------------------------------------
# 6. 消费流程原语（scheduler 五个挂载点的等价串联）
# ---------------------------------------------------------------------------


def test_dedupe_key_is_deterministic_per_minute_bucket(store):
    events, _threads, _commitments = store
    minute_a = f"proactive_decision:{USER_KEY}:2026-11-11 10:30"
    minute_b = f"proactive_decision:{USER_KEY}:2026-11-11 10:31"

    first = events.record_event("agent_msg", session_key=USER_KEY, dedupe_key=minute_a)
    again = events.record_event("agent_msg", session_key=USER_KEY, dedupe_key=minute_a)
    later = events.record_event("agent_msg", session_key=USER_KEY, dedupe_key=minute_b)
    assert first == again and later != first
    assert len(_query(events, "select * from agent_events")) == 2

    body = "早上好呀"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    manual_a = f"proactive_manual:{USER_KEY}:{digest}"
    manual_b = f"proactive_manual:{USER_KEY}:{digest}"
    assert manual_a == manual_b
    one = events.record_event("agent_msg", session_key=USER_KEY, dedupe_key=manual_a)
    two = events.record_event("agent_msg", session_key=USER_KEY, dedupe_key=manual_a)
    assert one == two


def test_expire_is_idempotent_per_sweep_and_global(store):
    events, _threads, commitments = store
    commitments.add_commitment("a", user_key=USER_KEY, due_at="2026-11-09 06:00")
    commitments.add_commitment("b", user_key=OTHER_KEY, due_at="2026-11-09 07:00")
    commitments.add_commitment("c", user_key=USER_KEY, due_at="2026-12-01 06:00")

    assert commitments.expire_commitments("2026-11-11 00:00:00", limit=500) == 2
    # 每个 sweep 只维护一次：第二次调用不再产生行转换
    assert commitments.expire_commitments("2026-11-11 00:00:00", limit=500) == 0
    assert len(commitments.list_open_commitments(USER_KEY)) == 1


def test_extraction_writes_ledger_and_list_returns_it(store):
    events, _threads, commitments = store
    text = "我帮你整理一下体检报告，明天发给你"
    assert gate.should_extract(text) is True

    due = gate.expires_at().strftime("%Y-%m-%d %H:%M:%S")
    cid = commitments.add_commitment(text[:500], kind="deliver", session_key=USER_KEY, due_at=due)
    assert cid

    rows = commitments.list_open_commitments(USER_KEY, session_key=USER_KEY)
    assert [r["commitment_id"] for r in rows] == [cid]
    assert rows[0]["kind"] == "deliver"
    assert rows[0]["due_at"] == due
    assert rows[0]["status"] == "open"


def test_expires_at_isoformat_is_dropped_by_normalizer(store):
    """⚠️ 契约风险记录（不是需求）：``expires_at().isoformat()`` 带 ``T``，

    ``add_commitment`` 的 ``_normalize_ts`` 只认 ``%Y-%m-%d %H:%M[:%S]``，由此
    ``due_at`` 落成空串 ⇒ 承诺**永不过期**。主动线 owner 传 due_at 时必须用
    ``strftime('%Y-%m-%d %H:%M:%S')``（或改传 expires_at）。本用例固定现行为，
    防止无声漂移。
    """
    _events, _threads, commitments = store
    assert "T" in gate.expires_at().isoformat()
    cid = commitments.add_commitment("承诺", kind="deliver", session_key=USER_KEY, due_at=gate.expires_at().isoformat())
    row = commitments.list_open_commitments(USER_KEY)[0]
    assert row["commitment_id"] == cid
    assert row["due_at"] == ""


def test_inbound_receipt_closes_open_commitment(store):
    events, _threads, commitments = store
    detail = "我帮你整理一下体检报告"
    cid = commitments.add_commitment(detail, kind="deliver", session_key=USER_KEY)

    opens = commitments.list_open_commitments(USER_KEY)
    assert [c["detail"] for c in opens] == [detail]

    user_text = "体检报告收到啦，谢谢你"
    to_close = [c for c in opens if gate.should_close(c["detail"], user_text)]
    assert [c["commitment_id"] for c in to_close] == [cid]
    assert commitments.close_commitment(to_close[0]["commitment_id"], reason="user_receipt") is True
    assert commitments.list_open_commitments(USER_KEY) == []


def test_unrelated_user_message_does_not_close(store):
    _events, _threads, commitments = store
    commitments.add_commitment("我帮你整理体检报告", kind="deliver", session_key=USER_KEY)
    opens = commitments.list_open_commitments(USER_KEY)
    assert [c for c in opens if gate.should_close(c["detail"], "今天好热啊")] == []
    # 判定在调用方：gate 不放行就不调 close_commitment —— 承诺保持 open。
    assert len(commitments.list_open_commitments(USER_KEY)) == 1


def test_hooks_only_drop_matching_hook_and_never_touch_body(store):
    _events, _threads, commitments = store
    commitments.add_commitment("我帮你整理体检报告", kind="deliver", session_key=USER_KEY)
    opens = commitments.list_open_commitments(USER_KEY)

    hooks = ["体检报告我整理好了", "今天天气不错"]
    body = "好的，我这就去整理体检报告"
    before = body

    kept = [h for h in hooks if not any(gate.references_commitment(c["detail"], h) for c in opens)]
    assert kept == ["今天天气不错"]
    assert body == before  # 只剔由头，正文逐字不变（不做投递拦截）


def test_empty_open_list_keeps_all_hooks_and_no_prompt_segment(store):
    _events, _threads, commitments = store
    opens = commitments.list_open_commitments(USER_KEY)
    assert opens == []
    hooks = ["今天天气不错", "体检报告我整理好了"]
    kept = [h for h in hooks if not any(gate.references_commitment(c["detail"], h) for c in opens)]
    assert kept == hooks
    # 注入段只在非空时出现
    segment = ""
    if opens:
        segment = "你还没兑现的承诺（本轮若不能真的交付就别提；提了就必须交付）"
    assert segment == ""
