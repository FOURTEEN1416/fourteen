from __future__ import annotations

import threading
from collections import deque


class SafetyLogManager:
    def __init__(self, maxlen: int = 2000):
        self._log: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        with self._lock:
            self._log.append(entry)

    def get_recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._log)
        return items[-limit:]

    def get_stats(self, enabled: bool = False) -> dict:
        with self._lock:
            items = list(self._log)
        recent = items[-200:]
        categories: dict[str, int] = {}
        for entry in recent:
            cat = entry.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "enabled": enabled,
            "total_flagged": len(items),
            "recent_flagged": len(recent),
            "by_category": categories,
            "recent": recent[-20:],
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._log)
