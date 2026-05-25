"""
时间漂移器 — 实现时间段窗口 + 随机偏移
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

logger = logging.getLogger("time_drifter")


@dataclass
class TimeWindow:
    """时间窗口"""
    start: time
    end: time
    drift_minutes: int = 30  # 允许的随机偏移（分钟）


class TimeDrifter:
    """
    时间漂移器

    实现自然的时间触发，而非固定的闹钟式触发

    特点：
    - 时间段窗口：在[start, end]范围内随机选择
    - 相邻两天偏移：至少±30分钟差异
    - 用户活动感知：如果用户已活跃，跳过触发
    """

    # 默认时间窗口配置
    DEFAULT_WINDOWS = {
        "morning": TimeWindow(time(7, 0), time(9, 30), drift_minutes=30),
        "lunch": TimeWindow(time(11, 0), time(13, 0), drift_minutes=20),
        "dinner": TimeWindow(time(17, 0), time(19, 30), drift_minutes=25),
        "night": TimeWindow(time(21, 30), time(0, 30), drift_minutes=40),
    }

    def __init__(self, windows: dict[str, TimeWindow] | None = None):
        self._windows = windows or self.DEFAULT_WINDOWS
        self._last_trigger_times: dict[str, datetime] = {}
        self._last_drift_values: dict[str, int] = {}

    def get_next_trigger_time(
        self,
        window_name: str,
        base_date: datetime | None = None,
    ) -> datetime | None:
        """
        获取下一次触发时间

        Args:
            window_name: 窗口名称 (morning/lunch/dinner/night)
            base_date: 基准日期，默认今天

        Returns:
            下一次触发时间
        """
        if window_name not in self._windows:
            return None

        window = self._windows[window_name]
        base_date = base_date or datetime.now(tz=timezone.utc)

        # 计算窗口的开始和结束时间
        start_dt = datetime.combine(base_date.date(), window.start)
        end_dt = datetime.combine(base_date.date(), window.end)

        # 处理跨午夜的情况
        if window.end < window.start:
            end_dt += timedelta(days=1)

        # 计算随机偏移
        drift = random.randint(-window.drift_minutes, window.drift_minutes)

        # 确保与上次偏移差异足够大
        if window_name in self._last_drift_values:
            last_drift = self._last_drift_values[window_name]
            while abs(drift - last_drift) < 15:  # 至少15分钟差异
                drift = random.randint(-window.drift_minutes, window.drift_minutes)

        self._last_drift_values[window_name] = drift

        # 在窗口内随机选择一个时间点
        window_seconds = (end_dt - start_dt).total_seconds()
        random_offset = random.random() * window_seconds

        trigger_time = start_dt + timedelta(seconds=random_offset)
        trigger_time += timedelta(minutes=drift)

        return trigger_time

    def should_trigger_now(
        self,
        window_name: str,
        user_active: bool = False,
    ) -> bool:
        """
        检查当前是否应该触发

        Args:
            window_name: 窗口名称
            user_active: 用户是否已活跃（如果活跃则跳过）

        Returns:
            是否应该触发
        """
        if user_active:
            logger.debug("User already active, skipping trigger")
            return False

        now = datetime.now(tz=timezone.utc)

        if window_name not in self._windows:
            return False

        window = self._windows[window_name]
        current_time = now.time()

        # 检查是否在窗口内
        if window.start <= window.end:
            in_window = window.start <= current_time <= window.end
        else:
            # 跨午夜
            in_window = current_time >= window.start or current_time <= window.end

        if not in_window:
            return False

        # 检查是否已经触发过
        if window_name in self._last_trigger_times:
            last = self._last_trigger_times[window_name]
            if (now - last).total_seconds() < 3600:  # 1小时内不重复触发
                return False

        # 随机概率触发（避免每次检查都触发）
        trigger_probability = 0.1  # 10%概率
        if random.random() < trigger_probability:
            self._last_trigger_times[window_name] = now
            return True

        return False

    def get_persona_rhythm_window(
        self,
        wake_up_window: list[str] | None = None,
        sleep_window: list[str] | None = None,
    ) -> dict[str, TimeWindow]:
        """
        根据人设作息生成时间窗口

        Args:
            wake_up_window: 起床时间窗口 ["10:00", "12:00"]
            sleep_window: 睡觉时间窗口 ["01:00", "03:00"]

        Returns:
            个性化时间窗口
        """
        windows = dict(self._windows)

        if wake_up_window and len(wake_up_window) == 2:
            start = self._parse_time(wake_up_window[0])
            end = self._parse_time(wake_up_window[1])
            if start and end:
                windows["morning"] = TimeWindow(start, end, drift_minutes=30)

        if sleep_window and len(sleep_window) == 2:
            start = self._parse_time(sleep_window[0])
            end = self._parse_time(sleep_window[1])
            if start and end:
                windows["night"] = TimeWindow(start, end, drift_minutes=40)

        return windows

    def _parse_time(self, time_str: str) -> time | None:
        """解析时间字符串"""
        try:
            parts = time_str.split(":")
            return time(int(parts[0]), int(parts[1]))
        except Exception as e:  # noqa: BLE001

            logger.debug("Error: %s", e)

            return None

    def mark_triggered(self, window_name: str) -> None:
        """标记已触发"""
        self._last_trigger_times[window_name] = datetime.now(tz=timezone.utc)
