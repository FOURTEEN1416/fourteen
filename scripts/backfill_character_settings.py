"""存量「用户 × 角色」设置物化脚本（2026-11-07 痛点批 P1）。

用户裁决「每人每角色一份」：本脚本给**已存在**的组合各建一行全量快照，
并继承旧的 per-user 覆盖值（`user_humanize` / `user_proactive_config`），
不让用户已有调参丢失。

组合来源（逐条去重）：
1. `wechat_bindings`：每个绑定 `(user_id, wxid)` → `user_key = f"{user_id}:{wxid}"`，
   角色取 `character_card_id`（缺省 `default`）。
2. `user_active_characters`：每个激活行 `(user_id, character_id)`，`user_key`
   取该用户最近一条绑定的会话键（无绑定 → 跳过，不猜键）。
3. `config/characters/*.json` 中 `user_id` 为真实数字的私有卡：
   给持卡人（若已绑定）物化该角色。

幂等：已存在的行跳过（`character_settings.has_row`）。只增不改，不删任何数据。

用法（在仓库根目录）：
    python -m scripts.backfill_character_settings --dry-run
    python -m scripts.backfill_character_settings
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

from utils import character_settings

logger = logging.getLogger("backfill_character_settings")

_REPO = Path(__file__).resolve().parent.parent
_DB = _REPO / "data" / "sqlite.db"
_CHARS = _REPO / "config" / "characters"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def collect_pairs(db_path: Path = _DB, chars_dir: Path = _CHARS) -> list[tuple[str, str]]:
    """收集 (user_key, character_id) 组合，去重且保序。"""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(user_key: str, character_id: str) -> None:
        uk = str(user_key or "").strip()
        cid = str(character_id or "").strip() or "default"
        if not uk:
            return
        key = (uk, cid)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    bindings: list[tuple[int, str, str]] = []
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            if _table_exists(conn, "wechat_bindings"):
                rows = conn.execute(
                    "SELECT user_id, wxid, COALESCE(character_card_id, 'default') "
                    "FROM wechat_bindings WHERE wxid IS NOT NULL AND wxid != ''"
                ).fetchall()
                for user_id, wxid, card_id in rows:
                    bindings.append((int(user_id), str(wxid), str(card_id)))
                    _add(f"{int(user_id)}:{wxid}", str(card_id))

            if _table_exists(conn, "user_active_characters"):
                # 每个用户最近一条绑定（按 bound_at/id 倒序）
                latest: dict[int, str] = {}
                if bindings:
                    rows = conn.execute(
                        "SELECT user_id, wxid FROM wechat_bindings "
                        "WHERE wxid IS NOT NULL AND wxid != '' "
                        "ORDER BY bound_at ASC, id ASC"
                    ).fetchall()
                    for user_id, wxid in rows:
                        latest[int(user_id)] = str(wxid)
                active_rows = conn.execute(
                    "SELECT user_id, character_id FROM user_active_characters"
                ).fetchall()
                for user_id, character_id in active_rows:
                    wxid = latest.get(int(user_id))
                    if wxid:
                        _add(f"{int(user_id)}:{wxid}", str(character_id))

    # 私有卡持卡人（需要已绑定才能得到会话键）
    if chars_dir.exists():
        owner_wxid: dict[str, str] = {}
        for user_id, wxid, _ in bindings:
            owner_wxid.setdefault(str(user_id), wxid)
        for path in sorted(chars_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            owner = str(card.get("user_id") or "").strip()
            if owner.isdigit() and owner in owner_wxid:
                _add(f"{owner}:{owner_wxid[owner]}", str(card.get("id") or path.stem))

    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="物化存量「用户 × 角色」设置快照")
    parser.add_argument("--dry-run", action="store_true", help="只打印将处理的组合，不写库")
    parser.add_argument("--db", default=str(_DB), help="sqlite.db 路径")
    parser.add_argument("--chars", default=str(_CHARS), help="角色卡目录")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pairs = collect_pairs(Path(args.db), Path(args.chars))
    logger.info("发现 %d 个「用户 × 角色」组合", len(pairs))
    for user_key, cid in pairs:
        logger.info("  %s × %s", user_key, cid)

    if args.dry_run:
        logger.info("dry-run：未写库")
        return 0

    created = character_settings.backfill_pairs(pairs)
    logger.info("完成：新建 %d 行，跳过已存在 %d 行", created, len(pairs) - created)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
