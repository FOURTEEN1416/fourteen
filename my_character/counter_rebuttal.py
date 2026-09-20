"""
计数反诘模块 — 跟踪用户连续说"没事"等敷衍词的次数

包 Q · A2：不再直接 append 写死句。达到阈值时返回连续次数，
由 orchestrator 把「连续否认 N 次」写入本轮 system 段，
或经 utils.fallback_lines 角色化。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("counter_rebuttal")


class CounterRebuttal:
    """计数反诘：用户连续敷衍达阈值时，返回次数供 system 注入。

    设计要点：
    - 仅当消息"主要是"敷衍词时才计数（避免误触发）
    - 任何非敷衍消息都会重置计数（用户一旦说出真实内容就清零）
    - 触发后计数清零，下一轮重新开始
    - 线程安全：多 session 并发安全
    """

    TRIGGER_PATTERNS = ["没事", "我没事", "我很好", "还行", "还好"]
    THRESHOLD = 5

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _is_perfunctory(self, message: str) -> bool:
        if not message:
            return False
        msg = message.strip()
        if len(msg) > 10:
            return False
        return any(p in msg for p in self.TRIGGER_PATTERNS)

    def check_and_increment(self, message: str, session_id: str) -> int | None:
        """检查是否触发反诘，返回连续次数（达到阈值时）或 None。

        A2：返回值由「写死句」改为「次数」；文案由 system 注入 / fallback_lines 负责。
        """
        if not session_id:
            session_id = "__default__"

        with self._lock:
            if not self._is_perfunctory(message):
                if self._counts.get(session_id, 0) > 0:
                    self._counts[session_id] = 0
                    logger.debug("CounterRebuttal reset: session=%s", session_id)
                return None

            current = self._counts.get(session_id, 0) + 1
            self._counts[session_id] = current
            logger.debug(
                "CounterRebuttal increment: session=%s count=%d", session_id, current
            )

            if current >= self.THRESHOLD:
                self._counts[session_id] = 0
                logger.info(
                    "CounterRebuttal triggered: session=%s count=%d", session_id, current
                )
                return current
            return None

    def reset(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            if session_id in self._counts:
                del self._counts[session_id]

    def get_count(self, session_id: str) -> int:
        if not session_id:
            return 0
        with self._lock:
            return self._counts.get(session_id, 0)
