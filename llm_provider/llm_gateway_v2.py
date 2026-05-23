from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from observability.logging_setup import get_logger
from observability.metrics import record_chat_duration, record_error, record_token_usage

logger = get_logger("llm_gateway_v2")

DEFAULT_API_BASE = "https://api.deepseek.com/v1"


@dataclass
class ModelEntry:
    name: str
    priority: int
    retry_count: int = 0
    cooldown_until: float = 0.0
    max_retries: int = 1
    cooldown_seconds: float = 10.0

    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_failed(self):
        self.retry_count += 1
        if self.retry_count >= self.max_retries:
            self.cooldown_until = time.time() + self.cooldown_seconds
            self.retry_count = 0
            logger.warning("Model %s entering cooldown for %.0fs", self.name, self.cooldown_seconds)

    def mark_success(self):
        self.retry_count = 0


class ModelRegistry:
    def __init__(self, models: Optional[List[Dict[str, Any]]] = None):
        default_models = [
            {"name": "deepseek-chat", "priority": 1},
            {"name": "deepseek-reasoner", "priority": 2},
        ]
        model_list = models or default_models
        self._models: List[ModelEntry] = [
            ModelEntry(name=m["name"], priority=m["priority"])
            for m in model_list
        ]
        self._models.sort(key=lambda m: m.priority)

    def get_available(self) -> Optional[ModelEntry]:
        for m in self._models:
            if m.is_available():
                return m
        return None

    def get_by_name(self, name: str) -> Optional[ModelEntry]:
        for m in self._models:
            if m.name == name:
                return m
        return None

    @property
    def all_models(self) -> List[ModelEntry]:
        return self._models


