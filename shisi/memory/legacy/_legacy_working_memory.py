"""有界工作记忆：按会话和角色分桶；持久化 chat_history 才是完整历史真源。"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class WorkingMemory:
    def __init__(self, limit: int = 500):
        self.limit = limit
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], deque] = {}
        self._session_id = ""
        self._sequence = 0

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = f"session_{time.time_ns()}"
        return self._session_id

    def _key(self, session_id: str, character_id: str) -> tuple[str, str]:
        return (session_id or self.session_id, character_id)

    def start_session(self, session_id: str = "", channel: str = "wechat", user_id: str = "default") -> None:
        with self._lock:
            self._session_id = session_id or f"session_{time.time_ns()}"
            for key in list(self._buckets):
                if key[0] == self._session_id:
                    del self._buckets[key]

    def add(
        self, role: str, content: str, emotion: str = "", importance: float = 0.5,
        session_id: str = "", character_id: str = "", turn_id: str = "",
    ) -> None:
        key = self._key(session_id, character_id)
        with self._lock:
            bucket = self._buckets.setdefault(key, deque(maxlen=self.limit))
            if turn_id and any(m.get("turn_id") == turn_id and m["role"] == role for m in bucket):
                return
            self._sequence += 1
            bucket.append({
                "id": self._sequence, "role": role, "content": content, "emotion": emotion,
                "importance": importance, "timestamp": time.time(), "session_id": key[0],
                "character_id": character_id, "turn_id": turn_id,
            })
            if len(self._buckets) > 200:
                for old in list(self._buckets):
                    if old != key:
                        del self._buckets[old]
                        break

    def get_recent(self, n: int = 10, session_id: str = "", character_id: str = "") -> list[dict[str, Any]]:
        if n <= 0:
            return []
        return self.get_for_archive(session_id, character_id)[-n:]

    def get_for_archive(self, session_id: str = "", character_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._buckets.get(self._key(session_id, character_id), [])]

    def should_archive(self, trigger_count: int = 20, session_id: str = "", character_id: str = "") -> bool:
        return self.count(session_id, character_id) >= trigger_count

    def acknowledge_archive(self, messages: list[dict], session_id: str = "", character_id: str = "") -> None:
        """只移除已成功归档的快照消息；生成摘要期间追加的消息不在快照中。"""
        ids = {m["id"] for m in messages}
        key = self._key(session_id, character_id)
        with self._lock:
            self._buckets[key] = deque(
                (m for m in self._buckets.get(key, []) if m["id"] not in ids), maxlen=self.limit,
            )

    def clear(self, session_id: str = "", character_id: str = "") -> None:
        with self._lock:
            self._buckets.pop(self._key(session_id, character_id), None)

    def count(self, session_id: str = "", character_id: str = "") -> int:
        with self._lock:
            return len(self._buckets.get(self._key(session_id, character_id), []))
