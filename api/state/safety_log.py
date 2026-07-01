from __future__ import annotations

import threading
from collections import deque


class SafetyLogManager:
    def __init__(self, maxlen: int = 2000):
        self._log: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry: dict, user_id: int | None = None) -> None:
        entry = {**entry, "user_id": user_id}
        with self._lock:
            self._log.append(entry)

    def _filter_by_user(self, items: list[dict], user_id: int | None = None) -> list[dict]:
        if user_id is None:
            return items
        return [it for it in items if it.get("user_id") == user_id]

    def get_recent(self, limit: int = 50, user_id: int | None = None) -> list[dict]:
        with self._lock:
            items = list(self._log)
        items = self._filter_by_user(items, user_id)
        return items[-limit:]

    def get_stats(self, enabled: bool = False, user_id: int | None = None) -> dict:
        with self._lock:
            items = list(self._log)
        items = self._filter_by_user(items, user_id)
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
