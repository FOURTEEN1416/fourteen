"""CharacterStore — SQLite持久化，角色元数据CRUD。"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import CardFormat, CharaCardV2, CharacterState

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class CharacterStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else _DB_DEFAULT

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """P2: 上下文管理器，确保数据库连接正确关闭"""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def save_character(self, card: CharaCardV2, fmt: CardFormat = CardFormat.CHARA_CARD_V2) -> str:
        char_id = re.sub(r'[^\w\u4e00-\u9fff]', '_', card.data.name).strip('_')[:50]
        if not char_id:
            char_id = f"char_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        card_json = card.model_dump_json()
        tags_json = json.dumps(card.data.tags, ensure_ascii=False)

        with self._connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO characters
                   (character_id, name, chara_card_json, format, is_active, tags, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?, datetime('now'))""",
                (char_id, card.data.name, card_json, fmt.value, tags_json),
            )
            conn.commit()
            return char_id

    def get_character(self, character_id: str) -> CharaCardV2 | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT chara_card_json FROM characters WHERE character_id=?",
                (character_id,),
            ).fetchone()
            if not row:
                return None
            from .character_card_v2 import ParserDispatcher
            data = json.loads(row["chara_card_json"])
            data.pop("_format", None)
            card, _ = ParserDispatcher.parse(data)
            return card

    def list_characters(self) -> list[CharacterState]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT character_id, name, format, is_active, tags, created_at, updated_at
                   FROM characters ORDER BY updated_at DESC"""
            ).fetchall()
            result = []
            for r in rows:
                tags = json.loads(r["tags"]) if r["tags"] else []
                result.append(CharacterState(
                    character_id=r["character_id"],
                    name=r["name"],
                    format=CardFormat(r["format"]) if r["format"] in CardFormat._value2member_map_ else CardFormat.CHARA_CARD_V2,
                    is_active=bool(r["is_active"]),
                    tags=tags,
                    created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else datetime.now(),
                    updated_at=datetime.fromisoformat(r["updated_at"]) if r["updated_at"] else datetime.now(),
                ))
            return result

    def set_active(self, character_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT character_id FROM characters WHERE character_id=?", (character_id,)
            ).fetchone()
            if not row:
                return None
            with conn:
                conn.execute("UPDATE characters SET is_active=0 WHERE is_active=1")
                conn.execute("UPDATE characters SET is_active=1, updated_at=datetime('now') WHERE character_id=?", (character_id,))
            return character_id

    def get_active_id(self) -> str | None:
        with self._connection() as conn:
            row = conn.execute("SELECT character_id FROM characters WHERE is_active=1").fetchone()
            return row["character_id"] if row else None

    def delete_character(self, character_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM characters WHERE character_id=?", (character_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_character(self, character_id: str, card: CharaCardV2) -> bool:
        card_json = card.model_dump_json()
        tags_json = json.dumps(card.data.tags, ensure_ascii=False)
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE characters SET name=?, chara_card_json=?, tags=?, updated_at=datetime('now')
                   WHERE character_id=?""",
                (card.data.name, card_json, tags_json, character_id),
            )
            conn.commit()
            return cursor.rowcount > 0
