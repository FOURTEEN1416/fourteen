"""LLM 网关 → 同步单串函数 的唯一适配器。

## 为什么存在（2026-09-21 重扫：一个被静默关掉的能力）

记忆管道里三处写了同一个坏判据：

```python
self.fe = FactExtractor(llm_func=self._llm if callable(self._llm) else None)
self.ds = DiarySummarizer(llm_func=self._llm if callable(self._llm) else None)
self.reflection = ReflectionEngine(llm_func=self._llm if callable(self._llm) else None)
```

``self._llm`` 是**网关对象**（`ReloadableLLMGateway` / `MultiProviderGateway`），
不带 ``__call__`` → ``callable()`` 恒为 False → **LLM 抽取/日记/反思三条能力
全部退化为 None**，事实只能由正则规则产出（生产实证的碎片事实
「叫我」「上班」即由此而来）。能力"存在于代码里、从未启用"。

对标 nana `prompts/memory_extract.md`：事实抽取是一次结构化 LLM 调用
（summary / topics / emotional_valence / importance / user_facts 分桶），
而不是关键词正则。

本模块是唯一适配点：任何"需要 `prompt -> str` 同步函数"的组件都从这里取，
禁止再写 `callable(llm)` 判据。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from inspect import isasyncgenfunction, signature
from typing import Any

logger = logging.getLogger("utils.llm_bridge")

# 辅助摘要/抽取等共享组件只持适配器，实际网关随请求上下文，不改共享实例属性。
request_llm: ContextVar[Any] = ContextVar("request_llm", default=None)


def current_llm(default: Any = None) -> Any:
    selected = request_llm.get()
    return selected if selected is not None else default


def request_scoped_llm(method):
    """为普通/流式编排入口绑定网关快照，退出必复位；子任务按ContextVar继承。"""
    params = signature(method)

    def bind(self, args, kwargs):
        from llm_provider import select_request_llm

        bound = params.bind(self, *args, **kwargs)
        selected = select_request_llm(
            self.components.get("llm"), bound.arguments.get("user_id"),
            bound.arguments.get("user_llm_config"),
        )
        return selected

    if isasyncgenfunction(method):
        @wraps(method)
        async def stream(self, *args, **kwargs):
            selected = bind(self, args, kwargs)
            gen = method(self, *args, **kwargs)
            try:
                while True:
                    token = request_llm.set(selected)
                    try:
                        event = await anext(gen)
                    except StopAsyncIteration:
                        break
                    finally:
                        request_llm.reset(token)
                    yield event
            finally:
                token = request_llm.set(selected)
                try:
                    await gen.aclose()
                finally:
                    request_llm.reset(token)
        return stream

    @wraps(method)
    async def call(self, *args, **kwargs):
        token = request_llm.set(bind(self, args, kwargs))
        try:
            return await method(self, *args, **kwargs)
        finally:
            request_llm.reset(token)
    return call


def to_sync_callable(
    llm: Any,
    *,
    max_tokens: int = 512,
    temperature: float = 0.3,
) -> Callable[[str], str] | None:
    """把网关适配成 ``prompt -> str`` 的同步函数；无法适配时返回 None。

    - 网关带 ``chat_sync``（本项目所有网关都有）→ 包装它；
    - 本身就是可调用对象 → 直接用；
    - 调用失败返回空串（调用方按"无结果"处理），**不抛异常** ——
      这些调用点在后台记忆线程里，异常会打断整批记忆处理。
    """
    if llm is None:
        return None

    if hasattr(llm, "chat_sync"):
        def _call_via_gateway(prompt: str) -> str:
            try:
                result = current_llm(llm).chat_sync(
                    query=prompt, max_tokens=max_tokens, temperature=temperature,
                )
                return str(result or "")
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM 同步调用失败（按空结果处理）: %s", e)
                return ""
        return _call_via_gateway

    if callable(llm):
        def _call_direct(prompt: str) -> str:
            try:
                return str(llm(prompt) or "")
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM 可调用对象执行失败（按空结果处理）: %s", e)
                return ""
        return _call_direct

    return None
