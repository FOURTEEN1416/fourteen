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
