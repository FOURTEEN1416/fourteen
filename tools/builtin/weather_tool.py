from __future__ import annotations

import logging

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("weather_tool")

try:
    from plugins.weather import WeatherPlugin
    HAS_WEATHER = True
except ImportError:
    HAS_WEATHER = False


class WeatherTool(BaseTool):
    name = "weather"
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

    def health_check(self) -> dict[str, Any]:
        if not self._plugin:
            return {"available": True, "error": "", "note": "使用 wttr.in 公开接口降级"}
        return {"available": True, "error": ""}

    def execute(self, city: str = "", **kwargs) -> ToolResult:
        target_city = city or "Shanghai"
        if self._plugin:
            try:
                self._plugin.city = target_city
                weather = self._plugin.get_weather()
                if weather:
                    return ToolResult(True, data=weather)
            except Exception:
                logger.exception("插件获取天气失败，尝试降级")
        return self._fetch_wttr(target_city)

    def _fetch_wttr(self, city: str) -> ToolResult:
        """无插件时的公开天气降级（wttr.in）。"""
        try:
            import requests
            url = f"https://wttr.in/{city}?format=j1"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            return ToolResult(True, data={
                "city": city,
                "temperature": current.get("temp_C"),
                "condition": current.get("weatherDesc", [{}])[0].get("value", ""),
                "humidity": current.get("humidity"),
                "source": "wttr.in",
            })
        except Exception as e:
            logger.exception("wttr.in 降级获取天气失败")
            return ToolResult(False, error=f"天气服务暂不可用: {e}")
