"""
工作记忆 — 短期对话上下文（memory_pipeline 内联版本）

基于 deque O(1) 的短期对话上下文存储，支持自动归档触发。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger("working_memory")


class WorkingMemory:
    """工作记忆 — 短期对话上下文（deque O(1) + 自动归档触发）"""

    def __init__(self, limit: int = 500):
        self._messages: deque = deque(maxlen=limit)
        self._session_id: str = ""
        self._lock = threading.Lock()
        self._total_count = 0

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = f"session_{int(time.time())}"
        return self._session_id

    def start_session(self, session_id: str = "", channel: str = "wechat", user_id: str = "default") -> None:
        with self._lock:
            self._messages.clear()
            self._session_id = session_id or f"session_{int(time.time())}"
            self._total_count = 0

    def add(self, role: str, content: str, emotion: str = "",
            importance: float = 0.5) -> None:
        with self._lock:
            self._messages.append({
                "role": role,
                "content": content,
                "emotion": emotion,
                "importance": importance,
                "timestamp": time.time(),
            })
            self._total_count += 1

    def get_recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            return list(self._messages)[-n:]

    def get_for_archive(self) -> list[dict]:
        with self._lock:
            return list(self._messages)

    def should_archive(self, trigger_count: int = 20) -> bool:
        return self._total_count >= trigger_count

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._total_count = 0

    def count(self) -> int:
        return len(self._messages)
