"""
多供应商网关 — 在多个 LLM 提供商之间按优先级链式 Fallback

工作方式:
  1. 按 fallback_chain 顺序依次尝试 provider
  2. 当前 provider 所有模型失败 → 切换到下一个 provider
  3. 所有 provider 都失败 → 返回错误信息

支持的 provider:
  - sensenova: 商汤日日新（glm-5.2）
  - zhipu:     智谱AI
  - xunfei:    讯飞星火
  - baidu:     百度千帆
  - deepseek:  DeepSeek API
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from .llm_gateway import LLMGatewayV2
from .openai_compatible_provider import OpenAICompatibleProvider

logger = logging.getLogger("llm_provider.multi_gateway")

# ── fallback 统计（可选依赖，缺失时静默降级） ──
try:
    from observability.metrics import record_provider_fallback as _record_fallback
except ImportError:  # pragma: no cover
    def _record_fallback(provider: str, status: str) -> None:
        return None

# ── 默认 fallback 链 ──
DEFAULT_FALLBACK_CHAIN = ["sensenova", "zhipu", "xunfei", "baidu"]

# ── 默认提供商配置 ──
DEFAULT_PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "zhipu": {
        "name": "智谱AI",
        "model": "glm-4-flash",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "auth_mode": "bearer",
        "max_tokens": 2048,
        "temperature": 0.85,
        "description": "GLM-4.7-Flash 永久免费，无限 Token，200K 上下文",
    },
    "xunfei": {
        "name": "讯飞星火",
        "model": "spark-lite",
        "api_base": "https://spark-api-open.xf-yun.com/v1",
        "auth_mode": "bearer",
        "max_tokens": 2048,
        "temperature": 0.85,
        "description": "Spark Lite 永久免费，无限 Token，并发 5",
    },
    "baidu": {
        "name": "百度千帆",
        "model": "ernie-speed-128k",
        "api_base": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat",
        "auth_mode": "oauth",
        "max_tokens": 2048,
        "temperature": 0.85,
        "description": "ERNIE-Speed 每月 50 万 Token 免费，128K 上下文",
    },
    "deepseek": {
        "name": "DeepSeek",
        "model": "deepseek-chat",
        "api_base": "https://api.deepseek.com/v1",
        "auth_mode": "bearer",
        "max_tokens": 4096,
        "temperature": 0.85,
        "description": "DeepSeek-V2 高质量模型，需自行申请 API Key",
    },
    "sensenova": {
        "name": "商汤日日新 SenseNova",
        "model": "glm-5.2",
        "api_base": "https://token.sensenova.cn/v1",
        "auth_mode": "bearer",
        "max_tokens": 8192,
        "temperature": 0.85,
        "description": "商汤 SenseNova 平台，glm-5.2 1M上下文/13万输出，支持工具调用",
        "extra_payload": {"reasoning_effort": "none"},
    },
}


def _load_providers_config() -> dict[str, Any]:
    """从 config/llm_providers.json 加载配置"""
    config_path = Path("config/llm_providers.json")
    if not config_path.exists():
        return {}

    try:
        # 用 utf-8-sig 兼容 BOM 头（utf-8 会在首个 BOM 字节处抛 UnicodeDecodeError）
        with open(config_path, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load llm_providers.json: %s", e)
        return {}


def _resolve_env_override(provider_key: str, config: dict[str, Any]) -> dict[str, Any]:
    """用环境变量覆盖配置"""
    env_map = {
        "zhipu": {"key": "ZHIPU_API_KEY", "base": "ZHIPU_API_BASE", "model": "ZHIPU_MODEL"},
        "xunfei": {"key": "XUNFEI_API_KEY", "base": "XUNFEI_API_BASE", "model": "XUNFEI_MODEL"},
        "baidu": {"key": "BAIDU_API_KEY", "base": "BAIDU_API_BASE", "model": "BAIDU_MODEL"},
        "deepseek": {"key": "DEEPSEEK_API_KEY", "base": "DEEPSEEK_API_BASE", "model": "DEEPSEEK_MODEL"},
        "sensenova": {"key": "SENSENOVA_API_KEY", "base": "SENSENOVA_API_BASE", "model": "SENSENOVA_MODEL"},
    }

    mapping = env_map.get(provider_key)
    if not mapping:
        return config

    cfg = config.copy()

    # API Key
    env_key = os.environ.get(mapping["key"], "")
    if env_key:
        cfg["api_key"] = env_key

    # API Base
    env_base = os.environ.get(mapping["base"], "")
    if env_base:
        cfg["api_base"] = env_base

    # Model
    env_model = os.environ.get(mapping["model"], "")
    if env_model:
        cfg["model"] = env_model

    return cfg


class MultiProviderGateway:
    """
    多供应商网关 — 自动在多个 LLM 提供商之间 fallback

    按 fallback_chain 顺序尝试:
      商汤日日新 → 智谱AI → 讯飞星火 → 百度千帆
    如果用户配置了 DeepSeek，自动插入到最前面。
    """

    def __init__(
        self,
        fallback_chain: list[str] | None = None,
        providers_config: dict[str, dict[str, Any]] | None = None,
    ):
        # 加载配置文件
        file_config = _load_providers_config()
        self._chain = fallback_chain or file_config.get("fallback_chain", DEFAULT_FALLBACK_CHAIN)
        provider_configs = providers_config or file_config.get("providers", {})

        # 构建 provider 实例
        self._providers: dict[str, Any] = {}
        self._current_index: int = 0  # 当前活跃的 provider 索引

        for key in self._chain:
            if key == "deepseek":
                self._providers[key] = LLMGatewayV2()
                continue

            # 从文件配置或默认配置创建
            cfg = provider_configs.get(key, {})
            if not cfg:
                cfg = DEFAULT_PROVIDER_CONFIG.get(key, {})

            # 环境变量覆盖
            cfg = _resolve_env_override(key, cfg)

            # 跳过没有 API Key 的 provider
            if not cfg.get("api_key"):
                logger.info("[MultiGateway] %s 未配置 API Key，跳过", key)
                continue

            self._providers[key] = OpenAICompatibleProvider(
                provider_name=key,
                api_key=cfg.get("api_key", ""),
                api_base=cfg.get("api_base", ""),
                model=cfg.get("model", ""),
                auth_mode=cfg.get("auth_mode", "bearer"),
                max_tokens=cfg.get("max_tokens", 2048),
                temperature=cfg.get("temperature", 0.85),
                extra_payload=cfg.get("extra_payload"),
            )

        logger.info(
            "MultiProviderGateway ready: chain=%s, active_providers=%s",
            list(self._providers.keys()),
            len(self._providers),
        )

    @property
    def current_provider_key(self) -> str:
        """当前活跃的 provider key"""
        keys = list(self._providers.keys())
        if not keys:
            return ""
        idx = min(self._current_index, len(keys) - 1)
        return keys[idx]

    @property
    def current_provider(self) -> Any:
        """当前活跃的 provider 实例"""
        key = self.current_provider_key
        return self._providers.get(key)

    @property
    def provider_keys(self) -> list[str]:
        return list(self._providers.keys())

    @property
    def all_providers(self) -> dict[str, Any]:
        return self._providers

    # ─── 公共接口 ────────────────────────────────

    async def chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
        model: str | None = None,
    ) -> str:
        """按 fallback 链尝试各个 provider"""
        last_error = ""

        for idx, key in enumerate(self._providers):
            provider = self._providers[key]
            self._current_index = idx
            try:
                result = await provider.chat(
                    query=query, system_prompt=system_prompt,
                    history=history, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, model=model,
                )
                if result and not result.startswith("（"):
                    _record_fallback(key, "success")
                    return result
                last_error = result
                _record_fallback(key, "fallback")
                logger.warning("[MultiGateway] %s returned: %s", key, result)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                _record_fallback(key, "error")
                logger.warning("[MultiGateway] %s failed: %s", key, e)

        logger.error("[MultiGateway] All providers failed, last_error=%s", last_error)
        return f"（所有 LLM 提供商均不可用，请检查配置。最后错误: {last_error}）"

    async def chat_stream(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
    ) -> AsyncIterator[str]:
        """流式聊天 — 只在第一个可用 provider 上执行"""
        provider = self.current_provider
        if not provider:
            yield "（没有可用的 LLM 提供商）"
            return

        async for token in provider.chat_stream(
            query=query, system_prompt=system_prompt,
            history=history, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools,
        ):
            yield token

    async def chat_with_tools(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
    ) -> dict[str, Any]:
        provider = self.current_provider
        if not provider:
            return {"content": "（没有可用的 LLM 提供商）", "tool_calls": None}

        return await provider.chat_with_tools(
            query=query, system_prompt=system_prompt,
            history=history, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools,
        )

    def switch_provider(self, provider_key: str) -> bool:
        """手动切换到指定提供商"""
        if provider_key in self._providers:
            keys = list(self._providers.keys())
            self._current_index = keys.index(provider_key)
            logger.info("[MultiGateway] Switched to provider: %s", provider_key)
            return True
        logger.warning("[MultiGateway] Provider not found: %s", provider_key)
        return False

    def switch_model(self, model_name: str) -> bool:
        """切换当前 provider 的模型"""
        provider = self.current_provider
        if provider and hasattr(provider, "switch_model"):
            return provider.switch_model(model_name)
        return False

    def health_check(self) -> dict[str, Any]:
        """所有 provider 的健康状态"""
        providers_status = {}
        for key, provider in self._providers.items():
            try:
                if hasattr(provider, "health_check"):
                    providers_status[key] = provider.health_check()
                else:
                    providers_status[key] = {"configured": True}
            except Exception as e:  # noqa: BLE001
                providers_status[key] = {"configured": True, "error": str(e)}

        return {
            "current_provider": self.current_provider_key,
            "fallback_chain": list(self._providers.keys()),
            "providers": providers_status,
        }

    async def close(self) -> None:
        for provider in self._providers.values():
            if hasattr(provider, "close"):
                with suppress(Exception):
                    await provider.close()
