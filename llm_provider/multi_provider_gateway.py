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

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from .llm_gateway import LLMGatewayV2
from .openai_compatible_provider import OpenAICompatibleProvider

logger = logging.getLogger("llm_provider.multi_gateway")

# ── fallback 统计（可选依赖，缺失时静默降级） ──
try:
    from observability.metrics import record_provider_fallback as _record_fallback
except ImportError:  # pragma: no cover
    def _record_fallback(provider: str, status: str) -> Any:
        return None

def _merge_attachments(
    query: str,
    system_prompt: str,
    history: list | None,
    messages: list | None,
    attachments: list | None,
) -> tuple[list | None, str]:
    """把多模态附件并入末条 user message，返回 (messages, query)。

    与 ``llm_gateway.LLMGateway._build_messages`` 保持同一规则：**不替换整条 messages**，
    否则 system_prompt（角色人设）与 history（对话历史）会被整个丢弃。合并后 query
    已进入 messages，故返回空串避免重复拼接。

    为何必须在网关层处理（2026-09-15 线上故障）：``provider.chat()`` 没有 attachments
    形参，而 ``provider._build_messages`` 只在 messages 为空时才自建 —— 图片只能经
    messages 传递。此前 orchestrator 传 attachments、本网关却无此形参，导致
    ``TypeError: MultiProviderGateway.chat() got an unexpected keyword argument
    'attachments'``，微信每条消息（含纯文本）都返回"处理消息时出现异常"。
    """
    if not attachments or messages:
        return messages, query
    built: list = []
    if system_prompt:
        built.append({"role": "system", "content": system_prompt})
    if history:
        built.extend(history)
    parts: list = [{"type": "text", "text": query}] if query else []
    parts.extend(attachments)
    built.append({"role": "user", "content": parts})
    return built, ""


# ── 默认 fallback 链 ──
DEFAULT_FALLBACK_CHAIN = ["sensenova", "zhipu", "xunfei", "baidu"]

# ── Provider 失败哨兵 ──
# 各 provider 的 _handle_error()/_mock_reply() 统一返回**全角括号包裹**的固定文案。
# 2026-09-17 修复：旧实现用 `result.startswith("（")` 判定失败，会误伤正常回复——
# 本项目人设（傲娇）大量使用括号内心独白（默认配置里就有"（其实在等你哄）"），
# 这类正常回复会被当成失败：① 被丢弃；② 触发对下一个 provider 的**重复请求**
# （浪费 token 与延迟）；③ 全链失败时最终返回错误文案。
# 改为精确匹配错误文案特征词：仍以全角括号包裹，且包含下列任一错误标记。
_ERROR_SENTINELS: tuple[str, ...] = (
    "API 请求失败",
    "网络请求失败",
    "服务暂时不可用",
    "生成回复时出现异常",
    "生成已超时",
    "未配置 API",
    "所有 LLM 提供商均不可用",
)


def _is_error_reply(text: str) -> bool:
    """判断 provider 返回值是否为错误哨兵文案（而非正常回复）。"""
    if not text:
        return True
    stripped = text.strip()
    if not (stripped.startswith("（") and stripped.endswith("）")):
        return False
    return any(sentinel in stripped for sentinel in _ERROR_SENTINELS)

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
    """从 config/llm_providers.json 加载配置

    路径锚定项目根（2026-09-17 修复）：旧实现 ``Path("config/llm_providers.json")``
    按进程 CWD 解析，从非仓库根启动时静默返回 ``{}`` —— provider 配置全部丢失，
    fallback 链退化为默认值，且**无任何报错**（只打一条 warning）。
    """
    from utils.project_paths import project_path

    config_path = project_path("config", "llm_providers.json")
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
        attachments: list | None = None,
    ) -> str:
        """按 fallback 链尝试各个 provider"""
        last_error = ""

        # 多模态附件先并入 messages 再进入 fallback 链（见 _merge_attachments 注释）
        messages, query = _merge_attachments(
            query, system_prompt, history, messages, attachments
        )

        # 并发修复（2026-09-17）：旧实现在循环里写 self._current_index，
        # 而 chat_stream/chat_with_tools 通过 current_provider 读它。
        # 多请求并发时 A 请求探测 provider 失败会把索引推到末尾，
        # B 请求的流式/工具调用就会打到**错误的 provider**（甚至空 provider）。
        # 现在只记录本次调用"实际成功"的 provider，且仅在成功后才发布，
        # 使失败探测不再污染全局当前指针。
        for key in self._providers:
            provider = self._providers[key]
            try:
                result = await provider.chat(
                    query=query, system_prompt=system_prompt,
                    history=history, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, model=model,
                )
                if result and not _is_error_reply(result):
                    _record_fallback(key, "success")
                    self._publish_current(key)
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

    def _publish_current(self, key: str) -> None:
        """把"当前活跃 provider"指针发布到 key（线程安全）。

        仅在调用成功后调用；失败探测不再改动全局指针（见 chat() 注释）。
        """
        keys = list(self._providers.keys())
        if key in keys:
            self._current_index = keys.index(key)

    def chat_sync(
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
        """同步入口 — 供 APScheduler 线程/脚本侧调用方使用。

        2026-09-17 补齐：ASE 主动消息生成（MessageGenerator）等重要日期祝福等
        消费方以 hasattr(llm, "chat_sync") 探测同步能力，本类此前缺失该方法，
        导致 LLM 生成静默失败、全部回落模板（生产日志实证全为模板消息）。
        """
        try:
            asyncio.get_running_loop()
            # 已在事件循环内（不应发生于此方法的设计调用场景）：丢线程池执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.chat(
                        query=query, system_prompt=system_prompt, history=history,
                        messages=messages, temperature=temperature,
                        max_tokens=max_tokens, tools=tools, model=model,
                    ),
                )
                return future.result()
        except RuntimeError:
            return asyncio.run(
                self.chat(
                    query=query, system_prompt=system_prompt, history=history,
                    messages=messages, temperature=temperature,
                    max_tokens=max_tokens, tools=tools, model=model,
                )
            )

    async def chat_stream(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
        attachments: list | None = None,
    ) -> AsyncIterator[str]:
        """流式聊天 — 只在第一个可用 provider 上执行"""
        provider = self.current_provider
        if not provider:
            yield "（没有可用的 LLM 提供商）"
            return

        messages, query = _merge_attachments(
            query, system_prompt, history, messages, attachments
        )

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
        attachments: list | None = None,
    ) -> dict[str, Any]:
        provider = self.current_provider
        if not provider:
            return {"content": "（没有可用的 LLM 提供商）", "tool_calls": None}

        messages, query = _merge_attachments(
            query, system_prompt, history, messages, attachments
        )

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
