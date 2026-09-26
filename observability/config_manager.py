from __future__ import annotations

import contextlib
import copy
import logging
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from observability.config_models import SystemConfig

logger = logging.getLogger("config_manager")

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")
_MASKED_VALUE = "****"
_SENSITIVE_KEY_PARTS = ("api_key", "secret", "token", "password", "encryption_key")
# 持久化版本号：随每次成功保存 +1 写回 YAML 根级。SystemConfig 对未声明字段
# 按 extra=ignore 处理，raw 文档往返不会丢失该键。
_VERSION_KEY = "config_version"


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
        # 本进程组件已应用到的持久化版本（W6 缺陷 A：版本可说明的前提）。
        # 启动装载时对齐文件版本；其他 worker 保存后本进程不再自动跟进
        # （无 watcher），status() 会如实报告 stale。
        self._version_applied: int = 0

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
        try:
            raw, version = self._read_persisted()
        except Exception as e:  # noqa: BLE001
            logger.error("Config file could not be read, using defaults: %s", e)
            self._raw_config = {}
            self._version_applied = 0
            return SystemConfig()

        self._raw_config = raw
        self._version_applied = version
        try:
            return SystemConfig(**self._effective_data(raw))
        except Exception as e:  # noqa: BLE001
            # Keep the raw document in memory.  A later partial update may repair
            # the invalid field; discarding it here would erase unrelated config.
            logger.error("Config validation failed, using runtime defaults: %s", e)
            return SystemConfig()

    def _read_persisted(self) -> tuple[dict[str, Any], int]:
        """读磁盘上的 system.yaml 及其持久化版本号（不触碰进程缓存）。"""
        raw = self._read_yaml(self.config_dir / "system.yaml")
        try:
            version = int(raw.get(_VERSION_KEY, 0))
        except (TypeError, ValueError):
            version = 0
        return raw, version

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
        """兼容入口：merge 进磁盘最新 YAML 并持久化，返回验证后的配置。"""
        return self.save_with_receipt(updates)["config"]

    @contextlib.contextmanager
    def _yaml_file_lock(self):
        """跨进程排他锁（msvcrt/fcntl）：serialize 读盘-merge-写盘整个临界区。

        W6 缺陷 A 根治的一半：多 worker 并发保存时，后到者必须在锁内重读
        磁盘最新版本再 merge，否则会按本进程陈旧缓存覆盖掉对方刚写的字段。
        与 orchestrator.session_locks.ProcessSessionLock 同构的 win32/POSIX 双实现。
        """
        lock_path = self.config_dir / ".system.yaml.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            import time as _time

            deadline = _time.monotonic() + 10.0
            while True:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if _time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"config save lock timeout: {lock_path}"
                        ) from None
                    _time.sleep(0.05)
            yield
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    if sys.platform == "win32":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def save_with_receipt(
        self,
        updates: dict[str, Any],
        *,
        live_components: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """基于锁内磁盘最新版本 merge 保存，返回可审计的保存回执。

        回执字段：
        - ``config``：验证后的 SystemConfig；
        - ``persisted_version``：本次写入磁盘的版本（锁内从磁盘版本 +1）；
        - ``effective_version`` / ``in_sync``：本进程组件实际应用到的版本；
          只有调用方传入 ``live_components`` 且存在 live 字段时才追平，
          否则如实保留旧值（诚实原则：保存成功 ≠ 本进程已生效）。
        - ``applied_live`` / ``restart_required`` / ``unsupported`` / ``field_status``：
          按字段声明的生效语义。
        """
        if not isinstance(updates, dict):
            raise TypeError("Config updates must be a mapping")
        cleaned = self._without_masked_secrets(updates)
        system_yaml = self.config_dir / "system.yaml"
        with self._lock:
            with self._yaml_file_lock():
                # 关键：merge 基准是**锁内重读的磁盘最新版**，不是本进程缓存。
                disk_raw, disk_version = self._read_persisted()
                merged_raw = self._deep_merge(disk_raw, cleaned)
                merged_raw[_VERSION_KEY] = disk_version + 1
                # Validate the exact effective result before replacing the file.
                validated = SystemConfig(**self._effective_data(merged_raw))
                self._atomic_dump_yaml(system_yaml, merged_raw)
            old = self._config
            self._raw_config = merged_raw
            self._config = validated
            new_version = disk_version + 1

            field_status = self._status_for_updates(updates)
            applied_live: list[str] = []
            if live_components is not None:
                applied_live = self.apply_live(live_components)
                if applied_live:
                    # 本进程组件已追平新版本
                    self._version_applied = new_version
            receipt: dict[str, Any] = {
                "config": validated,
                "persisted_version": new_version,
                "effective_version": self._version_applied,
                "in_sync": self._version_applied == new_version,
                "applied_live": applied_live,
                "restart_required": [
                    path
                    for path, meta in field_status.items()
                    if meta["status"] == "restart_required"
                ],
                "unsupported": [
                    {"field": path, **meta}
                    for path, meta in field_status.items()
                    if meta["status"] == "unsupported"
                ],
                "field_status": field_status,
            }
        logger.info("Config saved to %s (v%d)", system_yaml, new_version)
        if old != validated:
            self._notify_callbacks(old, validated)
        return receipt

    # ── 字段生效语义声明（W6 任务 1/2）────────────────────────

    _CAPABILITY_DECLARATION: dict[str, dict[str, str]] = {
        "llm": {"status": "live", "reason": "reconfigure_llm 重建请求级网关"},
        "safety.input_filter_enabled": {"status": "live"},
        "safety.output_filter_enabled": {"status": "live"},
        "safety.self_harm_intervention": {
            "status": "live",
            "reason": "消费已接线；是否允许用户侧关闭属产品裁决（W6 登记，未自决）",
        },
        "safety.pii_anonymizer_enabled": {"status": "live"},
        "safety.prompt_injection_detection": {"status": "live"},
        "safety.encryption_enabled": {
            "status": "unsupported",
            "reason": "静态加密无存储链（chat_history 明文绑定 INSERT）："
            "开启不产生任何加密效果，本进程拒绝应用",
        },
        "safety.encryption_key_env": {
            "status": "restart_required",
            "reason": "EncryptionManager 仅在启动时读取环境变量",
        },
    }

    def capability_declaration(self) -> dict[str, dict[str, str]]:
        """按字段声明 live / restart_required / unsupported（只读副本）。"""
        return {k: dict(v) for k, v in self._CAPABILITY_DECLARATION.items()}

    def _status_for_updates(self, updates: dict[str, Any]) -> dict[str, dict[str, str]]:
        """对本次 updates 涉及的字段路径给出生效语义声明。"""
        declared: dict[str, dict[str, str]] = {}
        for top_key, value in updates.items():
            section_meta = self._CAPABILITY_DECLARATION.get(top_key)
            if isinstance(value, dict) and section_meta is None:
                for sub_key in value:
                    path = f"{top_key}.{sub_key}"
                    declared[path] = dict(
                        self._CAPABILITY_DECLARATION.get(
                            path, {"status": "restart_required"}
                        )
                    )
            else:
                declared[top_key] = dict(
                    section_meta or {"status": "restart_required"}
                )
        return declared

    def apply_live(self, components: dict[str, Any]) -> list[str]:
        """把 live 字段应用到本进程运行组件，返回实际应用的字段路径。

        鸭子类型匹配组件（orchestrator 装配归 W3，本方法不 import 组件类）。
        ``encryption`` 组件被**有意跳过**：静态加密无存储链，应用它等于谎报生效。
        """
        # 注意：save_with_receipt 在持有 self._lock 的情况下调用本方法，
        # 此处必须用已就绪的 self._config，不得走会二次加锁的 self.config。
        cfg = self._config if self._config is not None else self.config
        applied: list[str] = []
        safety = components.get("safety")
        if safety is not None and hasattr(safety, "input_enabled"):
            safety.input_enabled = cfg.safety.input_filter_enabled
            safety.output_enabled = cfg.safety.output_filter_enabled
            safety.self_harm_intervention = cfg.safety.self_harm_intervention
            # 旧 master 开关：任一方向开启即视为过滤活跃（/api/safety/stats 读它）
            safety.enabled = (
                cfg.safety.input_filter_enabled or cfg.safety.output_filter_enabled
            )
            applied += [
                "safety.input_filter_enabled",
                "safety.output_filter_enabled",
                "safety.self_harm_intervention",
            ]
        pii = components.get("pii")
        if pii is not None and hasattr(pii, "enabled"):
            pii.enabled = cfg.safety.pii_anonymizer_enabled
            applied.append("safety.pii_anonymizer_enabled")
        injection = components.get("injection")
        if injection is not None and hasattr(injection, "enabled"):
            injection.enabled = cfg.safety.prompt_injection_detection
            applied.append("safety.prompt_injection_detection")
        return applied

    def status(self) -> dict[str, Any]:
        """本 worker 的版本可说明性：磁盘持久版本 vs 本进程已应用版本。"""
        try:
            _, persisted = self._read_persisted()
        except Exception:  # noqa: BLE001
            persisted = self._version_applied
        return {
            "persisted_version": persisted,
            "effective_version": self._version_applied,
            "stale": persisted != self._version_applied,
        }

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
