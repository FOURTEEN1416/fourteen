"""公开角色卡的「每人一份」私人覆盖（2026-11-07 用户裁决）。

用户原话：「直接就是默认每一个人都可以拥有所有的公开模版，并且能够对该模型
进行自定义修改，不然太复杂而且冗余了，还会多出一个页面来」。

因此公开卡**不再要求克隆**：任何登录用户都可以直接打开、激活、修改；修改写入
本表（`user_character_overrides`），只影响该用户自己。共享卡文件保持原样，
不复制卡、不产生重复条目，也不新增页面。

读侧合并点：
- API：`GET /characters`、`GET /characters/{id}`、`GET /characters/{id}/persona`
- 聊天：orchestrator 在卡片加载后按 `(user_id, character_id)` 合并，
  保证「改了人设，聊天里马上生效」。

隔离：`user_id` 为注册用户主键；不同用户对同一张公开卡的覆盖互不可见。
管理员写公共卡仍走原路径（直接改共享卡文件 + 影响所有人），普通用户永远走覆盖。
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("character_overrides")

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"
TABLE = "user_character_overrides"

#: 普通用户可自定义的字段（与角色设置页「基础」区一致）。
#: 不在白名单里的键一律忽略——防止覆盖把归属/激活等内部字段改掉。
ALLOWED_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "personality",
    "speaking_style",
    "core_anchors",
    "tags",
)


def _conn() -> sqlite3.Connection:
    path = Path(_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_id      INTEGER NOT NULL,
            character_id TEXT NOT NULL,
            patch        TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, character_id)
        )
        """
    )


def _sanitize_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in ALLOWED_FIELDS:
            continue
        if key in ("personality", "speaking_style"):
            if isinstance(value, dict):
                out[key] = {str(k): v for k, v in value.items()}
        elif key in ("core_anchors", "tags"):
            if isinstance(value, list):
                out[key] = [str(x) for x in value if str(x).strip()]
        else:
            if isinstance(value, str):
                out[key] = value
    return out


def read(user_id: int, character_id: str) -> dict[str, Any]:
    """读该用户对这张卡的私人覆盖（无 → 空 dict；坏行 → 空 dict，不抛异常）。"""
    cid = str(character_id or "").strip()
    if not cid or not user_id:
        return {}
    try:
        with closing(_conn()) as conn, conn:
            _ensure_table(conn)
            row = conn.execute(
                f"SELECT patch FROM {TABLE} WHERE user_id = ? AND character_id = ?",
                (int(user_id), cid),
            ).fetchone()
        if not row:
            return {}
        data = json.loads(row[0])
        return _sanitize_patch(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取角色覆盖失败 user=%s ch=%s: %s", user_id, cid, exc)
        return {}


def apply(user_id: int | None, character_id: str, card: dict[str, Any] | None) -> dict[str, Any] | None:
    """把私人覆盖合并进卡片副本（不改原 dict）；无覆盖原样返回副本。"""
    if not isinstance(card, dict):
        return card
    merged = copy.deepcopy(card)
    if not user_id:
        return merged
    patch = read(int(user_id), character_id)
    if not patch:
        return merged
    for key, value in patch.items():
        if key == "speaking_style" and isinstance(value, dict):
            base = merged.get("speaking_style")
            merged["speaking_style"] = {**(base if isinstance(base, dict) else {}), **value}
        elif key == "personality" and isinstance(value, dict):
            base = merged.get("personality")
            merged["personality"] = {**(base if isinstance(base, dict) else {}), **value}
        else:
            merged[key] = value
    return merged


def write(user_id: int, character_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入私人覆盖（只允许白名单字段）。返回合并后的完整覆盖。"""
    cid = str(character_id or "").strip()
    if not cid or not user_id:
        raise ValueError("user_id / character_id 不能为空")
    clean = _sanitize_patch(patch)
    if not clean:
        raise ValueError("没有可写入的字段（允许：name/description/personality/speaking_style/core_anchors/tags）")

    current = read(int(user_id), cid)
    for key, value in clean.items():
        if key in ("personality", "speaking_style") and isinstance(value, dict):
            base = current.get(key)
            current[key] = {**(base if isinstance(base, dict) else {}), **value}
        else:
            current[key] = value

    now = datetime.now(tz=timezone.utc).isoformat()
    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        conn.execute(
            f"""
            INSERT INTO {TABLE} (user_id, character_id, patch, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, character_id)
            DO UPDATE SET patch = excluded.patch, updated_at = excluded.updated_at
            """,
            (int(user_id), cid, json.dumps(current, ensure_ascii=False, sort_keys=True), now),
        )
    return current


def clear(user_id: int, character_id: str) -> bool:
    """撤销该用户对这张卡的全部私人修改（回到公开卡原样）。"""
    cid = str(character_id or "").strip()
    if not cid or not user_id:
        return False
    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE user_id = ? AND character_id = ?",
            (int(user_id), cid),
        )
        return bool(cur.rowcount)


def has(user_id: int, character_id: str) -> bool:
    return bool(read(int(user_id), character_id))


__all__ = [
    "TABLE",
    "ALLOWED_FIELDS",
    "read",
    "apply",
    "write",
    "clear",
    "has",
]
