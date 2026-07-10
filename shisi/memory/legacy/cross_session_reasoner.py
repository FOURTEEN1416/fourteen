"""
跨会话推理器 — 提取未来事件并跟踪

从对话中识别未来事件（"下周去北京"），持久化到数据库并支持跟踪/解决。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("cross_session_reasoner")


class CrossSessionReasoner:
    """跨会话推理 — 提取未来事件并跟踪"""

    FUTURE_KEYWORDS = ["明天", "下周", "周末", "之后", "以后", "即将", "将要"]

    def __init__(self, structured_memory):
        self._sm = structured_memory

    def extract_pending_event(self, fact: str) -> dict | None:
        for kw in self.FUTURE_KEYWORDS:
            if kw in fact:
                return {"event_desc": fact, "keyword": kw}
        return None

    def store_pending_event(self, event_desc: str,
                            expected_time: str | None = None,
                            session_id: str = ""):
        try:
            with self._sm.get_connection() as conn:
                conn.execute(
                    "INSERT INTO pending_events "
                    "(event_desc, expected_time, source_session_id) "
                    "VALUES (?, ?, ?)",
                    (event_desc, expected_time, session_id),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("store_pending_event failed: %s", e)

    def get_pending_events(self) -> list[dict[str, Any]]:
        try:
            with self._sm.get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM pending_events WHERE is_resolved = 0 "
                    "ORDER BY created_at ASC"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.debug("get_pending_events failed: %s", e)
            return []

    def resolve_event(self, event_id: int):
        try:
            with self._sm.get_connection() as conn:
                conn.execute(
                    "UPDATE pending_events SET is_resolved = 1 WHERE id = ?",
                    (event_id,),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("resolve_event failed: %s", e)
