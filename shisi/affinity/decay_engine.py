"""好感度衰减引擎 — 宽限期 + 每日衰减。

口径（唯一真源，调用方不得各自累计）：
``decay_for_interval`` 计算的是**区间**衰减 —— 给定窗口起点与 ``now``，
返回这段时间内应扣的量。跨重启/多次调用要守恒，调用方必须自己记住
"上次已扣到何时"，并把该水位作为下一次的窗口起点（见
``AffinityEnhancer._last_decay_at``）。旧实现只提供"自最后交互起的累计量"
单一入口，调用方逐次相减即重复扣减（第 N 天扣 ``rate×(N−grace)`` 而非
当天的 ``rate``，累计按平方增长）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import get_config


class DecayEngine:
    def __init__(
        self,
        decay_rate: float | None = None,
        grace_period_days: int | None = None,
    ):
        self._decay_rate = decay_rate if decay_rate is not None else get_config("affinity", "decay_rate", 0.5)
        self._grace_period = grace_period_days if grace_period_days is not None else get_config("affinity", "grace_period_days", 3)

    @property
    def grace_period_days(self) -> float:
        return float(self._grace_period)

    def grace_start(self, last_interaction: datetime) -> datetime:
        """宽限期结束时刻 —— 在此之前不产生任何衰减。"""
        return last_interaction + timedelta(days=float(self._grace_period))

    def decay_for_interval(
        self,
        current_affinity: float,
        start: datetime,
        now: datetime,
    ) -> float:
        """区间 ``[start, now]`` 内应扣的量，钳到当前值（不得扣成负数）。"""
        days = (now - start).total_seconds() / 86400
        if days <= 0:
            return 0.0
        return min(self._decay_rate * days, max(0.0, current_affinity))

    def calculate_decay(
        self,
        current_affinity: float,
        last_interaction: datetime,
        now: datetime | None = None,
    ) -> float:
        """自最后一次交互起**累计**应扣的量（含宽限期）。

        注意：这是"从交互时刻算到现在"的总量，只适合一次性结算；逐次调用方
        必须改用 ``decay_for_interval`` 并自带水位，否则重复扣减。
        """
        now = now or datetime.now(tz=timezone.utc)
        return self.decay_for_interval(current_affinity, self.grace_start(last_interaction), now)
