"""
频率自适应器（normal→low→minimal） + 频率控制器（三重检查）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("proactive.frequency")


class FrequencyAdapter:
    def __init__(
        self,
        normal_daily: int = 8,
        low_daily: int = 3,
        min_weekly: int = 1,
    ):
        self.normal_daily = normal_daily
        self.low_daily = low_daily
        self.min_weekly = min_weekly
        self._unanswered_count = 0
        self._current_level = "normal"

    def on_reply_received(self) -> None:
        self._unanswered_count = max(0, self._unanswered_count - 1)
        if self._current_level == "low" and self._unanswered_count == 0:
            self._current_level = "normal"

    def on_no_reply(self) -> None:
        self._unanswered_count += 1
        if self._unanswered_count >= 5 and self._current_level != "minimal":
            self._current_level = "minimal"
        elif self._unanswered_count >= 3 and self._current_level == "normal":
            self._current_level = "low"

    def get_max_daily(self) -> int:
        if self._current_level == "minimal":
            return self.min_weekly
        if self._current_level == "low":
            return self.low_daily
        return self.normal_daily

    @property
    def level(self) -> str:
        return self._current_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_level": self._current_level,
            "unanswered_count": self._unanswered_count,
            "normal_daily": self.normal_daily,
            "low_daily": self.low_daily,
            "min_weekly": self.min_weekly,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._current_level = data.get("current_level", self._current_level)
        self._unanswered_count = data.get("unanswered_count", self._unanswered_count)
        self.normal_daily = data.get("normal_daily", self.normal_daily)
        self.low_daily = data.get("low_daily", self.low_daily)
        self.min_weekly = data.get("min_weekly", self.min_weekly)


class FrequencyController:
    def __init__(
        self,
        max_daily: int = 8,
        min_interval_minutes: int = 30,
        cooldown_after_reply_minutes: int = 10,
    ):
        self.max_daily = max_daily
        self.min_interval = timedelta(minutes=min_interval_minutes)
        self.cooldown = timedelta(minutes=cooldown_after_reply_minutes)

        self._daily_count = 0
        self._last_sent_time: datetime | None = None
        self._last_reply_time: datetime | None = None
        self._last_reset_date: datetime | None = None

    def can_send(self) -> tuple[bool, str]:
        now = datetime.now(tz=timezone.utc)
        if self._last_reset_date is None or now.date() != self._last_reset_date:
            self._daily_count = 0
            self._last_reset_date = now.date()  # type: ignore[assignment]

        if self._daily_count >= self.max_daily:
            return False, "daily_limit"
        if self._last_sent_time and now - self._last_sent_time < self.min_interval:
            return False, "min_interval"
        if self._last_reply_time and now - self._last_reply_time < self.cooldown:
            return False, "cooldown"
        return True, "ok"

    def record_sent(self) -> None:
        self._daily_count += 1
        self._last_sent_time = datetime.now(tz=timezone.utc)

    def record_reply(self) -> None:
        self._last_reply_time = datetime.now(tz=timezone.utc)

    def get_state(self) -> dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "remaining": self.max_daily - self._daily_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "min_interval_minutes": self.min_interval.total_seconds() / 60,
            "cooldown_minutes": self.cooldown.total_seconds() / 60,
            "last_sent_time": self._last_sent_time.isoformat() if self._last_sent_time else None,
            "last_reply_time": self._last_reply_time.isoformat() if self._last_reply_time else None,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._daily_count = data.get("daily_count", self._daily_count)
        self.max_daily = data.get("max_daily", self.max_daily)
        if "min_interval_minutes" in data:
            self.min_interval = timedelta(minutes=data["min_interval_minutes"])
        if "cooldown_minutes" in data:
            self.cooldown = timedelta(minutes=data["cooldown_minutes"])
        if data.get("last_sent_time"):
            self._last_sent_time = datetime.fromisoformat(data["last_sent_time"])
        if data.get("last_reply_time"):
            self._last_reply_time = datetime.fromisoformat(data["last_reply_time"])
