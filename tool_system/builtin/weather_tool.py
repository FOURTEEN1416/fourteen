from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from tool_system.base import BaseTool, ToolResult

logger = logging.getLogger("weather_tool")

try:
    from plugins.weather import WeatherPlugin
    HAS_WEATHER = True
except ImportError:
    HAS_WEATHER = False


class WeatherTool(BaseTool):
    name = "get_weather"
    description = "获取当前天气信息，包括温度、天气状况等"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，默认Shanghai",
            },
        },
        "required": [],
    }

    def __init__(self, api_key: str = "", city: str = "Shanghai"):
        self._plugin = WeatherPlugin(api_key=api_key, city=city) if HAS_WEATHER else None

    def execute(self, city: str = "", **kwargs) -> ToolResult:
        if not self._plugin:
            return ToolResult(False, error="Weather plugin not available")
        if city:
            self._plugin.city = city
        try:
            weather = self._plugin.get_weather()
            if weather:
                return ToolResult(True, data=weather)
            return ToolResult(False, error="Failed to get weather data")
        except Exception as e:
            return ToolResult(False, error=str(e))
