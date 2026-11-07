"""P8 用户自填自我介绍回归（2026-11-07 痛点批）。

用户裁决：「心理画像其实属于记忆的一部分」「用户可以填写属于自己的画像
（自我介绍）」。本文件锁四件事：
1. 自述走 EventLedger 投影（唯一写权威），标量覆盖与清空语义正确；
2. 渲染进画像槽，且**优先展示**（本人声明 > 模型推测）；
3. 只有自述、没有其他字段时画像块也要渲染（不能被空判拦掉）；
4. 旧库 `user_profile` 自动补 `self_intro` 列（CREATE IF NOT EXISTS 不改旧表）。
"""

from __future__ import annotations

import sqlite3
import sys

sys.path.insert(0, ".")

from shisi.agent_plane.profile_projection import (
    _empty_profile,
    apply_profile_ops_to_state,
)
from shisi.agent_plane.runtime import format_profile_prompt_block
from shisi.memory.legacy.user_profile import UserProfileStore


def test_self_intro_applies_to_projection() -> None:
    state = _empty_profile()
    state = apply_profile_ops_to_state(state, {"self_intro": "我是做后端的，喜欢猫"})

    assert state["self_intro"] == "我是做后端的，喜欢猫"


def test_self_intro_can_be_cleared_by_correct_event() -> None:
    state = {"self_intro": "旧介绍"}
    state = apply_profile_ops_to_state(state, {"_event": "profile_correct", "self_intro": ""})

    assert state["self_intro"] == ""


def test_prompt_block_renders_self_intro_with_priority() -> None:
    block = format_profile_prompt_block(
        {"self_intro": "我是默默，做安全的，讨厌被叫夏夏", "nickname": "默默"}
    )

    assert "用户自述" in block
    # 自述排在称呼之前（本人声明优先）
    assert block.index("用户自述") < block.index("称呼")


def test_prompt_block_renders_with_only_self_intro() -> None:
    block = format_profile_prompt_block({"self_intro": "我是默默"})

    assert block.startswith("# 用户画像")
    assert "我是默默" in block


def test_legacy_store_persists_self_intro(tmp_path) -> None:
    store = UserProfileStore(tmp_path / "profile.db")

    store.upsert("2:wx", self_intro="我喜欢安静")
    row = store.get("2:wx")
    assert row["self_intro"] == "我喜欢安静"
    assert "用户自述" in store.to_prompt_block("2:wx")


def test_legacy_schema_migration_adds_column(tmp_path) -> None:
    db = tmp_path / "old.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            CREATE TABLE user_profile (
                user_key TEXT PRIMARY KEY,
                nickname TEXT DEFAULT '',
                birthday TEXT DEFAULT '',
                occupation TEXT DEFAULT '',
                location TEXT DEFAULT '',
                preferences TEXT DEFAULT '[]',
                commitments TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO user_profile (user_key, nickname) VALUES ('2:wx', '默默')"
        )

    store = UserProfileStore(str(db))
    store.upsert("2:wx", self_intro="我是默默")

    with sqlite3.connect(str(db)) as conn:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(user_profile)")}
        value = conn.execute(
            "SELECT self_intro FROM user_profile WHERE user_key='2:wx'"
        ).fetchone()[0]
    assert "self_intro" in cols
    assert value == "我是默默"
