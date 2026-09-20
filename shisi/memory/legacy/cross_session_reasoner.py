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

    def get_pending_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """待处理事件。session_id 非 None 时按 source_session_id 隔离。

        2026-09-21：旧实现 `WHERE is_resolved=0` 全表返回，多用户会互相
        看见对方未完成事项（串台）。
        """
        try:
            with self._sm.get_connection() as conn:
                if session_id is not None:
                    sid = str(session_id)
                    rows = conn.execute(
                        "SELECT * FROM pending_events WHERE is_resolved = 0 "
                        "AND (source_session_id = ? OR source_session_id = '') "
                        "ORDER BY created_at ASC",
                        (sid,),
                    ).fetchall()
                    # 空 source 的历史事件不注入具体会话
                    return [
                        dict(r) for r in rows
                        if str(dict(r).get("source_session_id") or "") == sid
                    ]
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
