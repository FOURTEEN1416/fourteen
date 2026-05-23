"""StickerManager表情包管理器 — CRUD + 分类 + 推荐。"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

logger = logging.getLogger("shisi.sticker.sticker_manager")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class StickerManager:
    def __init__(self, db_path: Path | str | None = None, data_dir: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        self._data_dir = Path(data_dir) if data_dir else Path(get_config("sticker", "data_dir", "data/stickers"))
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def list_by_category(self, category: str | None = None) -> list[dict[str, Any]]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            if category:
                rows = conn.execute("SELECT * FROM stickers WHERE category=?", (category,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM stickers").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def recommend(self, emotion_tags: list[str], limit: int = 5) -> list[dict[str, Any]]:
        from .emotion_recommender import EmotionRecommender
        rec = EmotionRecommender()
        return rec.recommend(emotion_tags, self.list_by_category(), limit)

    def import_zip(self, zip_path: Path | str, category: str = "default") -> tuple[int, int]:
        from .importer import StickerImporter
        imp = StickerImporter(self._data_dir)
        return imp.import_zip(zip_path, category)

    def bind_to_character(self, character_id: str, sticker_ids: list[str], unlock_threshold: int = 0) -> int:
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = 0
            for sid in sticker_ids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO character_stickers (character_id, sticker_id, unlock_threshold) VALUES (?,?,?)",
                        (character_id, sid, unlock_threshold),
                    )
                    count += 1
                except Exception as e:
                    logger.debug("单条表情包插入跳过: %s", e)
            conn.commit()
            return count
        finally:
            conn.close()

    def get_sticker(self, sticker_id: str) -> Optional[dict[str, Any]]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM stickers WHERE sticker_id=?", (sticker_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_sticker(self, sticker_id: str, category: str, emotion_tags: list[str], file_path: str, fmt: str = "png", character_id: str | None = None) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO stickers (sticker_id, category, emotion_tags, file_path, format, character_id) VALUES (?,?,?,?,?,?)",
                (sticker_id, category, json.dumps(emotion_tags, ensure_ascii=False), file_path, fmt, character_id),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_sticker(self, sticker_id: str) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute("DELETE FROM stickers WHERE sticker_id=?", (sticker_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
