"""
OpenCode/Zen 免费大模型提供者

API 端点: https://opencode.ai/zen/v1
认证方式: 无需 API Key
协议: 兼容 OpenAI Chat Completions API
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .llm_gateway_v2 import ModelRegistry

try:
    from observability.metrics import (
        record_chat_duration,
        record_error,
        record_token_usage,
    )
    HAS_METRICS = True
except ImportError:
    HAS_METRICS = False

try:
    from observability.logging_setup import get_logger
    logger = get_logger("opencode_zen")
except ImportError:
    logger = logging.getLogger("opencode_zen")

DEFAULT_API_BASE = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "big-pickle"

DEFAULT_MODELS_PRIORITY = [
    # ── OpenCode Zen 免费模型（无需 API Key） ──
    # 只有这 5 个是确定免费的，其余都要钱
    {"name": "big-pickle",             "priority": 1},   # 综合最强，推荐首选
    {"name": "nemotron-3-super-free",  "priority": 2},   # 稳定性最好（实测立刻响应）
    {"name": "qwen3.6-plus-free",      "priority": 3},   # 通义千问，中文优秀
    {"name": "deepseek-v4-flash-free", "priority": 4},   # DeepSeek 快速版
    {"name": "minimax-m2.5-free",      "priority": 5},   # MiniMax 备用
]


class OpenCodeZenProvider:
    """OpenCode/Zen 免费大模型提供者 — 无需 API Key"""

    def __init__(
        self,
        api_base: str | None = None,
        model: str | None = None,
        models_config: list[dict] | None = None,
    ):
        self.api_base = (api_base or os.environ.get("OPENCODE_ZEN_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("OPENCODE_ZEN_DEFAULT_MODEL") or DEFAULT_MODEL
        self.registry = ModelRegistry(models_config or DEFAULT_MODELS_PRIORITY)
        self._chat_url = f"{self.api_base}/chat/completions"
        self._models_url = f"{self.api_base}/models"
        self._headers = {"Content-Type": "application/json"}
        self.available_models: list[str] = []

        logger.info("OpenCodeZenProvider ready: api_base=%s, default_model=%s", self.api_base, self.model)

        try:
            self.fetch_available_models()
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch models on init (will use config): %s", e)

    def fetch_available_models(self) -> list[str]:
        try:
            resp = httpx.get(self._models_url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            remote_ids = set()
            if isinstance(data, dict) and "data" in data:
                for m in data["data"]:
                    if isinstance(m, dict) and "id" in m:
                        remote_ids.add(m["id"])
            elif isinstance(data, list):
                for m in data:
                    if isinstance(m, dict) and "id" in m:
                        remote_ids.add(m["id"])

            configured_ids = {m.name for m in self.registry.all_models}
            self.available_models = sorted(remote_ids & configured_ids)

            filtered_out = configured_ids - remote_ids
            if filtered_out:
                logger.info("Models filtered (not available on server): %s", filtered_out)

            if not self.available_models:
                self.available_models = sorted(configured_ids)
                logger.warning("No overlap with remote models, using all configured models")

            return self.available_models
        except Exception as e:  # noqa: BLE001
            logger.warning("fetch_available_models failed: %s", e)
            self.available_models = [m.name for m in self.registry.all_models]
            return self.available_models

    def chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 1024,
        tools: list | None = None,
        model: str | None = None,
    ) -> str:
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
            resp = httpx.post(self._chat_url, headers=self._headers, json=payload, timeout=60)
            if resp.status_code == 429:
                return self._handle_429_and_fallback(built_messages, temperature, max_tokens, tools, model_name)

            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            if HAS_METRICS:
                record_chat_duration(model_name, time.perf_counter() - start)
                if usage:
                    record_token_usage(model_name, "prompt", usage.get("prompt_tokens", 0))
                    record_token_usage(model_name, "completion", usage.get("completion_tokens", 0))
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_success()
            return content.strip()  # type: ignore[no-any-return]

        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm", type(e).__name__)
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_failed()
            fallback = self._try_fallback(built_messages, temperature, max_tokens, tools)
            if fallback:
                return fallback
            return self._handle_error(e)

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
            async with httpx.AsyncClient(timeout=60) as client:  # noqa: SIM117
                async with client.stream("POST", self._chat_url, headers=self._headers, json=payload) as resp:
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
                                            logger.warning("Stream first token timeout (>3s)")
                                    yield delta["content"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
        except asyncio.TimeoutError:
            logger.warning("OpenCodeZen 流式生成超时 (60s)")
            yield "（生成已超时，请重试）"
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm_stream", type(e).__name__)
            yield self._handle_error(e)

    def chat_with_tools(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
    ) -> dict[str, Any]:
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
            resp = httpx.post(self._chat_url, headers=self._headers, json=payload, timeout=60)
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
            if tools:
                logger.warning("Tool call failed, retrying without tools")
                try:
                    payload.pop("tools", None)
                    payload.pop("tool_choice", None)
                    resp = httpx.post(self._chat_url, headers=self._headers, json=payload, timeout=60)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return {"content": content, "tool_calls": None}
                except Exception as e:  # noqa: BLE001
                    pass
            return {"content": self._handle_error(e), "tool_calls": None}  # type: ignore[misc]

    def switch_model(self, model_name: str) -> bool:
        entry = self.registry.get_by_name(model_name)
        if entry:
            old_model = self.model
            self.model = model_name
            logger.info("Switched model: %s → %s", old_model, model_name)
            return True
        logger.warning("Model not found in registry: %s", model_name)
        return False

    def health_check(self) -> dict:
        reachable = False
        remote_models: list[str] = []
        try:
            resp = httpx.get(self._models_url, headers=self._headers, timeout=5)
            resp.raise_for_status()
            reachable = True
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                remote_models = [m.get("id", "") for m in data["data"] if isinstance(m, dict)]
            elif isinstance(data, list):
                remote_models = [m.get("id", "") for m in data if isinstance(m, dict)]
        except Exception as e:  # noqa: BLE001
            logger.warning("Health check: opencode/zen not reachable: %s", e)

        return {
            "configured": True,
            "provider": "opencode_zen",
            "reachable": reachable,
            "default_model": self.model,
            "available_models": self.available_models or remote_models,
        }

    def _build_messages(self, query: str, system_prompt: str,
                        history: list | None, messages: list | None) -> list:
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

    def _try_fallback(self, messages: list, temperature: float,
                      max_tokens: int, tools: list | None) -> str | None:
        fallback_count = 0
        for entry in self.registry.all_models:
            if entry.name == self.model or not entry.is_available():
                continue
            fallback_count += 1
            if fallback_count > len(self.registry.all_models):
                break
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
                resp = httpx.post(self._chat_url, headers=self._headers, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                entry.mark_success()
                logger.info("Fallback: %s → %s succeeded", self.model, entry.name)
                return content.strip()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                entry.mark_failed()
        return None

    def _handle_429_and_fallback(self, messages: list, temperature: float,
                                  max_tokens: int, tools: list | None,
                                  model_name: str) -> str:
        entry = self.registry.get_by_name(model_name)
        if entry:
            retry_after = 30.0
            entry.cooldown_until = time.time() + retry_after
            entry.retry_count = 0
            logger.warning("Model %s rate-limited (429), cooling for %.0fs", model_name, retry_after)

        fallback = self._try_fallback(messages, temperature, max_tokens, tools)
        if fallback:
            return fallback
        return "（当前服务暂时不可用，请稍后重试）"

    def _handle_error(self, e: Exception) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            logger.error("OpenCode/zen API HTTP %d: %s", e.response.status_code, e.response.text[:200])
            return f"（API 请求失败，错误代码 {e.response.status_code}）"
        if isinstance(e, httpx.RequestError):
            logger.error("OpenCode/zen API 请求失败: %s", e)
            return "（网络请求失败，请检查网络连接）"
        logger.error("OpenCode/zen API 异常: %s", e)
        return "（当前服务暂时不可用，请稍后重试）"
