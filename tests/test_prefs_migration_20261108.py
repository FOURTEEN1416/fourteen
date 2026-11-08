"""旧 per-user 覆盖表 → my-settings 快照的迁移脚本（2026-11-08 D-A）。

被测对象：`scripts/migrate_per_user_prefs_to_my_settings.py` 的**纯映射函数**，
以及 `load_legacy` / `apply_plan` 在一条合成 sqlite 上的行为。

锁定语义：
- 只迁旧表里**真的出现过**的键，逐键宽松过滤（脏值丢弃、不让整条迁移失败）；
- 目标角色 = 当前激活角色（`user_active_characters` → 绑定卡 → 内置 `default`）；
- 目标快照已存在时默认跳过（幂等，不覆盖新界面里的改动），`--overwrite` 才写；
- `user_key` 不是 `N:wxid` 形态 → 跳过（不猜用户）。
"""

from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, ".")

from scripts.migrate_per_user_prefs_to_my_settings import (  # noqa: E402
    DEFAULT_CHARACTER_ID,
    active_character_for,
    apply_plan,
    build_plan,
    combine_patches,
    humanize_patch,
    load_legacy,
    proactive_patch,
    purge_legacy,
    reply_patch,
    session_key_for,
)

_ALICE_KEY = "2:alice_wx"
_BOB_KEY = "3:bob_wx"


# ── 纯映射 ────────────────────────────────────────────────


def test_reply_patch_only_accepts_two_modes():
    assert reply_patch("novel") == {"reply_mode": "novel"}
    assert reply_patch("immersive") == {"reply_mode": "immersive"}
    assert reply_patch("poetry") == {}
    assert reply_patch(None) == {}
    assert reply_patch("") == {}


def test_humanize_patch_filters_unknown_and_invalid_keys():
    patch = humanize_patch(
        {
            "per_char_ms": 70,          # 合法
            "delay_mode": "fixed",      # 合法
            "per_char_ms_typo": 1,      # 未知键
            "fixed_ms": -5,             # 越界
            "max_daily_messages": 5,    # 别的组的键（不得串组）
            "reply_mode": "novel",      # 别的组的键
        }
    )
    assert patch == {"per_char_ms": 70, "delay_mode": "fixed"}


def test_humanize_patch_tolerates_bad_json_and_non_dict():
    assert humanize_patch("{not json") == {}
    assert humanize_patch(None) == {}
    assert humanize_patch([1, 2]) == {}
    # 字符串形态的合法 JSON 也接受（旧库列是 TEXT）
    assert humanize_patch('{"typo_rate": 12}') == {"typo_rate": 12}


def test_proactive_patch_drops_tombstone_marker():
    patch = proactive_patch(
        {"paused": True, "threshold": 5.5, "__revert__": ["threshold"], "nope": 1}
    )
    assert patch == {"paused": True, "threshold": 5.5}


def test_combine_patches_later_wins_and_ignores_empty():
    assert combine_patches({"a": 1}, {}, {"a": 2, "b": 3}) == {"a": 2, "b": 3}
    assert combine_patches({}, {}) == {}


def test_active_character_prefers_active_row_then_binding_then_default():
    active = [(2, "old_card"), (2, "new_card")]
    bindings = [(2, "alice_wx", "bind_card"), (3, "bob_wx", "")]
    assert active_character_for(2, active, bindings) == "new_card"
    assert active_character_for(3, [], bindings) == DEFAULT_CHARACTER_ID
    assert active_character_for(4, [], []) == DEFAULT_CHARACTER_ID


def test_session_key_uses_latest_binding_and_never_guesses():
    bindings = [(2, "old_wx", ""), (2, "new_wx", ""), (3, "", "")]
    assert session_key_for(2, bindings) == "2:new_wx"
    assert session_key_for(3, bindings) == ""
    assert session_key_for(9, bindings) == ""


def test_build_plan_merges_three_sources_per_user():
    plan = build_plan(
        [( _ALICE_KEY, "novel")],
        [(_ALICE_KEY, json.dumps({"per_char_ms": 70}))],
        [(_ALICE_KEY, json.dumps({"paused": True, "threshold": 6.0}))],
        [(2, "card_a")],
        [(2, "alice_wx", "card_b")],
    )
    assert len(plan) == 1
    item = plan[0]
    assert item["user_key"] == _ALICE_KEY
    assert item["user_id"] == 2
    assert item["character_id"] == "card_a"  # 激活行优先于绑定卡
    assert item["patch"] == {
        "reply_mode": "novel",
        "per_char_ms": 70,
        "paused": True,
        "threshold": 6.0,
    }
    assert item["sources"] == [
        "user_humanize", "user_proactive_config", "user_reply_mode",
    ]


