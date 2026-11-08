"""存量 `wechat_bindings` 补登脚本回归（D-D 2026-11-08 用户裁决）。

锁定的契约（`scripts/backfill_wechat_bindings.py` + `wechat_direct/binding_sync.py`）：

1. 推导（纯函数 `plan_bindings`）：每人只用扫码人 wxid；角色取
   `user_active_characters` 现值，无激活 → `default`；同 wxid 同 user 同角色 →
   `noop`；归属或角色变化 → `update`；同人多 slot 同 wxid 去重；两个用户声称
   同一 wxid → 先到者写、后者 `conflict` 不写（不猜归属）。
2. 通道来源：`collect_scanner_ids()` 读的是 `channel_paths` 的**凭证真源**
   （各 slot `credentials.json` 的 `user_id`），空 id 剔除。
3. 脚本安全：默认 dry-run **绝不写库**，`--apply` 才写，且写入幂等
   （重复执行不产生第二行、不改 `bound_at`）。

不连真实微信、不碰生产库：通道根与 users.db 全部在 per-test 临时目录。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "backfill_wechat_bindings.py"

# users 库最小 schema（与 api/database.py 的 WechatBinding / UserActiveCharacter
# 列定义一一对应；脚本只走原生 SQL，不需要 ORM 建表）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS wechat_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    wxid VARCHAR(255) UNIQUE NOT NULL,
    nickname VARCHAR(255) NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    character_card_id VARCHAR(255) NOT NULL DEFAULT 'default',
    bound_at DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS user_active_characters (
    user_id INTEGER PRIMARY KEY,
    character_id VARCHAR(255) NOT NULL,
    updated_at DATETIME NOT NULL
);
"""


@pytest.fixture(scope="module")
def backfill():
    """按仓内既有做法（tests/test_knowledge_dir_hygiene.py）从路径加载脚本。"""
    spec = importlib.util.spec_from_file_location(
        "backfill_wechat_bindings", _SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def users_db(tmp_path):
    path = tmp_path / "users.db"
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    return path


def _rows(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                "SELECT user_id, wxid, character_card_id, bound_at FROM wechat_bindings "
                "ORDER BY id"
            )
        ]


# ── 1. 纯函数推导 ─────────────────────────────────────────


def test_plan_inserts_with_active_character(backfill):
    plans = backfill.binding_sync.plan_bindings(
        {7: ["wx_a@im.wechat"]},
        {7: "card_active"},
        {},
    )
    assert [(p.user_id, p.wxid, p.character_card_id, p.action) for p in plans] == [
        (7, "wx_a@im.wechat", "card_active", "insert")
    ]
    assert plans[0].writable is True


def test_plan_falls_back_to_default_without_active_character(backfill):
    plans = backfill.binding_sync.plan_bindings(
        {8: ["wx_b@im.wechat"]},
        {},  # 无 user_active_characters 行
        {},
    )
    assert len(plans) == 1
    assert plans[0].character_card_id == "default"
    assert plans[0].action == "insert"


def test_plan_noop_when_same_user_and_character(backfill):
    plans = backfill.binding_sync.plan_bindings(
        {7: ["wx_a@im.wechat"]},
        {7: "card_active"},
        {"wx_a@im.wechat": (7, "card_active")},
    )
    assert plans[0].action == "noop"
    assert plans[0].writable is False, "noop 不得写库（幂等：不打乱 bound_at）"


def test_plan_updates_when_character_changed(backfill):
    plans = backfill.binding_sync.plan_bindings(
        {7: ["wx_a@im.wechat"]},
        {7: "card_new"},
        {"wx_a@im.wechat": (7, "card_old")},
    )
    assert plans[0].action == "update"
    assert plans[0].character_card_id == "card_new"
    assert plans[0].previous_character_card_id == "card_old"
    assert plans[0].writable is True


def test_plan_updates_when_owner_changed(backfill):
    """同一 wxid 换了注册账号（重新扫码换绑）→ update 到新 owner。"""
    plans = backfill.binding_sync.plan_bindings(
        {9: ["wx_a@im.wechat"]},
        {},
        {"wx_a@im.wechat": (7, "default")},
    )
    assert plans[0].action == "update"
    assert plans[0].user_id == 9
    assert plans[0].previous_user_id == 7


def test_plan_dedupes_same_wxid_across_slots(backfill):
    """同一用户多 slot 落同一扫码人 wxid → 只产生一行计划。"""
    plans = backfill.binding_sync.plan_bindings(
        {7: ["wx_a@im.wechat", "wx_a@im.wechat", ""]},
        {7: "card_active"},
        {},
    )
    assert len(plans) == 1
    assert plans[0].wxid == "wx_a@im.wechat"


