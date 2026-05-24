"""
LLM 提供商 — 对外统一接口

提供 get_llm() 工厂方法，自动根据配置选择 Provider：
  - deepseek    → LLMGatewayV2（功能完整：chat / chat_stream / chat_with_tools）
  - opencode_zen → OpenCodeZenProvider（免费，无需 API Key）

切换方式：修改 .env 中的 LLM_PROVIDER 即可
"""
import logging
import os
import threading
from typing import Dict, List, Optional

from .llm_gateway_v2 import LLMGatewayV2
from .opencode_zen_provider import DEFAULT_MODELS_PRIORITY, OpenCodeZenProvider

logger = logging.getLogger("llm_provider")

_instances: dict = {}
_instance_lock = threading.Lock()


def _resolve_provider(provider: str | None = None) -> str:
    if provider:
        return provider
    env_provider = os.environ.get("LLM_PROVIDER", "").strip()
    if env_provider:
        return env_provider
    config_provider = _load_provider_from_config()
    if config_provider:
        return config_provider
    return "opencode_zen"  # 默认用 OpenCode Zen 免费模型（无需 API Key）


def _load_provider_from_config() -> str | None:
    try:
        from pathlib import Path

        import yaml
        system_path = Path("config/system.yaml")
        if system_path.exists():
            with open(system_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("llm", {}).get("provider")
    except Exception:
        pass
    return None


def get_llm(provider: str | None = None, models_config: list[dict] | None = None):
    """
    获取 LLM 实例（线程安全，按 provider 缓存不同实例）

    Args:
        provider: 覆盖指定的 provider 名称，默认从 .env / system.yaml 读取
        models_config: 模型优先级列表，传给 provider 用于 fallback

    Returns:
        实现了 chat() / chat_stream() / chat_with_tools() / health_check() 的 provider 实例
    """
    resolved = _resolve_provider(provider)
    with _instance_lock:
        if resolved not in _instances:
            if resolved == "opencode_zen":
                _instances[resolved] = OpenCodeZenProvider(
                    models_config=models_config,
                )
                logger.info("Created OpenCodeZenProvider instance (free)")
            elif resolved == "deepseek":
                _instances[resolved] = LLMGatewayV2(
                    models_config=models_config,
                )
                logger.info("Created LLMGatewayV2 instance (DeepSeek)")
            else:
                logger.warning("Unknown provider '%s', falling back to opencode_zen", resolved)
                _instances[resolved] = OpenCodeZenProvider(
                    models_config=models_config,
                )
        return _instances[resolved]


def get_llm_names(provider: str | None = None) -> list:
    resolved = _resolve_provider(provider)
    if resolved == "opencode_zen":
        return [m["name"] for m in DEFAULT_MODELS_PRIORITY]
    return ["deepseek-chat", "deepseek-reasoner"]
