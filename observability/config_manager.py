from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from observability.config_models import SystemConfig

logger = logging.getLogger("config_manager")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    from watchfiles import watch
    HAS_WATCHFILES = True
except ImportError:
    HAS_WATCHFILES = False


class ConfigManager:
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(os.path.abspath(config_dir))
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._config: Optional[SystemConfig] = None
        self._watcher = None
        self._callbacks = []

    @property
    def config(self) -> SystemConfig:
        if self._config is None:
            self._config = self._load()
        return self._config

    def _load(self) -> SystemConfig:
        data = {}
        system_yaml = self.config_dir / "system.yaml"
        if HAS_YAML and system_yaml.exists():
            with open(system_yaml, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}  # type: ignore
        env = os.environ.get("AI_GF_ENV", data.get("env", "dev"))
        env_yaml = self.config_dir / f"system_{env}.yaml"
        if HAS_YAML and env_yaml.exists():
            with open(env_yaml, "r", encoding="utf-8") as f:
                env_overrides = yaml.safe_load(f) or {}  # type: ignore
            data = self._deep_merge(data, env_overrides)
        for key in SystemConfig.model_fields:
            env_val = os.environ.get(f"AI_GF_{key.upper()}")
            if env_val is not None:
                data[key] = env_val
        try:
            return SystemConfig(**data)
        except Exception as e:
            logger.error("Config validation failed, using defaults: %s", e)
            return SystemConfig()

    def save(self, updates: dict) -> SystemConfig:
        """Merge updates into current config and persist to YAML."""
        current = self.config.model_dump()
        merged = self._deep_merge(current, updates)
        self._config = SystemConfig(**merged)
        system_yaml = self.config_dir / "system.yaml"
        if HAS_YAML:
            with open(system_yaml, "w", encoding="utf-8") as f:
                yaml.dump(self._config.model_dump(), f, default_flow_style=False, allow_unicode=True)  # type: ignore
            logger.info("Config saved to %s", system_yaml)
        return self._config

    def reload(self) -> SystemConfig:
        old = self._config
        self._config = self._load()
        if old != self._config:
            logger.info("Config reloaded (changed)")
            for cb in self._callbacks:
                try:
                    cb(old, self._config)
                except Exception as e:
                    logger.warning("Config change callback error: %s", e)
        return self._config

    def on_change(self, callback):
        self._callbacks.append(callback)

    def start_watching(self):
        if not HAS_WATCHFILES:
            logger.warning("watchfiles not installed, auto-reload disabled")
            return
        import asyncio

        async def _watch():
            async for changes in watch(self.config_dir):  # type: ignore[misc]
                logger.info("Config files changed: %s, reloading", changes)
                self.reload()

        self._watcher = asyncio.ensure_future(_watch())

    def stop_watching(self):
        if self._watcher is not None:
            self._watcher.cancel()
            self._watcher = None

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = ConfigManager._deep_merge(result[k], v)
            else:
                result[k] = v
        return result
