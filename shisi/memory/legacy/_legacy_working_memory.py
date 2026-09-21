"""
工作记忆 — 短期对话上下文（memory_pipeline 内联版本）

P0-4-4：按 session_id 分桶，杜绝多用户消息交错进同一 deque 后被
统一归档并挂最后一个 session_id（跨用户情景记忆污染）。
未传 session_id 的旧调用落自动全局桶，行为兼容。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("working_memory")


class WorkingMemory:
    """工作记忆 — 短期对话上下文（按会话分桶 O(1) + 自动归档触发）"""

    def __init__(self, limit: int = 500):
        self.limit = limit
        self._lock = threading.Lock()
        self._buckets: dict[str, deque] = {}
        self._counts: dict[str, int] = {}
        self._session_id: str = ""

    @property
    def session_id(self) -> str:
        if not self._session_id:
            self._session_id = f"session_{int(time.time())}"
        return self._session_id

    def _bucket_for(self, sid: str) -> deque:
        dq = self._buckets.get(sid)
        if dq is None:
            dq = deque(maxlen=self.limit)
            self._buckets[sid] = dq
        return dq

    def start_session(
        self,
        session_id: str = "",
        channel: str = "wechat",
        user_id: str = "default",
    ) -> None:
        with self._lock:
            sid = session_id or f"session_{int(time.time())}"
            self._session_id = sid
            self._buckets[sid] = deque(maxlen=self.limit)
            self._counts[sid] = 0

    def add(
        self,
        role: str,
        content: str,
        emotion: str = "",
        importance: float = 0.5,
        session_id: str = "",
    ) -> None:
        sid = str(session_id or "") or self.session_id
        msg = {
            "role": role,
            "content": content,
            "emotion": emotion,
            "importance": importance,
            "timestamp": time.time(),
        }
        with self._lock:
            self._bucket_for(sid).append(msg)
            self._counts[sid] = self._counts.get(sid, 0) + 1
            # 桶数上限防泄漏：超限时按插入序淘汰最旧桶（当前写入桶除外）
            if len(self._buckets) > 200:
                for old in list(self._buckets.keys())[:-200]:
                    if old != sid:
                        self._buckets.pop(old, None)
                        self._counts.pop(old, None)

    def get_recent(self, n: int = 10, session_id: str = "") -> list[dict[str, Any]]:
        sid = str(session_id or "") or self.session_id
        with self._lock:
            return list(self._buckets.get(sid, []))[-n:]

    def get_for_archive(self, session_id: str = "") -> list[dict[str, Any]]:
        sid = str(session_id or "") or self.session_id
        with self._lock:
            return list(self._buckets.get(sid, []))

    def should_archive(self, trigger_count: int = 20, session_id: str = "") -> bool:
        sid = str(session_id or "") or self.session_id
        with self._lock:
            return self._counts.get(sid, 0) >= trigger_count

    def clear(self, session_id: str = "") -> None:
        sid = str(session_id or "") or self.session_id
        with self._lock:
            self._buckets[sid] = deque(maxlen=self.limit)
            self._counts[sid] = 0

    def count(self, session_id: str = "") -> int:
        sid = str(session_id or "") or self.session_id
        with self._lock:
            return len(self._buckets.get(sid, []))
