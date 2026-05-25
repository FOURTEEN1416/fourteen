"""对话统计分析服务。"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("shisi.stats.analytics")


class AnalyticsService:
    def __init__(self):
        self._message_count = 0
        self._daily_counts: dict[str, int] = {}
        self._character_usage: Counter = Counter()
        self._emotion_counts: Counter = Counter()
        self._affinity_history: dict[str, list[tuple[str, float]]] = {}

    def record_message(self, character_id: str, emotion: str = "", affinity: float | None = None) -> None:
        self._message_count += 1
        self._character_usage[character_id] += 1
        if emotion:
            self._emotion_counts[emotion] += 1
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        self._daily_counts[today] = self._daily_counts.get(today, 0) + 1
        if affinity is not None:
            history = self._affinity_history.setdefault(character_id, [])
            history.append((today, affinity))

    def get_stats(self) -> dict[str, Any]:
        days = len(self._daily_counts) or 1
        avg_daily = self._message_count / days

        return {
            "total_messages": self._message_count,
            "daily_average": round(avg_daily, 1),
            "character_distribution": dict(self._character_usage.most_common()),
            "emotion_distribution": dict(self._emotion_counts.most_common()),
            "affinity_history": {cid: hist[-30:] for cid, hist in self._affinity_history.items()},
            "active_days": days,
        }
