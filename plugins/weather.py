"""
天气插件 — 通过 OpenWeatherMap API 获取天气

用于场景触发器：下雨提醒带伞、高温提醒喝水等
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("weather_plugin")

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# 天气描述 → 触发类型映射
WEATHER_TRIGGERS = {
    "rain": ["雨", "雷阵雨", "小雨", "中雨", "大雨", "暴雨"],
    "snow": ["雪", "小雪", "中雪", "大雪"],
    "hot": ["晴"],  # 高温时触发（>35°C）
    "cold": ["多云", "阴"],  # 低温时触发（<5°C）
}


class WeatherPlugin:
    """
    天气查询插件

    提供天气场景触发所需的数据。
    使用 OpenWeatherMap API（免费版足够）。
    """

    def __init__(self, api_key: str = "", city: str = "Shanghai"):
        self.api_key = api_key or os.environ.get("OPENWEATHERMAP_API_KEY", "")
        self.city = city
        self._cache: dict[str, Any] | None = None
        self._cache_time: datetime | None = None
        self._cache_ttl = timedelta(minutes=30)  # 缓存30分钟

    def get_weather(self) -> dict[str, Any] | None:
        """
        获取当前天气

        Returns:
            {"condition": str, "temp": float, "humidity": float,
             "wind": float, "description": str, "icon": str}
        """
        # 检查缓存
        if self._cache and self._cache_time and datetime.now() - self._cache_time < self._cache_ttl:
            return self._cache

        # 使用 API
        if self.api_key and HAS_HTTPX:
            return self._fetch_from_api()

        # 无 API 时模拟数据
        return self._simulate()

    def check_trigger(self) -> dict[str, Any] | None:
        """
        检查天气是否触发关心场景

        Returns:
            触发时返回 {"type": str, "message": str}, 否则 None
        """
        weather = self.get_weather()
        if not weather:
            return None

        condition = weather.get("condition", "")
        temp = weather.get("temp", 20)

        if condition in ["rain", "thunderstorm", "drizzle"]:
            return {
                "type": "weather_rain",
                "message": "今天下雨了，带伞了吗？别淋湿了",
            }

        if condition in ["snow"]:
            return {
                "type": "weather_snow",
                "message": "下雪了！注意保暖，别着凉了",
            }

        if temp > 35:
            return {
                "type": "weather_hot",
                "message": f"今天{temp}°C，好热...记得多喝水",
            }

        if temp < 5:
            return {
                "type": "weather_cold",
                "message": f"今天才{temp}°C，多穿点！",
            }

        return None

    def get_description(self) -> str:
        """获取天气描述文本"""
        weather = self.get_weather()
        if not weather:
            return "天气数据获取失败"

        desc = weather.get("description", "")
        temp = weather.get("temp", 0)
        return f"{self.city} {desc} {temp}°C"

    # ── API 模式 ─────────────────────────────────────────

    def _fetch_from_api(self) -> dict[str, Any] | None:
        """从 OpenWeatherMap API 获取"""
        if not HAS_HTTPX:
            return self._simulate()

        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?q={self.city}&appid={self.api_key}&units=metric&lang=zh_cn"
        )

        try:
            with httpx.Client(timeout=10) as client:  # type: ignore
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()

                result = {
                    "condition": data["weather"][0]["main"].lower(),
                    "description": data["weather"][0]["description"],
                    "temp": data["main"]["temp"],
                    "humidity": data["main"]["humidity"],
                    "wind": data["wind"]["speed"],
                    "icon": data["weather"][0]["icon"],
                }

                # 更新缓存
                self._cache = result
                self._cache_time = datetime.now()

                return result

        except Exception as e:
            logger.warning("Weather API failed: %s", e)
            return self._simulate()

    # ── 模拟模式（无 API Key 时用） ─────────────────────

    def _simulate(self) -> dict[str, Any]:
        """模拟天气数据（开发/测试用）"""
        import random

        conditions = ["clear", "clouds", "rain", "clear", "clouds"]
        descriptions = ["晴", "多云", "小雨", "晴", "多云"]

        idx = random.randint(0, len(conditions) - 1)

        result = {
            "condition": conditions[idx],
            "description": descriptions[idx],
            "temp": random.randint(15, 32),
            "humidity": random.randint(40, 80),
            "wind": random.randint(0, 15),
            "icon": "01d",
        }

        self._cache = result
        self._cache_time = datetime.now()
        return result

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "api_configured": bool(self.api_key),
            "httpx_available": HAS_HTTPX,
            "city": self.city,
            "cached": self._cache is not None,
        }
