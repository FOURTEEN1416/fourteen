"""
配置加载器 — 加载 YAML 配置，提供默认值和热重载
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("character_config")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False
    logger.warning("PyYAML not installed, using fallback config")


class ConfigLoader:
    """
    配置加载器

    从 config/ 目录加载 YAML 配置文件，提供默认值兜底和热重载。
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(os.path.abspath(config_dir))
        self._cache: Dict[str, Any] = {}
        self._merged: Dict[str, Any] = {}
        self._reload_lock = threading.Lock()

        self.config_dir.mkdir(parents=True, exist_ok=True)

        logger.info("ConfigLoader initialized: %s", self.config_dir)

    def load_persona(self) -> dict:
        """加载人格配置"""
        return self._load_yaml("persona.yaml", self._default_persona())

    def load_emotion(self) -> dict:
        """加载情感状态机配置"""
        return self._load_yaml("emotion.yaml", self._default_emotion())

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        data = self._merged
        for k in keys:
            if isinstance(data, dict) and k in data:
                data = data[k]
            else:
                return default
        return data

    def reload(self) -> None:
        with self._reload_lock:
            new_cache = {}
            new_merged = {}
            _old_cache, _old_merged = self._cache, self._merged
            self._cache, self._merged = new_cache, new_merged
            self.load_persona()
            self.load_emotion()
        logger.info("All configs reloaded atomically")

    # ── 内部方法 ──────────────────────────────────────────────

    def _load_yaml(self, filename: str, default: dict) -> dict:
        """加载单个YAML文件"""
        filepath = self.config_dir / filename

        if not HAS_YAML:
            logger.warning("PyYAML unavailable, using defaults for %s", filename)
            self._cache[filename] = default
            return default

        try:
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    self._cache[filename] = data
                    self._merged.update(data)
                    logger.info("Loaded %s", filepath)
                    return data
            else:
                logger.warning("Config file not found: %s, using defaults", filepath)
        except Exception as e:
            logger.error("Failed to load %s: %s", filepath, e)

        self._cache[filename] = default
        return default

    # ── 默认配置 ──────────────────────────────────────────────

    @staticmethod
    def _default_persona() -> dict:
        return {
            "name": "十四",
            "core_anchors": [
                "表面傲娇，内心温柔",
                "在你面前才会展现脆弱",
                "嘴硬心软，从来不说实话",
                "嘴上嫌弃其实在乎得要命",
                "吃醋了也不会承认",
            ],
            "personality_traits": {
                "warmth": 0.8,
                "playfulness": 0.6,
                "independence": 0.7,
                "jealousy": 0.5,
                "stubbornness": 0.6,
            },
            "communication_style": {
                "greeting_morning": "早安呀～今天又比我先醒",
                "greeting_night": "还不睡？要不要我陪你会儿",
                "angry": "哼，不理你了（其实在等你哄）",
                "happy": "嘿嘿～今天心情好，赏你一句话",
                "jealous": "哦？她是谁？算了我不想知道",
            },
            "memory_settings": {
                "evolution_enabled": True,
            },
        }

    @staticmethod
    def _default_emotion() -> dict:
        return {
            "emotion": {
                "initial": {"emotion": "NEUTRAL", "energy": 1.0, "affinity": 0, "intensity": 0.5},
                "decay": {"intensity_per_minute": 0.001, "energy_recovery_per_hour": 0.05,
                          "energy_drain_per_message": 0.02},
            },
            "energy": {"max": 1.0, "min": 0.0, "recovery_rate": 0.05, "drain_per_reply": 0.02,
                       "critical_threshold": 0.2},
            "affection": {"max": 500, "per_positive_reply": 1.0, "per_negative_reply": -0.5,
                          "per_day_decay": 0.1, "per_miss_day": -1.0},
        }
