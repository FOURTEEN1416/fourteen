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
import contextlib
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

# OAuth 刷新失败/缺 secret 时的重试冷却（秒）——避免每请求白打 token 端点
_OAUTH_RETRY_COOLDOWN = 300.0


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
        self._oauth_warned_no_secret: bool = False

        # 连接池
        self._pool_limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)
        # P1-5（2026-09-21 审查修复）：AsyncClient 按**所属 loop** 缓存一格，
        # 而不是单格槽位轮换重建。uvicorn 主循环与常驻 sync loop 交替命中是常态
        # （web 聊天 vs ASE/调度线程），旧写法每次切换整体弃建 → 每轮重做 TLS
        # 握手，且把旧 client 的 aclose() 调度到**新** loop 上执行（跨 loop 关闭
        # 未定义）。现在各 loop 复用各自的 client；宿主 loop 已关闭/被回收的条目
        # 连同引用丢弃（不能跨 loop await，交给 GC）。
        self._clients: dict[int, tuple[Any, httpx.AsyncClient]] = {}  # loop_id -> (loop, client)
        self._sync_client: httpx.Client | None = None
        # P1-2：非流式单请求超时 25s —— 必须小于编排层整链预算（30s），
        # 否则链首网络黑洞一个 provider 就能烧光整条链的时间。
        self._request_timeout = httpx.Timeout(25.0, connect=10.0)

        logger.info(
            "OpenAICompatibleProvider [%s]: api_base=%s, model=%s, auth=%s",
            provider_name, self.api_base, self.model, auth_mode,
        )

    @property
    def _async_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        entry = self._clients.get(loop_id)
        if entry is not None:
            owner, client = entry
            if owner is loop and not owner.is_closed():
                return client
            # 宿主 loop 已关闭（或 id 被复用）：丢弃该格，不再跨 loop 关闭
            del self._clients[loop_id]
        # headers 传引用语义由 httpx 拷贝快照——OAuth 刷新后需 _refresh_client_headers
        client = httpx.AsyncClient(
            timeout=self._request_timeout,
            limits=self._pool_limits,
            headers=self._headers,
        )
        self._clients[loop_id] = (loop, client)
        return client

    def _refresh_client_headers(self) -> None:
        """把最新的 self._headers 同步进所有仍存活的 client（OAuth token 轮换后调用）。

        P1-3③：此前只改 dict，client 构造时已拷贝快照 → 新 token 永远不生效。
        """
        for loop, client in list(self._clients.items()):
            try:
                if not loop.is_closed():
                    client.headers.update(self._headers)
            except Exception:  # noqa: BLE001
                pass
        if self._sync_client is not None:
            with contextlib.suppress(Exception):
                self._sync_client.headers.update(self._headers)

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
        from typing import cast as _cast

        result = await self._chat(
            query=query, system_prompt=system_prompt, history=history,
            messages=messages, temperature=temperature, max_tokens=max_tokens,
            tools=tools, model=model, stream=False,
        )
        return _cast(str, result)

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
        """流式产出 token。

        P1-1（2026-09-21 审查修复）：错误不再 yield 成"内容"——旧实现把
        （xx API 请求失败，错误代码 401）逐字推给前端、计入 full_reply、
        经 after_chat 写进 chat_history 污染下轮上下文，且网关看不到失败
        （流式绕开了 fallback 链）。现在：
        - 首 token 前失败 → raise（网关 chat_stream 捕获后走下一 provider）；
        - 已产出内容后失败 → 记日志干净收尾（重放会造成半句+整句重复）；
        - SSE 200 + error 事件帧 → 同样 raise，不再 KeyError 静默吞。
        """
        first_token_time = None
        emitted = False
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
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("error"):
                            # 部分供应商在 200 SSE 里下发 error 事件帧
                            raise RuntimeError(
                                f"[{self.provider_name}] SSE error 帧: {str(chunk['error'])[:200]}"
                            )
                        try:
                            delta = chunk["choices"][0]["delta"]
                            if "content" in delta and delta["content"]:
                                if first_token_time is None:
                                    first_token_time = time.perf_counter()
                                    if first_token_time - start > 3.0:
                                        logger.warning("[%s] Stream first token timeout (>3s)", self.provider_name)
                                emitted = True
                                yield delta["content"]
                        except (KeyError, IndexError):
                            # 无 choices 的心跳/角色帧继续，其余保持宽松跳过
                            continue
        except asyncio.TimeoutError as e:
            logger.warning("[%s] Stream timeout (60s)", self.provider_name)
            if emitted:
                return
            raise RuntimeError(f"[{self.provider_name}] 流式生成超时") from e
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm_stream", type(e).__name__)
            if emitted:
                logger.warning("[%s] Stream 中断（已部分内容下发，不重放）: %s", self.provider_name, e)
                return
            raise

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
        """百度千帆 OAuth token 刷新。

        P1-3（2026-09-21 审查修复）：
        ① 旧实现用**同步** httpx.post——在 async 函数里触发即冻结整个 worker 循环；
        ② secret 恒空（网关构造不传）时旧实现每次请求都白打一发 token 端点再吞掉；
           现在缺 secret 直接判不可用并置冷却，失败同样置冷却；
        ③ 刷新成功后必须把新 Authorization 同步进已缓存的 httpx client
           （构造时拷贝过快照），否则 token 轮换不生效。
        """
        if self.auth_mode != "oauth":
            return
        now = time.time()
        if now < self._oauth_expires_at - 60:
            return  # token 还有效（含失败冷却：冷却期内 _oauth_expires_at 被拨到未来）
        if not self._api_secret:
            if not self._oauth_warned_no_secret:
                logger.error(
                    "[%s] OAuth 模式缺少 api_secret，token 永远换不到——"
                    "请在配置中补 BAIDU/对应 provider 的 api_secret 或改用 bearer",
                    self.provider_name,
                )
                self._oauth_warned_no_secret = True
            self._oauth_expires_at = now + _OAUTH_RETRY_COOLDOWN
            return
        try:
            token_url = "https://aip.baidubce.com/oauth/2.0/token"
            params = {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self._api_secret,
            }
            resp = await self._async_client.post(token_url, params=params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            self._oauth_token = data.get("access_token", "")
            if not self._oauth_token:
                raise RuntimeError(f"token 响应无 access_token: {str(data)[:200]}")
            expires_in = data.get("expires_in", 2592000)  # 默认30天
            self._oauth_expires_at = time.time() + expires_in
            # 更新 Authorization header —— 对后续请求真正生效需要同时更新 client 快照
            self._headers["Authorization"] = f"Bearer {self._oauth_token}"
            self._refresh_client_headers()
            logger.info("[%s] OAuth token refreshed, expires in %ds", self.provider_name, expires_in)
        except Exception as e:  # noqa: BLE001
            self._oauth_expires_at = now + _OAUTH_RETRY_COOLDOWN
            logger.warning(
                "[%s] OAuth refresh failed（%.0f 分钟冷却后重试）: %s",
                self.provider_name, _OAUTH_RETRY_COOLDOWN / 60, e,
            )

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
        # 只 await 本 loop 持有的 client；其他 loop 的格子丢弃引用
        # （跨 loop aclose 未定义，宿主 loop 多已停止）
        clients = list(self._clients.values())
        self._clients.clear()
        me = id(asyncio.get_running_loop())
        for loop, client in clients:
            with contextlib.suppress(Exception):
                if id(loop) == me:
                    await client.aclose()
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None
