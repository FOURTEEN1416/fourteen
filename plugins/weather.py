"""
天气插件 — 通过 OpenWeatherMap API 获取天气

用于场景触发器：下雨提醒带伞、高温提醒喝水等

W6 缺陷 E 根治：
- 旧实现无 key / API 失败时静默返回**随机假数据**并写入缓存（天气造假）；
  现改为返回 None（失败语义），仅显式 allow_simulation=True 时模拟，
  且结果带 source="simulation"、不写缓存。
- 旧缓存是单槽（_cache/_cache_time），上海查完查北京会命中上海的缓存；
  现按城市分键，各城独立 TTL。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
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

    def __init__(
        self,
        api_key: str = "",
        city: str = "Shanghai",
        *,
        allow_simulation: bool = False,
        cache_ttl_minutes: int = 30,
    ):
        self.api_key = api_key or os.environ.get("OPENWEATHERMAP_API_KEY", "")
        self.city = city
        self.allow_simulation = allow_simulation
        self._cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)  # 每城缓存30分钟

    def get_weather(self, city: str = "") -> dict[str, Any] | None:
        """
        获取当前天气；无 key / 服务失败时返回 None（不造假）。

        Returns:
            {"city": str, "condition": str, "temp": float, "humidity": float,
             "wind": float, "description": str, "icon": str, "source": "openweathermap"}
            模拟模式（显式开启）时 source="simulation"。
        """
        target = str(city or self.city)
        if not target:
            return None

        # 检查该城市自己的缓存
        now = datetime.now(tz=timezone.utc)
        with self._cache_lock:
            hit = self._cache.get(target)
            if hit and now - hit[0] < self._cache_ttl:
                return dict(hit[1])

        # 使用 API（缺 key 或缺 httpx 不再回落模拟）
        if self.api_key and HAS_HTTPX:
            data = self._fetch_from_api(target)
            if data is not None:
                return data
            if self.allow_simulation:
                return self._simulate(target)
            return None

        # 无 API：仅显式允许时给标记为 simulation 的演示数据（不写缓存）
        if self.allow_simulation:
            return self._simulate(target)
        return None

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

    def _fetch_from_api(self, city: str) -> dict[str, Any] | None:
        """从 OpenWeatherMap API 获取；失败返回 None，不写缓存不造假。"""
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": city, "appid": self.api_key, "units": "metric", "lang": "zh_cn"}

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                result = {
                    "city": city,
                    "condition": data["weather"][0]["main"].lower(),
                    "description": data["weather"][0]["description"],
                    "temp": data["main"]["temp"],
                    "humidity": data["main"]["humidity"],
                    "wind": data["wind"]["speed"],
                    "icon": data["weather"][0]["icon"],
                    "source": "openweathermap",
                }

                # 更新该城市的缓存
                with self._cache_lock:
                    self._cache[city] = (datetime.now(tz=timezone.utc), result)

                return result

        except Exception as e:  # noqa: BLE001
            logger.warning("Weather API failed for %s: %s", city, e)
            return None

    # ── 模拟模式（仅显式 allow_simulation=True 时可达，标记 source） ──

    def _simulate(self, city: str) -> dict[str, Any]:
        """模拟天气数据（演示/开发用）；**不写缓存**，防止假数据毒化真实查询。"""
        import random

        conditions = ["clear", "clouds", "rain", "clear", "clouds"]
        descriptions = ["晴", "多云", "小雨", "晴", "多云"]

        idx = random.randint(0, len(conditions) - 1)

        return {
            "city": city,
            "condition": conditions[idx],
            "description": descriptions[idx],
            "temp": random.randint(15, 32),
            "humidity": random.randint(40, 80),
            "wind": random.randint(0, 15),
            "icon": "01d",
            "source": "simulation",
        }

    def health_check(self) -> dict:
        """健康检查（反映配置状态，不做网络 IO）"""
        return {
            "api_configured": bool(self.api_key),
            "httpx_available": HAS_HTTPX,
            "simulation_enabled": self.allow_simulation,
            "city": self.city,
            "cached_cities": sorted(self._cache.keys()),
        }
