"""
LLM 提供商 — 对外统一接口

架构升级 V3 (多供应商网关):
  默认使用 智谱AI → 讯飞星火 → 百度千帆 → OpenCode Zen 的 fallback 链
  用户可自行配置 DeepSeek（前端引导），配置后自动插入优先位置

使用方式:
  llm = get_llm()
  result = await llm.chat("你好")
  
  或手动指定 provider:
  llm = get_llm(provider="deepseek")
"""

import logging
import os
import threading
from typing import Any

from .multi_provider_gateway import MultiProviderGateway

logger = logging.getLogger("llm_provider")

_instances: dict[str, Any] = {}
_instance_lock = threading.Lock()


def _resolve_provider(provider: str | None = None) -> str:
    """解析要使用的 provider 名称"""
    if provider:
        return provider
    env_provider = os.environ.get("LLM_PROVIDER", "").strip()
    if env_provider:
        return env_provider
    return "auto"  # 自动模式 = MultiProviderGateway


def get_llm(provider: str | None = None, models_config: list[dict] | None = None) -> Any:
    """
    获取 LLM 实例（线程安全，按 provider 缓存）

    Args:
        provider:
          - "auto" / None → MultiProviderGateway (多供应商自动 fallback)
          - "zhipu"       → 智谱AI
          - "xunfei"      → 讯飞星火
          - "baidu"       → 百度千帆
          - "deepseek"     → DeepSeek (LLMGatewayV2)
          - "opencode_zen" → OpenCode Zen 免费模型
        models_config: 模型优先级列表（传给单个 provider 的 fallback）

    Returns:
        实现了 chat() / chat_stream() / chat_with_tools() / health_check() 的实例
    """
    resolved = _resolve_provider(provider)

    with _instance_lock:
        if resolved not in _instances:
            if resolved == "auto":
                _instances[resolved] = MultiProviderGateway()
                logger.info("Created MultiProviderGateway (auto-fallback chain)")
            elif resolved == "zhipu":
                from .openai_compatible_provider import OpenAICompatibleProvider
                _instances[resolved] = OpenAICompatibleProvider(
                    provider_name="zhipu",
                    api_key=os.environ.get("ZHIPU_API_KEY", ""),
                    api_base=os.environ.get("ZHIPU_API_BASE", "https://open.bigmodel.cn/api/paas/v4"),
                    model=os.environ.get("ZHIPU_MODEL", "glm-4-flash"),
                    auth_mode="bearer",
                    models_config=models_config,
                )
                logger.info("Created OpenAICompatibleProvider [zhipu]")
            elif resolved == "xunfei":
                from .openai_compatible_provider import OpenAICompatibleProvider
                _instances[resolved] = OpenAICompatibleProvider(
                    provider_name="xunfei",
                    api_key=os.environ.get("XUNFEI_API_KEY", ""),
                    api_base=os.environ.get("XUNFEI_API_BASE", "https://spark-api-open.xf-yun.com/v1"),
                    model=os.environ.get("XUNFEI_MODEL", "spark-lite"),
                    auth_mode="bearer",
                    models_config=models_config,
                )
                logger.info("Created OpenAICompatibleProvider [xunfei]")
            elif resolved == "baidu":
                from .openai_compatible_provider import OpenAICompatibleProvider
                _instances[resolved] = OpenAICompatibleProvider(
                    provider_name="baidu",
                    api_key=os.environ.get("BAIDU_API_KEY", ""),
                    api_base=os.environ.get("BAIDU_API_BASE", "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat"),
                    model=os.environ.get("BAIDU_MODEL", "ernie-speed-128k"),
                    auth_mode="oauth",
                    api_secret=os.environ.get("BAIDU_API_SECRET", ""),
                    models_config=models_config,
                )
                logger.info("Created OpenAICompatibleProvider [baidu]")
            elif resolved == "deepseek":
                from .llm_gateway import LLMGatewayV2
                _instances[resolved] = LLMGatewayV2(models_config=models_config)
                logger.info("Created LLMGatewayV2 [deepseek]")
            elif resolved == "opencode_zen":
                from .opencode_zen_provider import OpenCodeZenProvider
                _instances[resolved] = OpenCodeZenProvider(models_config=models_config)
                logger.info("Created OpenCodeZenProvider [opencode_zen]")
            else:
                logger.warning("Unknown provider '%s', falling back to auto gateway", resolved)
                _instances[resolved] = MultiProviderGateway()

        return _instances[resolved]


def get_llm_names(provider: str | None = None) -> list[str]:
    """获取可用的模型名称列表"""
    resolved = _resolve_provider(provider)
    names: list[str] = []

    if resolved == "zhipu":
        names = ["glm-4-flash"]
    elif resolved == "xunfei":
        names = ["spark-lite"]
    elif resolved == "baidu":
        names = ["ernie-speed-128k"]
    elif resolved == "opencode_zen":
        from .opencode_zen_provider import DEFAULT_MODELS_PRIORITY
        names = [m["name"] for m in DEFAULT_MODELS_PRIORITY]
    elif resolved == "deepseek":
        names = ["deepseek-chat", "deepseek-reasoner"]
    else:
        names = ["auto (多供应商网关)"]

    return names
