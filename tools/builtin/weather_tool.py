from __future__ import annotations

import logging
from typing import Any

from tools import tool_state
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
        """反映配置状态：插件/key/模拟开关/降级通道，不做网络 IO。"""
        if not HAS_WEATHER or self._plugin is None:
            return {
                "available": True,
                "error": "",
                "note": "插件不可用，仅 wttr.in 公开接口降级",
                "api_configured": False,
                "plugin_enabled": tool_state.is_plugin_enabled("weather"),
                "simulation_enabled": False,
                "fallback": "wttr.in",
            }
        plugin_enabled = tool_state.is_plugin_enabled("weather")
        note = ""
        if not plugin_enabled:
            note = "天气插件已停用，走 wttr.in 公开接口"
        elif not self._plugin.api_key:
            note = "未配置 OPENWEATHERMAP_API_KEY，走 wttr.in 公开接口"
        return {
            "available": True,
            "error": "",
            "note": note,
            "api_configured": bool(self._plugin.api_key),
            "plugin_enabled": plugin_enabled,
            "simulation_enabled": self._plugin.allow_simulation,
            "fallback": "wttr.in",
        }

    def execute(self, city: str = "", **kwargs) -> ToolResult:
        target_city = city or "Shanghai"
        # W6 缺陷 F：插件开关的运行时消费者——停用即跳过 OpenWeatherMap 能力
        if self._plugin and tool_state.is_plugin_enabled("weather"):
            try:
                weather = self._plugin.get_weather(target_city)
                if weather:
                    # 模拟数据只在显式开启时出现且带 source=simulation 标记，
                    # 不再是无 key 时的静默假成功。
                    return ToolResult(True, data=weather)
                # 插件无数据（无 key / API 失败）→ 落到下方 wttr.in 真实降级
            except Exception:
                logger.exception("插件获取天气失败，尝试降级")
        return self._fetch_wttr(target_city)

    def _fetch_wttr(self, city: str) -> ToolResult:
        """无插件数据时的公开天气降级（wttr.in）。失败即明确失败，不造假。"""
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
