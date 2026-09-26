"""画像投影契约 — 写走 ledger、profile 仅投影；多用户 session 隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest

from shisi.agent_plane.event_ledger import EventLedger
from shisi.agent_plane.profile_projection import (
    project_profile,
    seed_profile_baseline,
    write_profile_event,
)


@pytest.fixture()
def ledger(tmp_path: Path) -> EventLedger:
    return EventLedger(tmp_path / "agent_plane.db")


def test_write_then_project_scalar(ledger: EventLedger) -> None:
    sk = "N:userA"
    proj = write_profile_event(
        ledger,
        session_key=sk,
        payload={"birthday": "腊月初一", "nickname": "彩儿"},
    )
    assert proj["birthday"] == "腊月初一"
    assert proj["nickname"] == "彩儿"
    assert project_profile(ledger, sk)["birthday"] == "腊月初一"


def test_late_background_result_cannot_overwrite_newer_source(ledger: EventLedger):
    write_profile_event(ledger, session_key="7:peer", payload={"birthday": "新生日", "_source_chat_id": 12})
    result = write_profile_event(ledger, session_key="7:peer", payload={"birthday": "旧生日", "location": "昆明", "_source_chat_id": 8})
    assert result["birthday"] == "新生日"
    assert result["location"] == "昆明"


def test_correction_clears_old_value(ledger: EventLedger) -> None:
    sk = "N:userA"
    write_profile_event(ledger, session_key=sk, payload={"birthday": "11月14"})
    proj = write_profile_event(
        ledger,
        session_key=sk,
        payload={"birthday": "腊月初一", "clear_birthday": False},
        correct=True,
    )
    assert proj["birthday"] == "腊月初一"

    # 否认清空
    proj2 = write_profile_event(
        ledger,
        session_key=sk,
        payload={"clear_birthday": True, "birthday": ""},
        correct=True,
    )
    assert proj2["birthday"] == ""


def test_preferences_add_remove_cap(ledger: EventLedger) -> None:
    sk = "N:userA"
    proj = write_profile_event(
        ledger,
        session_key=sk,
        payload={"preferences_add": ["安静", "猫", "安静"]},
    )
    assert proj["preferences"] == ["安静", "猫"]
    proj = write_profile_event(
        ledger,
        session_key=sk,
        payload={"preferences_remove": ["猫"], "preferences_add": ["雨"]},
    )
    assert "猫" not in proj["preferences"]
    assert "雨" in proj["preferences"]


def test_session_isolation_projection(ledger: EventLedger) -> None:
    write_profile_event(ledger, session_key="N:userA", payload={"birthday": "腊月初一"})
    b = project_profile(ledger, "N:userB")
    assert b["birthday"] == ""
    assert b["user_key"] == "N:userB"


def test_seed_baseline_projects(ledger: EventLedger) -> None:
    sk = "N:user4"
    proj = seed_profile_baseline(
        ledger,
        sk,
        {"birthday": "腊月初一", "occupation": "上班", "preferences": ["安静"]},
    )
    assert proj["birthday"] == "腊月初一"
    assert proj["occupation"] == "上班"
    assert proj["preferences"] == ["安静"]
    # 不污染他人
    assert project_profile(ledger, "N:user2")["birthday"] == ""


def test_interleaved_updates_and_corrections_follow_real_write_order(ledger: EventLedger):
    first = ledger.append(session_key="s", event_type="profile_update", payload={"birthday": "1月1日"}, created_at="2026-01-01T00:00:00+08:00")
    ledger.append(session_key="s", event_type="profile_correct", payload={"clear_birthday": True}, created_at=first.created_at)
    last = ledger.append(session_key="s", event_type="profile_update", payload={"birthday": "2月2日"}, created_at=first.created_at)
    assert project_profile(ledger, "s")["birthday"] == "2月2日"
    rows = ledger.query(session_key="s")
    assert rows[0].id == first.id < rows[1].id < last.id == rows[2].id


def test_projection_keeps_old_unchanged_fields_beyond_page_boundary(ledger: EventLedger):
    ledger.append(session_key="s", event_type="profile_update", payload={"birthday": "1月1日", "occupation": "设计师"})
    for index in range(9):
        ledger.append(session_key="s", event_type="profile_update", payload={"nickname": f"称呼{index}"})
    state = project_profile(ledger, "s", limit=3)
    assert (state["birthday"], state["occupation"], state["nickname"]) == ("1月1日", "设计师", "称呼8")


def test_profile_apply_does_not_mutate_previous_state():
    from shisi.agent_plane.profile_projection import apply_profile_ops_to_state

    before = {"preferences": ["猫"], "commitments": []}
    after = apply_profile_ops_to_state(before, {"preferences_add": ["狗"]})
    assert before["preferences"] == ["猫"]
    assert after["preferences"] == ["猫", "狗"]


def test_projection_window_takes_latest_events(ledger: EventLedger) -> None:
    """画像事件超限后投影必须反映**最新**状态（旧实现取最早 limit 条，
    事件累计超限后画像卡死旧值）。用 limit=5 + 8 次更名驱动窗口溢出。"""
    for i in range(8):
        write_profile_event(
            ledger, session_key="N:win", payload={"nickname": f"名字{i}"}
        )
    proj = project_profile(ledger, "N:win", limit=5)
    assert proj["nickname"] == "名字7"
