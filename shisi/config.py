"""十四模块配置加载器 — YAML加载 + 环境变量覆盖。"""

import os
from pathlib import Path
from typing import Any

import yaml

_CONFIG: dict[str, Any] | None = None
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "shisi.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_env_overrides(config: dict, prefix: str = "AIYU") -> dict:
    env_mappings = {
        f"{prefix}_CHARACTER_MAX_ACTIVE": ("character", "max_active", int),
        f"{prefix}_CHARACTER_DEFAULT": ("character", "default_character_id", str),
        f"{prefix}_AFFINITY_DECAY_RATE": ("affinity", "decay_rate", float),
        f"{prefix}_AFFINITY_GRACE_DAYS": ("affinity", "grace_period_days", int),
        f"{prefix}_AFFINITY_MAX": ("affinity", "max_value", int),
        f"{prefix}_AFFINITY_MIN": ("affinity", "min_value", int),
        f"{prefix}_WECHAT_RATE_LIMIT": ("wechat", "rate_limit_per_minute", int),
        f"{prefix}_STICKER_MAX_SIZE_MB": ("sticker", "max_file_size_mb", int),
        f"{prefix}_MEMORY_RECYCLE_DAYS": ("memory_ext", "recycle_bin_days", int),
    }
    for env_key, (section, key, cast) in env_mappings.items():
        val = os.environ.get(env_key)
        if val is not None:
            config.setdefault(section, {})[key] = cast(val)
    return config


def load_config(config_path: Path | str | None = None, force_reload: bool = False) -> dict[str, Any]:
    global _CONFIG
    if _CONFIG is not None and not force_reload:
        return _CONFIG

    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"shisi config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    config = _apply_env_overrides(config)
    _CONFIG = config
    return _CONFIG


def get_config(section: str | None = None, key: str | None = None, default: Any = None) -> Any:
    config = load_config()
    if section is None:
        return config
    sec = config.get(section, {})
    if key is None:
        return sec
    return sec.get(key, default)


def reset_config() -> None:
    global _CONFIG
    _CONFIG = None
