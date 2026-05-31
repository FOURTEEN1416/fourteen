"""收藏管理器 — 收藏/取消收藏/列表。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("shisi.memory.favorite_manager")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class FavoriteManager:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT

    def favorite(self, character_id: str, memory_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO memory_favorites (character_id, memory_id) VALUES (?,?)",
                (character_id, memory_id),
            )
            conn.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("收藏失败: %s", e)
            return False
        finally:
            conn.close()

    def unfavorite(self, character_id: str, memory_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(
                "DELETE FROM memory_favorites WHERE character_id=? AND memory_id=?",
                (character_id, memory_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def list_favorites(self, character_id: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM memory_favorites WHERE character_id=? ORDER BY favorited_at DESC",
                (character_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def is_favorite(self, character_id: str, memory_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT 1 FROM memory_favorites WHERE character_id=? AND memory_id=?",
                (character_id, memory_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
