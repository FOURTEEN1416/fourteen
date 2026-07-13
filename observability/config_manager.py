from __future__ import annotations

import copy
import logging
import os
import re
import threading
from pathlib import Path

from observability.config_models import SystemConfig

logger = logging.getLogger("config_manager")

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: object) -> object:
    """递归解析字符串中的 ${VAR:-default} 和 ${VAR} 环境变量"""
    if isinstance(value, str):
        def _replace(match: re.Match) -> str:
            expr = match.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, "")
        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def _apply_dot_env_overrides(data: dict) -> None:
    """应用点号路径环境变量覆盖

    例如: AI_GF_LLM_CACHE_REDIS_HOST → data["llm"]["cache"]["redis"]["host"]
    """
    prefix = "AI_GF_"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        # 去掉前缀，分割路径
        path = env_key[len(prefix):].lower().split("_")
        target = data
        for i, part in enumerate(path):
            if i == len(path) - 1:
                target[part] = env_val
            else:
                if part not in target or not isinstance(target[part], dict):
                    target[part] = {}
                target = target[part]

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
        self._config: SystemConfig | None = None
        self._lock = threading.Lock()
        self._watcher = None
        self._callbacks = []  # type: ignore[var-annotated]

    @property
    def config(self) -> SystemConfig:
        with self._lock:
            if self._config is None:
                self._config = self._load()
            return self._config

    def _load(self) -> SystemConfig:
        data = {}  # type: ignore[var-annotated]
        env = "prod"
        system_yaml = self.config_dir / "system.yaml"
        if HAS_YAML and system_yaml.exists():
            with open(system_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                env = os.environ.get("AI_GF_ENV", data.get("env", "dev"))
        env_yaml = self.config_dir / f"system_{env}.yaml"
        if HAS_YAML and env_yaml.exists():
            with open(env_yaml, encoding="utf-8") as f:
                env_overrides = yaml.safe_load(f) or {}
                data = self._deep_merge(data, env_overrides)
        # 解析 ${VAR:-default} 环境变量占位符
        data = _resolve_env_vars(data)

        # 深层 AI_GF_* 环境变量覆盖（支持点号路径: AI_GF_LLM_CACHE_REDIS_HOST）
        for key in SystemConfig.model_fields:
            env_val = os.environ.get(f"AI_GF_{key.upper()}")
            if env_val is not None:
                data[key] = env_val
        # 追加 dot-notation 环境变量覆盖: AI_GF_LLM_CACHE_REDIS_HOST → data["llm"]["cache"]["redis"]["host"]
        _apply_dot_env_overrides(data)
        try:
            return SystemConfig(**data)
        except Exception as e:  # noqa: BLE001
            logger.error("Config validation failed, using defaults: %s", e)
            return SystemConfig()

    def save(self, updates: dict) -> SystemConfig:
        """Merge updates into current config and persist to YAML."""
        with self._lock:
            if self._config is None:
                self._config = self._load()
            current = self._config.model_dump()
            merged = self._deep_merge(current, updates)
            self._config = SystemConfig(**merged)
            system_yaml = self.config_dir / "system.yaml"
            if HAS_YAML:
                with open(system_yaml, "w", encoding="utf-8") as f:
                    yaml.dump(self._config.model_dump(), f, default_flow_style=False, allow_unicode=True)
                    logger.info("Config saved to %s", system_yaml)
            return self._config

    def reload(self) -> SystemConfig:
        with self._lock:
            old = self._config
            self._config = self._load()
        if old != self._config:
            logger.info("Config reloaded (changed)")
            for cb in self._callbacks:
                try:
                    cb(old, self._config)
                except Exception as e:  # noqa: BLE001
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
            async for changes in watch(self.config_dir):
                logger.info("Config files changed: %s, reloading", changes)
                self.reload()

        self._watcher = asyncio.ensure_future(_watch())

    def stop_watching(self):
        if self._watcher is not None:
            self._watcher.cancel()
            self._watcher = None

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = copy.deepcopy(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = ConfigManager._deep_merge(result[k], v)
            else:
                result[k] = copy.deepcopy(v)
        return result
