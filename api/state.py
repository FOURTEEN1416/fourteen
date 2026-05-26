"""API 共享状态管理器"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, List


class SafetyLogManager:
    """安全日志管理器"""
    def __init__(self, maxlen: int = 500):
        self._log: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._log.append(entry)

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._log)[-limit:]
        return items

    def get_stats(self, enabled: bool = False) -> Dict[str, Any]:
        with self._lock:
            items = list(self._log)
            categories = {}
            for entry in items:
                cat = entry.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1
            return {
                "enabled": enabled,
                "total_flagged": len(self._log),
                "recent_flagged": len(items),
                "by_category": categories,
                "recent": items[-20:],
            }


class ToolHistoryManager:
    """工具操作历史管理器"""
    def __init__(self, maxlen: int = 1000):
        self._log: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._log.append(entry)

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._log)[-limit:]
        return items


class TrainingStateManager:
    """训练状态管理器"""
    def __init__(self):
        self._state: Dict[str, Any] = {
            "status": "idle",
            "progress": 0.0,
            "current_step": 0,
            "total_steps": 0,
            "loss": None,
            "extracted_turns": 0,
            "cleaned_turns": 0,
            "error": None,
            "start_time": None,
            "eta_seconds": None,
        }
        self._lock = threading.Lock()

    def update(self, **kwargs) -> None:
        with self._lock:
            self._state.update(kwargs)

    def get(self, key: str, default=None):
        with self._lock:
            return self._state.get(key, default)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)
