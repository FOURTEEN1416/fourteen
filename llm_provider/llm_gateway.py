from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from observability.logging_setup import get_logger
from observability.metrics import record_chat_duration, record_error, record_token_usage

logger = get_logger("llm_gateway")

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
    def __init__(self, models: list[dict[str, Any]] | None = None):
        default_models = [
            {"name": "deepseek-chat", "priority": 1},
            {"name": "deepseek-reasoner", "priority": 2},
        ]
        model_list = models or default_models
        self._models: list[ModelEntry] = [
            ModelEntry(name=m["name"], priority=m["priority"])  # type: ignore[arg-type]
            for m in model_list
        ]
        self._models.sort(key=lambda m: m.priority)

    def get_available(self) -> ModelEntry | None:
        for m in self._models:
            if m.is_available():
                return m
        return None

    def get_by_name(self, name: str) -> ModelEntry | None:
        for m in self._models:
            if m.name == name:
                return m
        return None

    @property
    def all_models(self) -> list[ModelEntry]:
        return self._models


class LLMGatewayV2:
    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        models_config: list[dict] | None = None,
        request_timeout: float | None = None,
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
        self.request_timeout = float(request_timeout or 60.0)
        # 每事件循环一个 client：httpx.AsyncClient **不可跨循环复用**（其传输层
        # 持有循环绑定的 socket/锁）。旧实现只留一个 client 并在切换循环时把
        # `aclose()` 投递到**新**循环上关闭**旧**循环的 client —— 注释与实现相反，
        # 且旧 client 的资源实际未被释放。现按 loop 记账，在**所属循环**上关闭。
        self._clients: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}

        if self.api_key:
            logger.info("LLMGatewayV2 ready, primary model=%s", self.model)
        else:
            logger.warning("LLMGatewayV2: no API key, using mock replies")

    @property
    def _async_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        entry = self._clients.get(id(loop))
        if entry is not None and entry[0] is loop:
            return entry[1]
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.request_timeout),
            limits=self._pool_limits,
            headers=self._headers,
        )
        self._clients[id(loop)] = (loop, client)
        self._reap_stale_clients(loop)
        return client

    def _reap_stale_clients(self, current: asyncio.AbstractEventLoop) -> None:
        """回收其它事件循环上的 client。

        - 对方循环仍在跑 → 用 ``run_coroutine_threadsafe`` 投递到**它自己**的循环关闭；
        - 对方循环已停但未关闭 → 就地 ``run_until_complete`` 收尾；
        - 对方循环已关闭 → 直接丢弃引用（socket 随循环销毁，等待 GC 回收）。
        """
        for key, (loop, client) in list(self._clients.items()):
            if loop is current:
                continue
            try:
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(client.aclose(), loop)
                elif not loop.is_closed():
                    loop.run_until_complete(client.aclose())
            except Exception as e:  # noqa: BLE001
                logger.debug("回收旧事件循环上的 LLM client 失败: %s", e)
            finally:
                self._clients.pop(key, None)

    async def chat(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 1024,
        tools: list | None = None,
        model: str | None = None,
        attachments: list | None = None,
    ) -> str:
        if not self.api_key:
            return self._mock_reply(query)

        built_messages = self._build_messages(
            query, system_prompt, history, messages, attachments
        )
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
            return content.strip()  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001
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
        history: list | None = None,
        messages: list | None = None,
        temperature: float = 0.85,
        max_tokens: int = 2048,
        tools: list | None = None,
    ) -> dict[str, Any]:
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
        except Exception as e:  # noqa: BLE001
            record_error("llm", type(e).__name__)
            return {"content": self._handle_error(e), "tool_calls": None}

    def chat_sync(
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
        """同步入口 —— 供 APScheduler 线程 / 微信消息线程 / 脚本调用。

        2026-09-21 审查修复：旧实现是「一次性 ``ThreadPoolExecutor`` +
        ``asyncio.run``」，与 ``MultiProviderGateway.chat_sync`` 在 09-19 已修掉的
        是**同一个根因**（每次调用新建并销毁事件循环 → httpx 连接池随循环失效、
        每轮请求重做 TLS 握手、``_async_client`` 的跨循环回收被反复触发）。
        该修复只落在网关一处，本类的同型实现留了尾巴。现统一委托
        ``utils.async_utils``（全项目唯一的同步→异步桥）：协程投递到**常驻
        共享循环**，连接池与事件循环绑定原语跨调用保持有效。
        """
        from utils.async_utils import get_shared_loop, run_on_shared_loop

        def _call() -> str:
            return self.chat(
                query=query, system_prompt=system_prompt, history=history,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, model=model,
            )

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            # 常规路径：调用线程无运行中的循环（微信消息线程 / APScheduler / 脚本）
            return run_on_shared_loop(_call())

        if running is get_shared_loop():
            # 已在共享循环内部：不能再向它投递（必死锁），退化为临时线程执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _call()).result()

        return run_on_shared_loop(_call())

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
        """流式聊天。

        2026-09-21 审查修复（与 `MultiProviderGateway.chat_stream` 的 P1-1 同规）：
        `MultiProviderGateway` 的降级链依赖「**首 token 前失败必须上抛**」这一契约
        （见 P1-1 注释：provider 把错误文案 yield 成"内容"会让链整体失效）。
        `OpenAICompatibleProvider` 遵守该契约，本类此前**不遵守** —— 失败时
        `yield self._handle_error(e)`，网关把它当成首个 token（`emitted=True`），
        于是：① 不再降级到其它 provider；② 「（API 请求失败，错误代码 500）」
        被当作回复写给用户，并被计入 chat_history 污染下一轮。
        现改为：首 token 前失败上抛（网关据此切换 provider），已下发内容后中断
        仍上抛（重放会造成半句+整句重复），由调用方兜底。
        """
        if not self.api_key:
            yield self._mock_reply(query)
            return

        built_messages = self._build_messages(query, system_prompt, history, messages)
        model_name = self.model
        payload = {
            "model": model_name,
            "messages": built_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        start = time.perf_counter()
        emitted = False
        try:
            # 空闲超时由 httpx 的 read timeout 承担（逐次读取计时）。
            # 旧实现额外套了一层 `asyncio.timeout(60)` 的**整段总时限**：
            # 慢供应商下 2048 token 的长回复会被硬切，且与链首 20s 预算口径不一。
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
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta.get("content"):
                        if not emitted:
                            emitted = True
                            first = time.perf_counter() - start
                            if first > 3.0:
                                logger.warning("首 token 延迟 %.1fs（模型 %s）", first, model_name)
                        yield delta["content"]
        except Exception as e:  # noqa: BLE001
            record_error("llm_stream", type(e).__name__)
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_failed()
            logger.warning(
                "LLM 流式失败（已下发 %s）: %s", "部分内容" if emitted else "零内容", e
            )
            # 零内容 → 上抛交给降级链；已有内容 → 上抛避免重放重复
            raise
        else:
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_success()

    def switch_model(self, model_name: str) -> bool:
        entry = self.registry.get_by_name(model_name)
        if entry:
            self.model = model_name
            logger.info("Switched to model: %s", model_name)
            return True
        return False

    def _build_messages(self, query: str, system_prompt: str,
                        history: list | None, messages: list | None,
                        attachments: list | None = None) -> list:
        if messages:
            return messages
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        if history:
            result.extend(history)
        # 多模态：图片附件追加到末条 user message（不替换整条 messages，
        # 否则 system_prompt（角色人设）与 history（对话历史）会被丢弃）
        if query or attachments:
            content: list | str = query or ""
            if attachments:
                parts: list = [{"type": "text", "text": query}] if query else []
                parts.extend(attachments)
                content = parts
            result.append({"role": "user", "content": content})
        return result

    async def _try_fallback_async(self, messages: list, temperature: float,
                      max_tokens: int, tools: list | None) -> str | None:
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
                return content.strip()  # type: ignore[no-any-return]
            except Exception:  # noqa: BLE001
                entry.mark_failed()
        return None

    async def close(self):
        """关闭本地循环上的 client；其它循环上的交由 `_reap_stale_clients` 回收。"""
        loop = asyncio.get_running_loop()
        entry = self._clients.pop(id(loop), None)
        if entry is not None and entry[0] is loop:
            await entry[1].aclose()
        self._reap_stale_clients(loop)
        if not self._clients:
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
