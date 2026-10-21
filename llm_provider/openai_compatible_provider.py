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

from .llm_gateway import ModelRegistry, ProviderError

try:
    from observability.metrics import record_chat_duration, record_error, record_token_usage
    HAS_METRICS = True
except ImportError:
    HAS_METRICS = False

logger = logging.getLogger("llm_provider.openai_compatible")

# OAuth 刷新失败/缺 secret 时的重试冷却（秒）——避免每请求白打 token 端点
_OAUTH_RETRY_COOLDOWN = 300.0

# 单次 provider 调用的结构化耗时日志标记（2026-10-21 FIX-5）。
# 排障时 `grep provider_call` 即可直接取延迟样本，不必再靠外挂探针反推。
# 字段一律 key=value、全部为元数据 —— 不含 prompt / 回复原文（脱敏纪律同
# `terminal_reply_guard` 的 `reply_guard_hit`：只记规则名与长度）。
PROVIDER_CALL_LOG_MARK = "[provider_call]"


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
        stream_enabled: bool = False,
        extra_payload: dict[str, Any] | None = None,
        request_timeout: float | None = None,
        **kwargs: Any,
    ):
        self.provider_name = provider_name
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.auth_mode = auth_mode
        self.max_tokens = max_tokens
        self.temperature = temperature
        # 2026-10-21 FIX-2：缺省 **False**（保持旧行为），由配置显式开启。
        # 「流式聚合」是本批新引入的能力，新能力在未声明的配置下不得改变既有
        # 行为 —— 生产 `config/llm_providers.json` 五个供应商都显式写了
        # ``"stream_enabled": true``，故生产走流式聚合；程序化构造且未声明的
        # 调用方维持原非流式路径（此前该字段是死配置，行为恒为非流式）。
        self.stream_enabled = bool(stream_enabled)
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
        # 可按供应商收紧（request_timeout）：实测 agnes-3.0-flash 健康时 3.8~19.8s、
        # 挂死时恒为 read 超时 25s，而链尾 zhipu 只要 ~1s；给 agnes 配 20s 后
        # 「挂死 → 降级 zhipu」最坏 21s 仍在 30s 预算内，不再吐出超时兜底句。
        self._request_timeout = httpx.Timeout(
            float(request_timeout) if request_timeout else 25.0, connect=10.0
        )

        logger.info(
            "OpenAICompatibleProvider [%s]: api_base=%s, model=%s, auth=%s",
            provider_name, self.api_base, self.model, auth_mode,
        )

    def _log_call_outcome(
        self,
        *,
        model_name: str,
        stream: bool,
        ok: bool,
        elapsed_s: float,
        empty: bool = False,
        error: str = "",
        ttft_s: float | None = None,
        partial: bool = False,
    ) -> None:
        """单次 provider 调用的结构化耗时日志（2026-10-21 FIX-5）。

        背景：此前 provider 自身**不记耗时** —— 日志里唯一相关的只有 wechat 侧
        整轮 `llm_time`，而 `observability.metrics.record_chat_duration` 仅在
        ``HAS_METRICS`` 时生效且不进日志。于是「agnes 单次延迟分布」只能靠外挂
        探针或反推，**无法从生产日志直接取证**（质量悬崖批排查时最大的证据缺口）。

        字段（key=value，便于 grep 与直接算分位数）：
        ``provider`` / ``model`` / ``stream`` / ``ok`` / ``elapsed_ms`` /
        ``ttft_ms``（仅流式）/ ``empty``（空内容响应）/ ``error``（失败时异常类型）
        / ``partial``（流式已下发部分内容后中断）。全部为元数据，**不含原文**。
        """
        parts = [
            PROVIDER_CALL_LOG_MARK,
            f"provider={self.provider_name}",
            f"model={model_name}",
            f"stream={stream}",
            f"ok={ok}",
            f"elapsed_ms={int(max(0.0, float(elapsed_s)) * 1000)}",
        ]
        if ttft_s is not None:
            parts.append(f"ttft_ms={int(max(0.0, float(ttft_s)) * 1000)}")
        parts.append(f"empty={bool(empty)}")
        if not ok:
            parts.append(f"error={error or 'Unknown'}")
        if partial:
            parts.append("partial=True")
        logger.info(" ".join(parts))

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
        for loop, client in list(self._clients.values()):
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
        attachments: list | None = None,
        timeout: float | None = None,
    ) -> str:
        """异步聊天（同步包装版）

        timeout（2026-10-16 三连同义批 D）：按调用覆盖读超时（如主动决策线
        放宽到 25s）。⚠️ 只传 truthy 值——httpx 语义里 per-request
        ``timeout=None`` 是「禁用超时」而非「用客户端默认」，不能无条件传。

        ``stream_enabled``（2026-10-21 FIX-2）：该字段此前是**死配置** ——
        配置里写着 true、前端也能改，但全仓无读取点，实际是否流式恒由调用方
        决定（本函数恒 ``stream=False``）。现在**消费**该开关：为真走流式聚合
        并返回完整文本，为假保持原非流式路径。
        """
        from typing import cast as _cast

        from .multi_provider_gateway import _merge_attachments

        messages, query = _merge_attachments(query, system_prompt, history, messages, attachments)
        if self.stream_enabled:
            return await self._chat_aggregated(
                query=query, system_prompt=system_prompt, history=history,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, model=model, timeout=timeout,
            )
        result = await self._chat(
            query=query, system_prompt=system_prompt, history=history,
            messages=messages, temperature=temperature, max_tokens=max_tokens,
            tools=tools, model=model, stream=False, timeout=timeout,
        )
        return _cast(str, result)

    async def _chat_aggregated(
        self,
        query: str = "",
        system_prompt: str = "",
        history: list | None = None,
        messages: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """流式聚合：消费 ``stream_enabled`` 开关（2026-10-21 FIX-2）。

        与非流式路径（``_chat(stream=False)``）**语义对齐**，这是本改动能安全
        落地的前提 —— 失败必须走**同一条**错误处理链：

        - ``timeout`` 继续生效（主动决策线放宽的 25s 不得静默变成 60s）；
        - 记账（``mark_failed`` / 指标）+ 同 provider 内 ``_try_fallback`` 模型
          兜底 + 统一 ``RuntimeError("模型请求失败，未生成角色回复")``；
        - 聚合为空视同「空内容响应」→ 同样走上面这条链（与非流式同规则）；
        - 失败不返回半截文本：聚合场景没有已下发的 token，部分文本一旦当成
          最终回复落库，就等于丢掉了「换供应商重试」的兜底。
        """
        model_name = model or self.model
        temp = temperature if temperature is not None else self.temperature
        mt = max_tokens or self.max_tokens
        start = time.perf_counter()
        parts: list[str] = []
        entry = self.registry.get_by_name(model_name)
        err: Exception | None = None
        try:
            result = await self._chat(
                query=query, system_prompt=system_prompt, history=history,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                tools=tools, model=model_name, stream=True, timeout=timeout,
            )
            if isinstance(result, str):
                # 无 api_key / 无 api_base 的短路分支：与非流式同构
                return result
            async for token in result:
                if token:
                    parts.append(token)
            if not "".join(parts).strip():
                raise RuntimeError(
                    f"[{self.provider_name}/{model_name}] 流式响应无内容（聚合为空）"
                )
        except Exception as e:  # noqa: BLE001
            err = e

        if err is None:
            if HAS_METRICS:
                record_chat_duration(
                    f"{self.provider_name}/{model_name}", time.perf_counter() - start,
                )
            if entry:
                entry.mark_success()
            return "".join(parts).strip()

        # 与非流式路径同一条错误链（见 _chat 的 except 分支）
        if HAS_METRICS:
            record_error("llm", type(err).__name__)
        if entry:
            entry.mark_failed()
        logger.warning(
            "[%s/%s] 流式聚合失败: %s: %s",
            self.provider_name, model_name, type(err).__name__, err,
        )
        built = self._build_messages(query, system_prompt, history, messages)
        fallback = await self._try_fallback(built, temp, mt, tools)
        if fallback:
            return fallback
        raise RuntimeError("模型请求失败，未生成角色回复") from err

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
            # W2 三态契约（2026-09-27）：失败必须 raise ProviderFailure——
            # 旧实现把异常吞成错误文案字典，编排层把错误文案当「模型判无需工具」，
            # 把用户正在补齐提醒信息的 pending 意图取消掉。
            raise ProviderError(self._handle_error(e)) from e

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
        timeout: float | None = None,
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
            return self._stream_chat(payload, model_name, timeout=timeout)

        start = time.perf_counter()
        resp = None
        empty_content = False
        try:
            # timeout 仅 truthy 时覆盖（None 在 httpx per-request 语义=禁用超时）。
            post_kwargs: dict[str, Any] = {}
            if timeout:
                post_kwargs["timeout"] = httpx.Timeout(float(timeout), connect=10.0)
            resp = await self._async_client.post(self._chat_url, json=payload, **post_kwargs)
            resp.raise_for_status()
            data = resp.json()
            message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
            content = message.get("content")
            # 2026-10-20 空内容响应根治：HTTP 200 但 content=None/空（推理模型
            # 只回 reasoning_content、上游截断、限流软失败）旧实现先 mark_success
            # 再 content.strip() → AttributeError 被 except 吞掉走 fallback，健康
            # 统计被污染且日志无痕。现在在 mark_success **之前**判空并抛错。
            if not isinstance(content, str) or not content.strip():
                empty_content = True
                raise RuntimeError(
                    f"[{self.provider_name}/{model_name}] 空内容响应: "
                    f"choices={len(data.get('choices') or [])} "
                    f"reasoning_only={bool(message.get('reasoning_content'))} "
                    f"message_keys={sorted(message)}"
                )
            usage = data.get("usage", {})

            if HAS_METRICS:
                record_chat_duration(f"{self.provider_name}/{model_name}", time.perf_counter() - start)
                if usage:
                    record_token_usage(f"{self.provider_name}/{model_name}", "prompt", usage.get("prompt_tokens", 0))
                    record_token_usage(f"{self.provider_name}/{model_name}", "completion", usage.get("completion_tokens", 0))

            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_success()
            # 2026-10-21 FIX-5：成功路径的结构化耗时（生产延迟分布的直接取证点）
            self._log_call_outcome(
                model_name=model_name, stream=False, ok=True,
                elapsed_s=time.perf_counter() - start,
            )
            return content.strip()
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm", type(e).__name__)
            entry = self.registry.get_by_name(model_name)
            if entry:
                entry.mark_failed()
            body_prefix = ""
            try:
                if resp is not None:
                    body_prefix = str(resp.text)[:300]
            except Exception:  # noqa: BLE001
                pass
            # 2026-10-21 FIX-5：失败路径同样留耗时 —— 慢失败（如 40s 读超时）
            # 才是延迟分布里最要紧的那一撮，缺它等于只统计成功样本。
            self._log_call_outcome(
                model_name=model_name, stream=False, ok=False,
                elapsed_s=time.perf_counter() - start,
                empty=empty_content, error=type(e).__name__,
            )
            logger.warning(
                "[%s/%s] 请求失败: %s: %s | body=%s",
                self.provider_name, model_name, type(e).__name__, e, body_prefix,
            )
            fallback = await self._try_fallback(built, temp, mt, tools)
            if fallback:
                return fallback
            raise RuntimeError("模型请求失败，未生成角色回复") from e

    async def _stream_chat(
        self, payload: dict, model_name: str, timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """流式产出 token。

        P1-1（2026-09-21 审查修复）：错误不再 yield 成"内容"——旧实现把
        （xx API 请求失败，错误代码 401）逐字推给前端、计入 full_reply、
        经 after_chat 写进 chat_history 污染下轮上下文，且网关看不到失败
        （流式绕开了 fallback 链）。现在：
        - 首 token 前失败 → raise（网关 chat_stream 捕获后走下一 provider）；
        - 已产出内容后失败 → 记日志干净收尾（重放会造成半句+整句重复）；
        - SSE 200 + error 事件帧 → 同样 raise，不再 KeyError 静默吞。

        ``timeout``（2026-10-21 FIX-2）：按调用覆盖读超时。此前流式分支**完全
        忽略**该实参，而 ``chat()`` 现在会在 ``stream_enabled`` 为真时走流式
        聚合 —— 不透传会让调用方的收紧预算（主动决策线 25s）静默失效。
        仍只传 truthy 值（httpx 语义：per-request ``None`` = 禁用超时）。
        """
        first_token_time = None
        emitted = False
        start = time.perf_counter()
        stream_kwargs: dict[str, Any] = {}
        if timeout:
            stream_kwargs["timeout"] = httpx.Timeout(float(timeout), connect=10.0)
        try:
            async with self._async_client.stream(
                "POST", self._chat_url, json=payload, **stream_kwargs,
            ) as resp:
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
            # 2026-10-21 FIX-5：流式正常读完（收到 [DONE] 或流自然结束）。
            # ttft 是流式唯一可靠的延迟信号（总时长受回复长度影响）。
            self._log_call_outcome(
                model_name=model_name, stream=True, ok=True,
                elapsed_s=time.perf_counter() - start,
                ttft_s=(first_token_time - start) if first_token_time else None,
            )
        except asyncio.TimeoutError as e:
            logger.warning("[%s] Stream timeout (60s)", self.provider_name)
            # 已下发部分内容 → 干净收尾不再重放；但日志必须区分它与正常成功
            # （partial=True），否则延迟样本会把「半截回复」算成好样本。
            self._log_call_outcome(
                model_name=model_name, stream=True, ok=False,
                elapsed_s=time.perf_counter() - start,
                ttft_s=(first_token_time - start) if first_token_time else None,
                error="TimeoutError", partial=bool(emitted),
            )
            if emitted:
                return
            raise RuntimeError(f"[{self.provider_name}] 流式生成超时") from e
        except Exception as e:  # noqa: BLE001
            if HAS_METRICS:
                record_error("llm_stream", type(e).__name__)
            self._log_call_outcome(
                model_name=model_name, stream=True, ok=False,
                elapsed_s=time.perf_counter() - start,
                ttft_s=(first_token_time - start) if first_token_time else None,
                error=type(e).__name__, partial=bool(emitted),
            )
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
                message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError(
                        f"空内容响应: choices={len(data.get('choices') or [])} "
                        f"message_keys={sorted(message)}"
                    )
                entry.mark_success()
                logger.info("[%s] Fallback: %s → %s succeeded", self.provider_name, self.model, entry.name)
                return content.strip()
            except Exception as e:  # noqa: BLE001
                entry.mark_failed()
                logger.warning(
                    "[%s] Fallback %s → %s failed: %s: %s",
                    self.provider_name, self.model, entry.name, type(e).__name__, e,
                )
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
