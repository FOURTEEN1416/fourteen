"""
OpenAI 兼容格式通用 Provider — 支持任意 OpenAI-compatible API

适用于:
  - DeepSeek    → https://api.deepseek.com/v1
  - 智谱AI       → https://open.bigmodel.cn/api/paas/v4
  - 讯飞星火 HTTP → https://spark-api-open.xf-yun.com/v1
  - 百度千帆      → https://aip.baidubce.com (需 OAuth 换 token)

所有端点的 chat/completions 都兼容 OpenAI 格式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .llm_gateway import ModelRegistry

try:
    from observability.metrics import record_chat_duration, record_error, record_token_usage
    HAS_METRICS = True
except ImportError:
    HAS_METRICS = False

logger = logging.getLogger("llm_provider.openai_compatible")


class OpenAICompatibleProvider:
    """
    通用 OpenAI 兼容格式 Provider

    支持任意使用 /v1/chat/completions 的 LLM API，
    通过 Bearer Token 或自定义 Header 鉴权。

    特殊处理:
      - 百度千帆: auth_mode="oauth", 自动刷新 access_token
      - 讯飞星火: auth_mode="bearer", 标准 Bearer Token
      - 智谱AI:   auth_mode="bearer", 标准 Bearer Token
    """

    def __init__(
        self,
        provider_name: str = "custom",
        api_key: str = "",
        api_base: str = "",
        model: str = "",
        auth_mode: str = "bearer",  # bearer | oauth
        models_config: list[dict] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.85,
        stream_enabled: bool = True,
        extra_payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        self.provider_name = provider_name
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.auth_mode = auth_mode
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.stream_enabled = stream_enabled
        self.extra_payload = extra_payload or {}

        # 模型降级注册表
        default_models = [{"name": model, "priority": 1}]
        self.registry = ModelRegistry(models_config or default_models)

        # 端点
        if self.api_base and not self.api_base.endswith("/chat/completions"):
            self._chat_url = f"{self.api_base}/chat/completions"
        else:
            self._chat_url = self.api_base

        # 请求头
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_mode == "bearer" and api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        elif auth_mode == "oauth":
            self._headers["Authorization"] = f"Bearer {api_key}"
            # 百度千帆特殊处理: 额外传 APPID 等
            self._app_id = kwargs.get("app_id", "")
            self._api_secret = kwargs.get("api_secret", "")

        # OAuth token 缓存（百度千帆用）
        self._oauth_token: str = ""
        self._oauth_expires_at: float = 0.0

        # 连接池
        self._pool_limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)
        self._client: httpx.AsyncClient | None = None
        self._client_loop_id: int | None = None
        self._sync_client: httpx.Client | None = None

        logger.info(
            "OpenAICompatibleProvider [%s]: api_base=%s, model=%s, auth=%s",
            provider_name, self.api_base, self.model, auth_mode,
        )

    @property
    def _async_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        if self._client is None or self._client_loop_id != loop_id:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0),
                limits=self._pool_limits,
                headers=self._headers,
            )
            self._client_loop_id = loop_id
        return self._client

    @property
    def _sync(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                timeout=httpx.Timeout(60.0),
                limits=self._pool_limits,
                headers=self._headers,
            )
        return self._sync_client

    # ─── 公共接口 ────────────────────────────────

    async def chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
        model: str | None = None,
    ) -> str:
        """异步聊天（同步包装版）"""
        return await self._chat(
            query=query, system_prompt=system_prompt, history=history,
            messages=messages, temperature=temperature, max_tokens=max_tokens,
            tools=tools, model=model, stream=False,
        )

    async def chat_stream(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
    ) -> AsyncIterator[str]:
        """流式聊天"""
        result = await self._chat(
            query=query, system_prompt=system_prompt, history=history,
            messages=messages, temperature=temperature, max_tokens=max_tokens,
            tools=tools, stream=True,
        )
        # _chat 对流式返回 AsyncIterator，同步返回 str
        if isinstance(result, str):
            yield result
        else:
            async for token in result:
                yield token

    async def chat_with_tools(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
    ) -> dict[str, Any]:
        """带工具的聊天"""
        temp = temperature if temperature is not None else self.temperature
        mt = max_tokens or self.max_tokens
        built = self._build_messages(query, system_prompt, history, messages)
        payload = {
            "model": self.model,
            "messages": built,
            "temperature": temp,
            "max_tokens": mt,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.extra_payload:
            payload.update(self.extra_payload)

        await self._refresh_oauth_if_needed()

        try:
            resp = await self._async_client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            message = data["choices"][0]["message"]
            return {
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls"),
            }
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm", type(e).__name__)
            return {"content": self._handle_error(e), "tool_calls": None}

    def switch_model(self, model_name: str) -> bool:
        """切换模型"""
        entry = self.registry.get_by_name(model_name)
        if entry:
            self.model = model_name
            logger.info("[%s] Switched to model: %s", self.provider_name, model_name)
            return True
        return False

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "configured": bool(self.api_key),
            "provider": self.provider_name,
            "model": self.model,
            "api_base": self.api_base,
            "available_models": [
                {"name": m.name, "priority": m.priority, "available": m.is_available()}
                for m in self.registry.all_models
            ],
        }

    # ─── 内部实现 ────────────────────────────────

    async def _chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
        model: str | None = None,
        stream: bool = False,
    ) -> str | AsyncIterator[str]:
        if not self.api_key:
            return self._mock_reply(query)
        if not self.api_base:
            return "（未配置 API 地址）"

        temp = temperature if temperature is not None else self.temperature
        mt = max_tokens or self.max_tokens
        model_name = model or self.model
        built = self._build_messages(query, system_prompt, history, messages)

        payload = {
            "model": model_name,
            "messages": built,
            "temperature": temp,
            "max_tokens": mt,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if self.extra_payload:
            payload.update(self.extra_payload)

        await self._refresh_oauth_if_needed()

        if stream:
            return self._stream_chat(payload, model_name)

        start = time.perf_counter()
        try:
            resp = await self._async_client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            if HAS_METRICS:
                record_chat_duration(f"{self.provider_name}/{model_name}", time.perf_counter() - start)
                if usage:
                    record_token_usage(f"{self.provider_name}/{model_name}", "prompt", usage.get("prompt_tokens", 0))
                    record_token_usage(f"{self.provider_name}/{model_name}", "completion", usage.get("completion_tokens", 0))

            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_success()
            return content.strip()
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm", type(e).__name__)
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_failed()
            fallback = await self._try_fallback(built, temp, mt, tools)
            if fallback:
                return fallback
            return self._handle_error(e)

    async def _stream_chat(
        self, payload: dict, model_name: str,
    ) -> AsyncIterator[str]:
        first_token_time = None
        start = time.perf_counter()
        try:
            async with self._async_client.stream("POST", self._chat_url, json=payload) as resp:
                resp.raise_for_status()
                async with asyncio.timeout(60):  # type: ignore[attr-defined]
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                if first_token_time is None:
                                    first_token_time = time.perf_counter()
                                    if first_token_time - start > 3.0:
                                        logger.warning("[%s] Stream first token timeout (>3s)", self.provider_name)
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except asyncio.TimeoutError:
            logger.warning("[%s] Stream timeout (60s)", self.provider_name)
            yield "（生成已超时，请重试）"
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm_stream", type(e).__name__)
            yield self._handle_error(e)

    async def _try_fallback(
        self, messages: list, temperature: float,
        max_tokens: int, tools: list | None,
    ) -> str | None:
        """同 provider 下不同模型 fallback"""
        for entry in self.registry.all_models:
            if entry.name == self.model or not entry.is_available():
                continue
            payload = {
                "model": entry.name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
            if self.extra_payload:
                payload.update(self.extra_payload)
            try:
                resp = await self._async_client.post(self._chat_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                entry.mark_success()
                logger.info("[%s] Fallback: %s → %s succeeded", self.provider_name, self.model, entry.name)
                return content.strip()
            except Exception:  # noqa: BLE001
                entry.mark_failed()
        return None

    async def _refresh_oauth_if_needed(self) -> None:
        """百度千帆 OAuth token 刷新"""
        if self.auth_mode != "oauth":
            return
        if time.time() < self._oauth_expires_at - 60:
            return  # token 还有效
        try:
            # 百度千帆 access_token 获取
            token_url = "https://aip.baidubce.com/oauth/2.0/token"
            params = {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self._api_secret,
            }
            resp = httpx.post(token_url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self._oauth_token = data.get("access_token", "")
            expires_in = data.get("expires_in", 2592000)  # 默认30天
            self._oauth_expires_at = time.time() + expires_in
            # 更新 Authorization header
            self._headers["Authorization"] = f"Bearer {self._oauth_token}"
            logger.info("[%s] OAuth token refreshed, expires in %ds", self.provider_name, expires_in)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] OAuth refresh failed: %s", self.provider_name, e)

    def _build_messages(
        self, query: str, system_prompt: str,
        history: list | None, messages: list | None,
    ) -> list:
        if messages:
            return messages
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        if history:
            result.extend(history)
        if query:
            result.append({"role": "user", "content": query})
        return result

    def _handle_error(self, e: Exception) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            logger.error("[%s] API HTTP %d: %s", self.provider_name, e.response.status_code, e.response.text[:200])
            return f"（{self.provider_name} API 请求失败，错误代码 {e.response.status_code}）"
        if isinstance(e, httpx.RequestError):
            logger.error("[%s] API 请求失败: %s", self.provider_name, e)
            return f"（{self.provider_name} 网络请求失败）"
        logger.error("[%s] API 异常: %s", self.provider_name, e)
        return "（当前服务暂时不可用）"

    def _mock_reply(self, query: str) -> str:
        return f"（{self.provider_name} 未配置 API Key，无法生成回复）"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
