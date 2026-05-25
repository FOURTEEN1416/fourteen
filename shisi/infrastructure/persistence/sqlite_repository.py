"""SQLite角色仓储实现"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from shisi.core.models.affinity_level import AffinityLevel
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.models.emotion_type import EmotionType
from shisi.core.models.emotional_state import EmotionalState
from shisi.core.models.persona_profile import PersonaProfile

from .schema import CHARACTERS_V2_DDL, CHARACTERS_V2_INDEXES


class SQLiteCharacterRepository:
    def __init__(self, db_path: Path = Path("data/sqlite.db")):
        self.db_path = db_path
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(CHARACTERS_V2_DDL)
            for idx_ddl in CHARACTERS_V2_INDEXES:
                conn.execute(idx_ddl)
            conn.commit()
        finally:
            conn.close()

    def get_by_id(self, character_id: str) -> CharacterAggregate | None:
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM characters_v2 WHERE id = ?", (character_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_aggregate(row)
        finally:
            conn.close()

    def get_active(self) -> CharacterAggregate | None:
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM characters_v2 WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return self._row_to_aggregate(row)
        finally:
            conn.close()

    def list_all(self) -> list[CharacterAggregate]:
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM characters_v2 ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_aggregate(row) for row in rows]
        finally:
            conn.close()

    def save(self, character: CharacterAggregate) -> None:
        conn = self._connect()
        try:
            active_id = self._get_active_id(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO characters_v2 (
                    id, name, description, avatar_url, tags,
                    persona_json, emotional_state_json, is_active,
                    created_at, updated_at, version, source_format, source_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character.id,
                    character.name,
                    character.description,
                    character.avatar_url,
                    json.dumps(character.tags, ensure_ascii=False),
                    json.dumps(character.persona.to_dict(), ensure_ascii=False),
                    json.dumps(character.emotional_state.to_dict(), ensure_ascii=False),
                    1 if character.id == active_id else 0,
                    character.created_at.isoformat(),
                    datetime.now(tz=timezone.utc).isoformat(),
                    character.version + 1,
                    character.source_format,
                    json.dumps(character.source_data, ensure_ascii=False)
                    if character.source_data
                    else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def set_active(self, character_id: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("UPDATE characters_v2 SET is_active = 0")
            if not character_id:
                conn.commit()
                return True
            row = conn.execute(
                "SELECT 1 FROM characters_v2 WHERE id = ?", (character_id,)
            ).fetchone()
            if not row:
                conn.commit()
                return False
            conn.execute(
                "UPDATE characters_v2 SET is_active = 1 WHERE id = ?",
                (character_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def delete(self, character_id: str) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM characters_v2 WHERE id = ?", (character_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _get_active_id(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute(
            "SELECT id FROM characters_v2 WHERE is_active = 1"
        ).fetchone()
        return row[0] if row else None

    def _row_to_aggregate(self, row: sqlite3.Row) -> CharacterAggregate:
        persona_data = json.loads(row["persona_json"])
        emotional_data = json.loads(row["emotional_state_json"])

        return CharacterAggregate(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            avatar_url=row["avatar_url"],
            tags=json.loads(row["tags"]),
            persona=PersonaProfile.from_dict(persona_data),
            emotional_state=EmotionalState(
                primary_emotion=EmotionType[emotional_data.get("primary_emotion", "NEUTRAL")],
                intensity=emotional_data.get("intensity", 0.5),
                energy=emotional_data.get("energy", 1.0),
                affinity_level=AffinityLevel(emotional_data.get("affinity_level", 0)),
                affection_points=emotional_data.get("affection_points", 0.0),
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
            source_format=row["source_format"],
            source_data=json.loads(row["source_data_json"]) if row["source_data_json"] else {},
        )
