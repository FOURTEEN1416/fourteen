"""好感度衰减引擎 — 宽限期 + 每日衰减。"""

from __future__ import annotations

from datetime import datetime

from ..config import get_config


class DecayEngine:
    def __init__(
        self,
        decay_rate: float | None = None,
        grace_period_days: int | None = None,
    ):
        self._decay_rate = decay_rate if decay_rate is not None else get_config("affinity", "decay_rate", 0.5)
        self._grace_period = grace_period_days if grace_period_days is not None else get_config("affinity", "grace_period_days", 3)

    def calculate_decay(
        self,
        current_affinity: float,
        last_interaction: datetime,
        now: datetime | None = None,
    ) -> float:
        now = now or datetime.now()
        days_since = (now - last_interaction).total_seconds() / 86400

        if days_since <= self._grace_period:
            return 0.0

        decay_days = days_since - self._grace_period
        decay = self._decay_rate * decay_days
        return min(decay, current_affinity)