class LLMGatewayV2:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        models_config: Optional[List[Dict]] = None,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.api_base = (api_base or os.environ.get("DEEPSEEK_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
        self.registry = ModelRegistry(models_config)
        self._chat_url = f"{self.api_base}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self._pool_limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        self._client = None
        self._client_loop_id = None

        if self.api_key:
            logger.info("LLMGatewayV2 ready, primary model=%s", self.model)
        else:
            logger.warning("LLMGatewayV2: no API key, using mock replies")

    @property
    def _async_client(self):
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

    async def chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: Optional[list] = None,
        messages: Optional[list] = None,
        temperature: float = 0.85,
        max_tokens: int = 1024,
        tools: Optional[list] = None,
        model: Optional[str] = None,
    ) -> str:
        if not self.api_key:
            return self._mock_reply(query)

        built_messages = self._build_messages(query, system_prompt, history, messages)
        model_name = model or self.model
        payload = {
            "model": model_name,
            "messages": built_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        start = time.perf_counter()
        try:
            resp = await self._async_client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            record_chat_duration(model_name, time.perf_counter() - start)
            if usage:
                record_token_usage(model_name, "prompt", usage.get("prompt_tokens", 0))
                record_token_usage(model_name, "completion", usage.get("completion_tokens", 0))
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_success()
            return content.strip()
        except Exception as e:
            record_error("llm", type(e).__name__)
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_failed()
            fallback_content = await self._try_fallback_async(built_messages, temperature, max_tokens, tools)
            if fallback_content:
                return fallback_content
            return self._handle_error(e)

    async def chat_with_tools(
        self,
        query: str = "",
        system_prompt: str = "",
        history: Optional[list] = None,
        messages: Optional[list] = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: Optional[list] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            return {"content": self._mock_reply(query), "tool_calls": None}

        built_messages = self._build_messages(query, system_prompt, history, messages)
        payload = {
            "model": self.model,
            "messages": built_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            resp = await self._async_client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            message = data["choices"][0]["message"]
            return {
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls"),
            }
        except Exception as e:
            record_error("llm", type(e).__name__)
            return {"content": self._handle_error(e), "tool_calls": None}

    def chat_sync(
        self,
        query: str = "",
        system_prompt: str = "",
        history: Optional[list] = None,
        messages: Optional[list] = None,
        temperature: float = 0.85,
        max_tokens: int = 1024,
        tools: Optional[list] = None,
        model: Optional[str] = None,
    ) -> str:
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.chat(
                    query=query, system_prompt=system_prompt, history=history,
                    messages=messages, temperature=temperature, max_tokens=max_tokens,
                    tools=tools, model=model,
                ))
                return future.result()
        except RuntimeError:
            return asyncio.run(self.chat(
                query=query, system_prompt=system_prompt, history=history,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, model=model,
            ))

    async def chat_stream(
        self,
        query: str = "",
        system_prompt: str = "",
        history: Optional[list] = None,
        messages: Optional[list] = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: Optional[list] = None,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            yield self._mock_reply(query)
            return

        built_messages = self._build_messages(query, system_prompt, history, messages)
        payload = {
            "model": self.model,
            "messages": built_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        first_token_time = None
        start = time.perf_counter()
        try:
            async with self._async_client.stream("POST", self._chat_url, json=payload) as resp:
                resp.raise_for_status()
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
                                    logger.warning("First token timeout (>3s)")
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except Exception as e:
            record_error("llm_stream", type(e).__name__)
            yield self._handle_error(e)

    def switch_model(self, model_name: str) -> bool:
        entry = self.registry.get_by_name(model_name)
        if entry:
            self.model = model_name
            logger.info("Switched to model: %s", model_name)
            return True
        return False

    def _build_messages(self, query: str, system_prompt: str,
                        history: Optional[list], messages: Optional[list]) -> list:
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

    async def _try_fallback_async(self, messages: list, temperature: float,
                      max_tokens: int, tools: Optional[list]) -> Optional[str]:
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
            try:
                resp = await self._async_client.post(self._chat_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                entry.mark_success()
                logger.info("Fallback to %s succeeded", entry.name)
                return content.strip()
            except Exception:
                entry.mark_failed()
        return None

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._client_loop_id = None
            logger.info("LLMGatewayV2 connection pool closed")

    def _handle_error(self, e: Exception) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            sanitized = self._sanitize_log(e.response.text[:200])
            logger.error("LLM API HTTP %d: %s", e.response.status_code, sanitized)
            return f"（API 请求失败，错误代码 {e.response.status_code}）"
        if isinstance(e, httpx.RequestError):
            logger.error("LLM API 请求失败: %s", e)
            return "（网络请求失败，请检查网络连接）"
        logger.error("LLM API 异常: %s", e)
        return "（生成回复时出现异常）"

    @staticmethod
    def _sanitize_log(text: str) -> str:
        import re
        text = re.sub(r'(Bearer\s+)sk-\S+', r'\1sk-****', text)
        text = re.sub(r'(?i)(Authorization["\s:]+)\S+', r'\1****', text)
        return text

    def _mock_reply(self, query: str) -> str:
        q = query.lower()
        if "你好" in query or "hi" in q or "hello" in q:
            return "笨蛋，你终于来啦～我等你很久了知道吗"
        if "天气" in query:
            return "今天天气怎么样？我猜你肯定又没看天气预报就出门了吧"
        if "喜欢" in query:
            return "哼，这种问题也要问…当然喜欢啦"
        if "睡" in query:
            return "这么晚还不睡？要我陪你聊会儿？"
        if "吃" in query:
            return "又吃？你上辈子是猪吧…不过我也有点饿了"
        if "工作" in query or "忙" in query:
            return "在忙什么呀？再忙也要记得回我消息"
        return f"{query}？嗯…我在听呢，继续说呀"

    def health_check(self) -> dict:
        return {
            "configured": bool(self.api_key),
            "model": self.model,
            "available_models": [
                {"name": m.name, "priority": m.priority, "available": m.is_available()}
                for m in self.registry.all_models
            ],
        }
