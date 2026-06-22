from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("working_memory")


class WorkingMemory:
    def __init__(self, structured_memory, session_id: str = "", limit: int = 20):
        self._sm = structured_memory
        self.session_id = session_id
        self.limit = limit

    def start_session(self, session_id: str, channel: str = "wechat", user_id: str = "default"):
        self.session_id = session_id
        with self._sm.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, channel, user_id, started_at, is_active) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1)",
                (session_id, channel, user_id),
            )
            conn.commit()

    def end_session(self):
        if not self.session_id:
            return
        with self._sm.get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP, is_active = 0 WHERE id = ?",
                (self.session_id,),
            )
            conn.commit()

    def add(self, role: str, content: str, emotion_tag: str = "", importance: float = 0.5):
        if not self.session_id:
            return
        with self._sm.get_connection() as conn:
            conn.execute(
                "INSERT INTO working_memory (session_id, role, content, emotion_tag, importance) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.session_id, role, content, emotion_tag, importance),
            )
            conn.commit()

    def get_recent(self, n: int | None = None) -> list[dict[str, Any]]:
        n = n or self.limit
        if not self.session_id:
            return []
        with self._sm.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM working_memory WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (self.session_id, n),
            ).fetchall()
            return [dict(r) for r in rows][::-1]

    def count(self) -> int:
        if not self.session_id:
            return 0
        with self._sm.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM working_memory WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()
            return row["cnt"] if row else 0

    def should_archive(self) -> bool:
        return self.count() >= self.limit

    def get_for_archive(self) -> list[dict[str, Any]]:
        return self.get_recent()

    def clear(self):
        if not self.session_id:
            return
        with self._sm.get_connection() as conn:
            conn.execute(
                "DELETE FROM working_memory WHERE session_id = ?",
                (self.session_id,),
            )
            conn.commit()
