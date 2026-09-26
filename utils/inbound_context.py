"""入站消息身份的请求级上下文 —— 唯一 owner（缺陷 I）。

**为什么存在**：同一句「明早六点叫我起床」可能被执行两次——微信 ``get_updates``
重发、宿主崩溃后新进程接管、或终审守卫**补跑** ``set_reminder``。消息级幂等账
（``proactive.runtime_plane.inbound_claim``）只覆盖"整回合重放"，回合**内部**的
副作用还需要一个稳定身份键，而 ``turn_id`` 每次都是新 UUID（不能用来去重）。

与 :mod:`utils.llm_bridge` 的 ``request_llm`` 同一模式：请求级值用 ContextVar
承载，编排链路内（``await`` / ``asyncio.to_thread`` / ``copy_context`` 提交线程池）
自动继承。**边界**：跨线程桥（连接器同步线程 → 常驻共享循环）ContextVar **不
随之传播**，所以入站 id 在那一跳必须显式传参（``UserManager.process_message(
message_id=...)``）后再在这里绑定。

无入站 id（web / console / 主动线）时上下文为空串 —— 调用方据此**不去重**，
宁可不 dedup，也不拿别的消息的键误吞本轮的副作用。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

request_message_id: ContextVar[str] = ContextVar("request_message_id", default="")


def current_message_id() -> str:
    """本轮的入站消息 id（无入站身份时为空串）。"""
    return str(request_message_id.get() or "")


@contextmanager
def bind_message_id(message_id: str) -> Iterator[str]:
    """把入站消息 id 绑定到当前请求上下文，退出必复位（禁止跨请求残留）。"""
    value = str(message_id or "").strip()
    token = request_message_id.set(value)
    try:
        yield value
    finally:
        request_message_id.reset(token)