def test_build_plan_flags_unnormalized_keys():
    plan = build_plan([("alice", "novel")], [], [], [], [])
    assert plan[0]["user_id"] is None
    assert plan[0]["character_id"] == ""


# ── 合成库：load_legacy / apply_plan / purge_legacy ─────────


def _make_legacy_db(path):
    conn = sqlite3.connect(str(path))
    try:
        with conn:
            conn.execute("CREATE TABLE user_reply_mode (user_key TEXT PRIMARY KEY, mode TEXT)")
            conn.execute("CREATE TABLE user_humanize (user_key TEXT PRIMARY KEY, config TEXT)")
            conn.execute("CREATE TABLE user_proactive_config (user_key TEXT PRIMARY KEY, config TEXT)")
            conn.execute(
                "CREATE TABLE user_active_characters (id INTEGER PRIMARY KEY, user_id INTEGER, character_id TEXT)"
            )
            conn.execute(
                "CREATE TABLE wechat_bindings (id INTEGER PRIMARY KEY, user_id INTEGER, wxid TEXT, "
                "character_card_id TEXT, bound_at TEXT)"
            )
            conn.execute("INSERT INTO user_reply_mode VALUES (?, 'novel')", (_ALICE_KEY,))
            conn.execute(
                "INSERT INTO user_humanize VALUES (?, ?)",
                (_ALICE_KEY, json.dumps({"per_char_ms": 70})),
            )
            conn.execute(
                "INSERT INTO user_proactive_config VALUES (?, ?)",
                (_ALICE_KEY, json.dumps({"paused": True})),
            )
            conn.execute("INSERT INTO user_active_characters VALUES (1, 2, 'card_a')")
            conn.execute("INSERT INTO wechat_bindings VALUES (1, 2, 'alice_wx', 'card_b', '2026-11-01')")
    finally:
        conn.close()


def test_load_legacy_reads_three_tables_and_tolerates_missing_ones(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    legacy = load_legacy(db)
    assert legacy["reply"] == [(_ALICE_KEY, "novel")]
    assert legacy["humanize"] and json.loads(legacy["humanize"][0][1]) == {"per_char_ms": 70}
    assert legacy["active"] == [(2, "card_a")]

    empty = load_legacy(tmp_path / "missing.db")
    assert empty == {
        "reply": [], "humanize": [], "proactive": [], "active": [], "bindings": [],
    }


def test_apply_plan_writes_snapshot_then_is_idempotent(tmp_path):
    from utils import character_settings as cs

    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    legacy = load_legacy(db)
    plan = build_plan(
        legacy["reply"], legacy["humanize"], legacy["proactive"],
        legacy["active"], legacy["bindings"],
    )

    applied, skipped = apply_plan(plan)
    assert len(applied) == 1 and skipped == []
    snapshot = cs.read(_ALICE_KEY, "card_a")
    assert snapshot["reply_mode"] == "novel"
    assert snapshot["per_char_ms"] == 70
    assert snapshot["paused"] is True

    # 二次执行：目标快照已存在 → 跳过（不覆盖新界面里的改动）
    applied2, skipped2 = apply_plan(plan)
    assert applied2 == []
    assert len(skipped2) == 1
    assert "已存在" in skipped2[0]["reason"]

    # --overwrite 才覆盖
    applied3, skipped3 = apply_plan(plan, overwrite=True)
    assert len(applied3) == 1 and skipped3 == []


def test_apply_plan_marks_unresolvable_keys_as_skipped():
    plan = build_plan([("alice", "novel")], [], [], [], [])
    applied, skipped = apply_plan(plan)
    assert applied == []
    assert "无法定位用户" in skipped[0]["reason"]


def test_purge_legacy_removes_only_given_keys(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    with sqlite3.connect(str(db)) as conn, conn:
        conn.execute("INSERT INTO user_reply_mode VALUES (?, 'immersive')", (_BOB_KEY,))

    removed = purge_legacy(db, [_ALICE_KEY])
    assert removed == 3  # 三张表各一行

    legacy = load_legacy(db)
    assert legacy["reply"] == [(_BOB_KEY, "immersive")]
