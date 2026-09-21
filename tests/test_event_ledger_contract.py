"""EventLedger 契约测试 — 任务包 AX 阶段 C 骨架。

突变验红设计：
- 剥离 session_key 过滤 → isolation 测试必须失败
- 拒绝空 session_key → 防脏账本
- replay 必须能还原 slots/tool/profile 事件
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shisi.agent_plane.event_ledger import (
    EVENT_MEMORY_WRITE,
    EVENT_PROFILE_CORRECT,
    EVENT_PROMPT_SLOTS,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EventLedger,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> EventLedger:
    return EventLedger(tmp_path / "agent_plane.db")


def test_schema_created(ledger: EventLedger) -> None:
    events = ledger.query(session_key="N:alice", limit=1)
    assert events == []


def test_append_requires_session_key(ledger: EventLedger) -> None:
    with pytest.raises(ValueError, match="session_key"):
        ledger.append(session_key="  ", event_type=EVENT_PROMPT_SLOTS)


def test_append_and_query_roundtrip(ledger: EventLedger) -> None:
    ev = ledger.append(
        session_key="N:alice",
        character_id="micai",
        event_type=EVENT_PROMPT_SLOTS,
        turn_id="t1",
        reply_id="r1",
        payload={"slots": {"memory_item_count": 2}},
    )
    rows = ledger.query(session_key="N:alice", turn_id="t1")
    assert len(rows) == 1
    assert rows[0].event_id == ev.event_id
    assert rows[0].payload["slots"]["memory_item_count"] == 2


def test_isolation_other_session_not_visible(ledger: EventLedger) -> None:
    ledger.append(
        session_key="N:alice",
        event_type=EVENT_MEMORY_WRITE,
        turn_id="t-shared-label",
        payload={"fact": "用户生日是腊月初一"},
    )
    # Bob 的同名 turn 不得看到 Alice 事件
    bob = ledger.query(session_key="N:bob", turn_id="t-shared-label")
    assert bob == []
    foreign = ledger.isolation_scan("N:bob", "t-shared-label")
    assert foreign == []
    alice = ledger.query(session_key="N:alice", turn_id="t-shared-label")
    assert len(alice) == 1


def test_replay_bundle_groups_ops(ledger: EventLedger) -> None:
    ledger.append_many(
        [
            {
                "session_key": "N:alice",
                "event_type": EVENT_PROMPT_SLOTS,
                "turn_id": "t9",
                "reply_id": "r9",
                "payload": {"slots": {"affinity_level": 3}},
            },
            {
                "session_key": "N:alice",
                "event_type": EVENT_PROFILE_CORRECT,
                "turn_id": "t9",
                "payload": {"field": "birthday", "new": "腊月初一", "cleared": "11月14"},
            },
            {
                "session_key": "N:alice",
                "event_type": EVENT_TOOL_CALL,
                "turn_id": "t9",
                "payload": {"name": "set_reminder"},
            },
            {
                "session_key": "N:alice",
                "event_type": EVENT_TOOL_RESULT,
                "turn_id": "t9",
                "payload": {"name": "set_reminder", "success": True, "id": 3},
            },
        ]
    )
    bundle = ledger.replay(session_key="N:alice", turn_id="t9")
    assert bundle.slots.get("affinity_level") == 3
    assert bundle.profile_ops and bundle.profile_ops[0]["field"] == "birthday"
    assert any(op.get("name") == "set_reminder" for op in bundle.tool_ops)
    summary = bundle.summary()
    assert summary["profile_ops"] == 1
    assert summary["tool_ops"] == 2


def test_replay_requires_turn_or_reply(ledger: EventLedger) -> None:
    with pytest.raises(ValueError, match="turn_id_or_reply_id"):
        ledger.replay(session_key="N:alice")


def test_count_by_session(ledger: EventLedger) -> None:
    ledger.append(session_key="N:alice", event_type=EVENT_MEMORY_WRITE)
    ledger.append(session_key="N:alice", event_type=EVENT_MEMORY_WRITE)
    ledger.append(session_key="N:bob", event_type=EVENT_MEMORY_WRITE)
    counts = ledger.count_by_session("N:alice")
    assert counts[EVENT_MEMORY_WRITE] == 2


# ── prune 类型豁免 + 投影窗口取最新（2026-09-22 修复锁定）────────────


def test_query_order_desc_takes_latest(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "ledger.db")
    for i in range(5):
        ledger.append(session_key="N:u", event_type="user_message", payload={"i": i})
    newest_first = ledger.query(session_key="N:u", limit=2, order="DESC")
    assert [e.payload["i"] for e in newest_first] == [4, 3]
    oldest_first = ledger.query(session_key="N:u", limit=2)
    assert [e.payload["i"] for e in oldest_first] == [0, 1]


def test_prune_preserves_profile_events(tmp_path: Path) -> None:
    """profile_update/correct 是画像唯一写权威——prune 必须豁免。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_PROFILE_UPDATE,
        EVENT_USER_MESSAGE,
    )

    ledger = EventLedger(tmp_path / "ledger.db")
    ledger.append(
        session_key="N:u",
        event_type=EVENT_PROFILE_UPDATE,
        payload={"birthday": "11-14"},
        created_at="2020-01-01T00:00:00+08:00",
    )
    ledger.append(
        session_key="N:u",
        event_type=EVENT_USER_MESSAGE,
        payload={"text": "旧消息"},
        created_at="2020-01-01T00:00:00+08:00",
    )
    removed = ledger.prune(retention_days=30, preserve_types=(EVENT_PROFILE_UPDATE,))
    assert removed == 1
    remaining_types = {e.event_type for e in ledger.query(session_key="N:u", limit=100)}
    assert remaining_types == {EVENT_PROFILE_UPDATE}
