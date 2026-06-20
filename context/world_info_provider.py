from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class WorldInfoProvider:
    """动态世界信息注入器。

    提供时间、时段、季节、节日等上下文，用于人格 prompt 动态组装。
    不依赖外部 API，完全离线计算，确保 demo/生产环境都能稳定工作。
    """

    # 时段 -> 氛围词映射
    TIME_SEGMENTS = [
        (5, "清晨", "天刚亮，适合轻声问候"),
        (8, "上午", "一天刚开始"),
        (12, "中午", "该吃饭了"),
        (14, "下午", "午后容易犯困"),
        (18, "傍晚", "快下班/放学了"),
        (22, "晚上", "可以放松了"),
        (24, "深夜", "该睡了，如果睡不着就陪我说说话"),
    ]

    SEASONS = {
        3: "春天", 4: "春天", 5: "春天",
        6: "夏天", 7: "夏天", 8: "夏天",
        9: "秋天", 10: "秋天", 11: "秋天",
        12: "冬天", 1: "冬天", 2: "冬天",
    }

    WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    # 常见农历/公历节日（简化版）
    FIXED_HOLIDAYS: dict[str, str] = {
        "01-01": "元旦",
        "02-14": "情人节",
        "03-08": "妇女节",
        "04-01": "愚人节",
        "05-01": "劳动节",
        "06-01": "儿童节",
        "10-01": "国庆节",
        "10-24": "程序员节",
        "12-24": "平安夜",
        "12-25": "圣诞节",
    }

    def __init__(self, timezone_offset: int = 8):
        """初始化。

        Args:
            timezone_offset: 时区偏移（小时），默认东八区。
        """
        self._tz_offset = timezone_offset

    def _local_now(self) -> datetime:
        from datetime import timedelta
        return datetime.now(tz=timezone.utc) + timedelta(hours=self._tz_offset)

    def get_segment(self, dt: datetime | None = None) -> dict[str, str]:
        """获取当前时段信息。"""
        dt = dt or self._local_now()
        hour = dt.hour
        for threshold, name, mood in self.TIME_SEGMENTS:
            if hour < threshold:
                return {"name": name, "mood": mood}
        # fallback
        return {"name": "深夜", "mood": "夜深了"}

    def get_season(self, dt: datetime | None = None) -> str:
        dt = dt or self._local_now()
        return self.SEASONS.get(dt.month, "")

    def get_holiday(self, dt: datetime | None = None) -> str:
        dt = dt or self._local_now()
        key = dt.strftime("%m-%d")
        return self.FIXED_HOLIDAYS.get(key, "")

    def get_world_info(self) -> dict[str, Any]:
        """获取完整世界信息字典。"""
        dt = self._local_now()
        segment = self.get_segment(dt)
        return {
            "datetime": dt.isoformat(timespec="seconds"),
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "weekday": self.WEEKDAYS[dt.weekday()],
            "segment": segment["name"],
            "segment_mood": segment["mood"],
            "season": self.get_season(dt),
            "holiday": self.get_holiday(dt),
            "year": dt.year,
            "month": dt.month,
            "day": dt.day,
            "hour": dt.hour,
        }

    def render(self) -> str:
        """渲染为 prompt 可用的自然语言字符串。"""
        info = self.get_world_info()
        parts = [
            f"现在是 {info['date']} {info['weekday']} {info['time']}，{info['segment']}。",
        ]
        if info["season"]:
            parts.append(f"季节：{info['season']}。")
        if info["holiday"]:
            parts.append(f"今天/明天是 {info['holiday']}，可以留意一下。")
        parts.append(f"{info['segment_mood']}。")
        return "\n".join(parts)
