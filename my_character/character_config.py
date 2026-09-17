"""
配置加载器 — 加载 YAML 配置，提供默认值和热重载
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

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
        self._cache: dict[str, Any] = {}
        self._merged: dict[str, Any] = {}
        self._reload_lock = threading.Lock()

        self.config_dir.mkdir(parents=True, exist_ok=True)

        logger.info("ConfigLoader initialized: %s", self.config_dir)

    def load_persona(self, merged_target: dict | None = None) -> dict:
        """加载人格配置

        Args:
            merged_target: 仅 reload() 内部使用——把解析结果合并到暂存字典，
                待全部文件加载成功后再整体发布，避免读者看到半成品配置。
        """
        return self._load_yaml("persona.yaml", self._default_persona(), merged_target)

    def load_emotion(self, merged_target: dict | None = None) -> dict:
        """加载情感状态机配置（merged_target 语义同 load_persona）"""
        return self._load_yaml("emotion.yaml", self._default_emotion(), merged_target)

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
        """重载全部配置。

        原子性契约（2026-09-17 修复）：
        - 旧实现先把 ``_cache``/``_merged`` 置空再逐个加载，一旦加载中途抛错，
          配置就**永久丢失**（此后所有 ``get()`` 返回默认值）——名为原子，实为
          "先毁后建"。``_old_cache``/``_old_merged`` 两个变量赋值后从未使用，
          是残留的死代码。
        - 现改为：先把两个文件解析进**暂存字典**，全部成功后才用**单次引用赋值**
          发布；任何异常都回滚到重载前状态。读者要么看到旧表、要么看到新表，
          不会看到只加载了一半的表。
        """
        with self._reload_lock:
            # 只需保存 _cache：_merged 在暂存表填满前不会被改写（见下方发布点），
            # 异常路径下它仍是重载前的旧表，无需回滚。
            prev_cache = self._cache
            staging_cache: dict[str, Any] = {}
            staging_merged: dict[str, Any] = {}
            # _cache 仅本类写入、无外部读者，可先切换；_merged 是读者可见的，
            # 必须等暂存表填满后才发布。
            self._cache = staging_cache
            try:
                self.load_persona(merged_target=staging_merged)
                self.load_emotion(merged_target=staging_merged)
            except Exception:
                self._cache = prev_cache
                logger.exception("配置重载失败，已保留重载前配置")
                raise
            self._merged = staging_merged
        logger.info("All configs reloaded atomically")

    # ── 内部方法 ──────────────────────────────────────────────

    def _load_yaml(self, filename: str, default: dict,
                   merged_target: dict | None = None) -> dict:
        """加载单个 YAML 文件。

        Args:
            merged_target: 合并目标；None 时直接写 ``self._merged``（常规加载），
                reload() 传暂存字典以实现原子发布。
        """
        filepath = self.config_dir / filename
        target = self._merged if merged_target is None else merged_target

        if not HAS_YAML:
            logger.warning("PyYAML unavailable, using defaults for %s", filename)
            self._cache[filename] = default
            target.update(default)
            return default

        try:
            if filepath.exists():
                with open(filepath, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    self._cache[filename] = data
                    target.update(data)
                    logger.info("Loaded %s", filepath)
                    return data
            else:
                logger.warning("Config file not found: %s, using defaults", filepath)
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to load %s: %s", filepath, e)

        self._cache[filename] = default
        target.update(default)
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
