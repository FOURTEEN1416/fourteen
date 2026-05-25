"""
勿扰模式守卫 — 时间段屏蔽 + 智能检测
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

logger = logging.getLogger("dnd_guard")


@dataclass
class DndWindow:
    """勿扰时间窗口"""
    start: time
    end: time
    reason: str = ""


class DndGuard:
    """
    勿扰模式守卫

    功能：
    - 时间段屏蔽：指定时间段内不发送消息
    - 智能检测：检测用户状态关键词（开会、忙、睡了等）
    - 动态暂停：用户表示忙碌时暂停一段时间
    """

    # 智能检测关键词
    BUSY_KEYWORDS = [
        "开会", "会议", "忙", "在忙", "工作",
        "睡了", "睡觉", "休息", "晚安",
        "别打扰", "勿扰", "免打扰",
    ]

    # 解除忙碌的关键词
    FREE_KEYWORDS = [
        "好了", "完了", "结束", "回来",
        "醒了", "早安", "早上好",
    ]

    def __init__(
        self,
        dnd_windows: list[DndWindow] | None = None,
        smart_detect: bool = True,
    ):
        self._dnd_windows = dnd_windows or [
            DndWindow(time(0, 0), time(7, 0), "深夜休息"),
        ]
        self._smart_detect = smart_detect
        self._pause_until: datetime | None = None
        self._last_user_message: str = ""

    def should_block(self, now: datetime | None = None) -> tuple[bool, str]:
        """
        检查是否应该阻止发送

        Returns:
            (是否阻止, 原因)
        """
        now = now or datetime.now(tz=timezone.utc)

        # 检查动态暂停
        if self._pause_until and now < self._pause_until:
            remaining = (self._pause_until - now).seconds // 60
            return True, f"用户忙碌中，{remaining}分钟后恢复"

        # 检查勿扰窗口
        current_time = now.time()
        for window in self._dnd_windows:
            if self._is_in_window(current_time, window):
                return True, window.reason

        return False, ""

    def _is_in_window(self, current: time, window: DndWindow) -> bool:
        """检查当前时间是否在勿扰窗口内"""
        if window.start <= window.end:
            return window.start <= current <= window.end
        else:
            # 跨午夜
            return current >= window.start or current <= window.end

    def process_user_message(self, message: str) -> None:
        """
        处理用户消息，检测忙碌状态

        Args:
            message: 用户消息
        """
        if not self._smart_detect:
            return

        self._last_user_message = message
        message.lower()

        # 检测忙碌关键词
        for keyword in self.BUSY_KEYWORDS:
            if keyword in message:
                self._pause(duration_minutes=120)  # 暂停2小时
                logger.info("Detected busy keyword '%s', pausing for 2 hours", keyword)
                return

        # 检测解除忙碌关键词
        for keyword in self.FREE_KEYWORDS:
            if keyword in message:
                self._resume()
                logger.info("Detected free keyword '%s', resuming", keyword)
                return

    def _pause(self, duration_minutes: int = 120) -> None:
        """暂停发送"""
        self._pause_until = datetime.now(tz=timezone.utc) + timedelta(minutes=duration_minutes)

    def _resume(self) -> None:
        """恢复发送"""
        self._pause_until = None

    def add_dnd_window(
        self,
        start: str,
        end: str,
        reason: str = "",
    ) -> None:
        """
        添加勿扰窗口

        Args:
            start: 开始时间 "HH:MM"
            end: 结束时间 "HH:MM"
            reason: 原因
        """
        try:
            start_parts = start.split(":")
            end_parts = end.split(":")

            window = DndWindow(
                start=time(int(start_parts[0]), int(start_parts[1])),
                end=time(int(end_parts[0]), int(end_parts[1])),
                reason=reason,
            )
            self._dnd_windows.append(window)
            logger.info("Added DND window: %s-%s (%s)", start, end, reason)
        except Exception as e:  # noqa: BLE001

            logger.warning("Failed to add DND window: %s", e)

    def remove_dnd_window(self, index: int) -> bool:
        """移除勿扰窗口"""
        if 0 <= index < len(self._dnd_windows):
            del self._dnd_windows[index]
            return True
        return False

    def list_dnd_windows(self) -> list[dict]:
        """列出所有勿扰窗口"""
        return [
            {
                "start": w.start.strftime("%H:%M"),
                "end": w.end.strftime("%H:%M"),
                "reason": w.reason,
            }
            for w in self._dnd_windows
        ]

    def get_status(self) -> dict:
        """获取当前状态"""
        blocked, reason = self.should_block()
        return {
            "blocked": blocked,
            "reason": reason,
            "pause_until": self._pause_until.isoformat() if self._pause_until else None,
            "dnd_windows": self.list_dnd_windows(),
        }