def test_plan_marks_conflict_when_two_users_claim_same_wxid(backfill):
    """数据异常：两账号同一 wxid → user_id 小的先写，后者记 conflict 不写。"""
    plans = backfill.binding_sync.plan_bindings(
        {9: ["wx_dup@im.wechat"], 7: ["wx_dup@im.wechat"]},
        {},
        {},
    )
    by_user = {p.user_id: p for p in plans}
    assert by_user[7].action == "insert", "user_id 升序先到者写"
    assert by_user[9].action == "conflict"
    assert by_user[9].writable is False, "conflict 不得写库（不猜归属）"


# ── 2. 通道凭证来源 ───────────────────────────────────────


def test_collect_scanner_ids_reads_channel_credentials(backfill):
    """从各 slot 的 credentials.json 取扫码人 id；空 id 剔除、重复去重。"""
    from wechat_direct import channel_paths

    # conftest 已把 sessions_root 沙箱化到 per-test 临时目录
    channel_paths.ensure_session_dir(21, 0)
    channel_paths.credentials_path(21, 0).write_text(
        json.dumps({"token": "t", "user_id": "wx_21@im.wechat"}), encoding="utf-8"
    )
    channel_paths.ensure_session_dir(21, 1)
    channel_paths.credentials_path(21, 1).write_text(
        json.dumps({"token": "t", "user_id": "wx_21@im.wechat"}), encoding="utf-8"
    )
    channel_paths.ensure_session_dir(22, 0)
    channel_paths.credentials_path(22, 0).write_text(
        json.dumps({"token": "t", "user_id": ""}), encoding="utf-8"
    )

    mapping = backfill.collect_scanner_ids()
    assert mapping == {21: ["wx_21@im.wechat"]}, "空 id 的用户不产生计划来源"


# ── 3. 脚本安全：默认 dry-run，--apply 才写且幂等 ─────────


def _seed_channel(user_id: int, wxid: str) -> None:
    from wechat_direct import channel_paths

    channel_paths.ensure_session_dir(user_id, 0)
    channel_paths.credentials_path(user_id, 0).write_text(
        json.dumps({"token": "t", "user_id": wxid}), encoding="utf-8"
    )


def _set_active_character(db_path: Path, user_id: int, character_id: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_active_characters "
            "(user_id, character_id, updated_at) VALUES (?, ?, '2026-11-08 00:00:00.000000')",
            (user_id, character_id),
        )
        conn.commit()


def test_cli_dry_run_does_not_write(backfill, users_db):
    _seed_channel(31, "wx_31@im.wechat")
    _set_active_character(users_db, 31, "card_31")

    assert backfill.main(["--db", str(users_db)]) == 0
    assert _rows(users_db) == [], "默认 dry-run 绝不写库"


def test_cli_apply_writes_row_and_is_idempotent(backfill, users_db):
    _seed_channel(31, "wx_31@im.wechat")
    _set_active_character(users_db, 31, "card_31")

    assert backfill.main(["--db", str(users_db), "--apply"]) == 0
    rows = _rows(users_db)
    assert len(rows) == 1
    assert rows[0]["user_id"] == 31
    assert rows[0]["wxid"] == "wx_31@im.wechat"
    assert rows[0]["character_card_id"] == "card_31"
    first_bound_at = rows[0]["bound_at"]

    # 再跑一次：noop，不产生第二行、不动 bound_at
    assert backfill.main(["--db", str(users_db), "--apply"]) == 0
    again = _rows(users_db)
    assert len(again) == 1
    assert again[0]["bound_at"] == first_bound_at


def test_cli_apply_updates_changed_character(backfill, users_db):
    _seed_channel(31, "wx_31@im.wechat")
    _set_active_character(users_db, 31, "card_31")
    assert backfill.main(["--db", str(users_db), "--apply"]) == 0

    _set_active_character(users_db, 31, "card_32")
    assert backfill.main(["--db", str(users_db), "--apply"]) == 0

    rows = _rows(users_db)
    assert len(rows) == 1
    assert rows[0]["character_card_id"] == "card_32", "换角后补登必须跟上激活角色"


def test_cli_reports_missing_db(backfill, tmp_path):
    missing = tmp_path / "nope.db"
    assert backfill.main(["--db", str(missing)]) == 2
