from __future__ import annotations

import contextlib
import copy
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from observability.config_models import SystemConfig

logger = logging.getLogger("config_manager")

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_MASKED_VALUE = "****"
_SENSITIVE_KEY_PARTS = ("api_key", "secret", "token", "password", "encryption_key")


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
        self._raw_config: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._watcher = None
        self._callbacks = []  # type: ignore[var-annotated]

    @property
    def config(self) -> SystemConfig:
        with self._lock:
            if self._config is None:
                self._config = self._load()
            return self._config

    def get_config_dict(self, *, resolve_env: bool = True) -> dict[str, Any]:
        """Return the complete effective config, including schema extensions.

        The YAML document remains the persistence source of truth.  This avoids
        silently dropping sections that are not represented by SystemConfig.
        """
        with self._lock:
            if self._config is None or self._raw_config is None:
                self._config = self._load()
            assert self._raw_config is not None
            data = self._effective_data(self._raw_config, resolve_env=resolve_env)
            return copy.deepcopy(data)

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not HAS_YAML or not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file must contain a mapping: {path}")
        return loaded

    def _effective_data(
        self,
        raw_base: dict[str, Any],
        *,
        resolve_env: bool = True,
    ) -> dict[str, Any]:
        data = copy.deepcopy(raw_base)
        env = os.environ.get("AI_GF_ENV", str(data.get("env", "dev")))
        env_yaml = self.config_dir / f"system_{env}.yaml"
        data = self._deep_merge(data, self._read_yaml(env_yaml))
        if resolve_env:
            data = _resolve_env_vars(data)  # type: ignore[assignment]
            for key in SystemConfig.model_fields:
                env_val = os.environ.get(f"AI_GF_{key.upper()}")
                if env_val is not None:
                    data[key] = env_val
            _apply_dot_env_overrides(data)
        return data

    def _load(self) -> SystemConfig:
        system_yaml = self.config_dir / "system.yaml"
        try:
            raw = self._read_yaml(system_yaml)
        except Exception as e:  # noqa: BLE001
            logger.error("Config file could not be read, using defaults: %s", e)
            self._raw_config = {}
            return SystemConfig()

        self._raw_config = raw
        try:
            return SystemConfig(**self._effective_data(raw))
        except Exception as e:  # noqa: BLE001
            # Keep the raw document in memory.  A later partial update may repair
            # the invalid field; discarding it here would erase unrelated config.
            logger.error("Config validation failed, using runtime defaults: %s", e)
            return SystemConfig()

    @staticmethod
    def _without_masked_secrets(updates: dict[str, Any]) -> dict[str, Any]:
        """Drop redaction placeholders so they can never replace real secrets."""
        cleaned: dict[str, Any] = {}
        for key, value in updates.items():
            lower_key = key.lower()
            is_sensitive = any(part in lower_key for part in _SENSITIVE_KEY_PARTS)
            if is_sensitive and value == _MASKED_VALUE:
                continue
            if isinstance(value, dict):
                cleaned[key] = ConfigManager._without_masked_secrets(value)
            else:
                cleaned[key] = copy.deepcopy(value)
        return cleaned

    @staticmethod
    def _atomic_dump_yaml(path: Path, data: dict[str, Any]) -> None:
        if not HAS_YAML:
            raise RuntimeError("PyYAML is required to persist configuration")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise

    def save(self, updates: dict[str, Any]) -> SystemConfig:
        """Merge into the original YAML and persist without losing extensions."""
        if not isinstance(updates, dict):
            raise TypeError("Config updates must be a mapping")
        with self._lock:
            if self._config is None or self._raw_config is None:
                self._config = self._load()
            assert self._raw_config is not None
            old = self._config
            cleaned = self._without_masked_secrets(updates)
            merged_raw = self._deep_merge(self._raw_config, cleaned)
            # Validate the exact effective result before replacing the file.
            validated = SystemConfig(**self._effective_data(merged_raw))
            system_yaml = self.config_dir / "system.yaml"
            self._atomic_dump_yaml(system_yaml, merged_raw)
            self._raw_config = merged_raw
            self._config = validated
        logger.info("Config saved to %s", system_yaml)
        if old != validated:
            self._notify_callbacks(old, validated)
        return validated

    def _notify_callbacks(
        self,
        old: SystemConfig | None,
        new: SystemConfig,
    ) -> None:
        for callback in tuple(self._callbacks):
            try:
                callback(old, new)
            except Exception as e:  # noqa: BLE001
                logger.warning("Config change callback error: %s", e)

    def reload(self) -> SystemConfig:
        with self._lock:
            old = self._config
            self._config = self._load()
        if old != self._config:
            logger.info("Config reloaded (changed)")
            self._notify_callbacks(old, self._config)
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
