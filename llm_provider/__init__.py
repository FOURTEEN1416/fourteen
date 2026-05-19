"""
LLM 提供商 — 对外统一接口

提供 get_llm() 工厂方法，返回一个 callable(query, system_prompt) → str
"""
import logging
import os
import threading
from typing import Optional

from .deepseek_gateway import DeepSeekGateway
from .opencode_zen_provider import OpenCodeZenProvider, DEFAULT_MODELS_PRIORITY

logger = logging.getLogger("llm_provider")

_instances: dict = {}
_instance_lock = threading.Lock()


def _resolve_provider(provider: Optional[str] = None) -> str:
    if provider:
        return provider
    env_provider = os.environ.get("LLM_PROVIDER", "").strip()
    if env_provider:
        return env_provider
    config_provider = _load_provider_from_config()
    if config_provider:
        return config_provider
    return "opencode_zen"  # 默认用 OpenCode Zen 免费模型（无需 API Key）


def _load_provider_from_config() -> Optional[str]:
    try:
        import json
        from pathlib import Path
        cowagent_path = Path("config/cowagent_config.json")
        if cowagent_path.exists():
            with open(cowagent_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("provider")
    except Exception:
        pass
    try:
        import yaml
        from pathlib import Path
        system_path = Path("config/system.yaml")
        if system_path.exists():
            with open(system_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("llm", {}).get("provider")
    except Exception:
        pass
    return None


def get_llm(provider: Optional[str] = None):
    """获取 LLM 实例（线程安全，按 provider 缓存不同实例）"""
    resolved = _resolve_provider(provider)
    with _instance_lock:
        if resolved not in _instances:
            if resolved == "opencode_zen":
                _instances[resolved] = OpenCodeZenProvider()
                logger.info("Created OpenCodeZenProvider instance")
            elif resolved == "deepseek":
                _instances[resolved] = DeepSeekGateway()
                logger.info("Created DeepSeekGateway instance")
            else:
                logger.warning("Unknown provider '%s', falling back to opencode_zen", resolved)
                _instances[resolved] = OpenCodeZenProvider()
        return _instances[resolved]


def get_llm_names(provider: Optional[str] = None) -> list:
    resolved = _resolve_provider(provider)
    if resolved == "opencode_zen":
        return [m["name"] for m in DEFAULT_MODELS_PRIORITY]
    return ["deepseek-chat", "deepseek-reasoner"]
