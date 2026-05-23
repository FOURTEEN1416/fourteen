"""时间感知工具 - 工作日/节假日/农历"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict

from tool_system.base import BaseTool, ToolResult

logger = logging.getLogger("time_awareness_tool")

try:
    import chinese_calendar
    HAS_CHINESE_CALENDAR = True
except ImportError:
    HAS_CHINESE_CALENDAR = False

try:
    from lunarcalendar import Converter, Solar
    HAS_LUNAR = True
except ImportError:
    HAS_LUNAR = False

HOLIDAY_NAME_MAP = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "National Day": "国庆节",
    "Mid-autumn Festival": "中秋节",
    "Anti-Fascist 70th Day": "抗战胜利70周年",
}

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class TimeAwarenessTool(BaseTool):
    """时间感知工具"""
    name = "time_awareness"
    description = "获取时间信息：工作日/节假日判断、农历转换"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["current", "holiday", "lunar", "workday"],
                "description": "操作类型",
            },
            "date": {
                "type": "string",
                "description": "日期(YYYY-MM-DD)，默认今天",
            },
        },
        "required": ["action"],
    }

    def execute(self, action: str, date: str = "", **kwargs) -> ToolResult:
        target = self._parse_date(date) if date else date.today()
        handlers = {
            "current": self._get_current,
            "holiday": lambda: self._check_holiday(target),
            "lunar": lambda: self._convert_lunar(target),
            "workday": lambda: self._check_workday(target),
        }
        handler = handlers.get(action)
        if not handler:
            return ToolResult(False, error=f"未知操作: {action}")
        try:
            return handler()
        except Exception as e:
            logger.error("时间感知错误: %s", e)
            return ToolResult(False, error=str(e))

    def _get_current(self) -> ToolResult:
        now = datetime.now()
        today = now.date()
        result: Dict = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": WEEKDAY_CN[now.weekday()],
        }
        if HAS_CHINESE_CALENDAR:
            result["is_workday"] = chinese_calendar.is_workday(today)
            result["is_holiday"] = chinese_calendar.is_holiday(today)
            on_holiday, en_name = chinese_calendar.get_holiday_detail(today)
            if on_holiday:
                result["holiday_name"] = HOLIDAY_NAME_MAP.get(en_name, en_name)
        if HAS_LUNAR:
            lunar = Converter.Solar2Lunar(Solar(today.year, today.month, today.day))
            result["lunar"] = f"农历{lunar.month}月{lunar.day}"
        return ToolResult(True, data=result)

    def _check_holiday(self, d: date) -> ToolResult:
        if not HAS_CHINESE_CALENDAR:
            return ToolResult(False, error="请安装: pip install chinese-calendar")
        on_holiday, en_name = chinese_calendar.get_holiday_detail(d)
        return ToolResult(True, data={
            "date": str(d),
            "is_holiday": on_holiday,
            "is_workday": chinese_calendar.is_workday(d),
            "holiday_name": HOLIDAY_NAME_MAP.get(en_name, en_name) if on_holiday else None,
            "is_in_lieu": chinese_calendar.is_in_lieu(d),
        })

    def _check_workday(self, d: date) -> ToolResult:
        if not HAS_CHINESE_CALENDAR:
            return ToolResult(False, error="请安装: pip install chinese-calendar")
        return ToolResult(True, data={
            "date": str(d),
            "is_workday": chinese_calendar.is_workday(d),
            "is_holiday": chinese_calendar.is_holiday(d),
        })

    def _convert_lunar(self, d: date) -> ToolResult:
        if not HAS_LUNAR:
            return ToolResult(False, error="请安装: pip install lunarcalendar")
        lunar = Converter.Solar2Lunar(Solar(d.year, d.month, d.day))
        return ToolResult(True, data={
            "solar_date": str(d),
            "lunar_date": f"农历{lunar.year}年{lunar.month}月{lunar.day}日",
            "is_leap_month": lunar.isleap,
        })

    @staticmethod
    def _parse_date(date_str: str) -> date:
        y, m, d = map(int, date_str.split("-"))
        return date(y, m, d)
