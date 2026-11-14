"""Speaker 统一出口的意图与投递回执类型（第 1 刀，2026-11-12）。

两条类型是五条出站线（回复/主动/提醒/重要日期/手动）与 Speaker 之间的
**唯一契约**：调用方只声明「谁、往哪个会话、以什么角色、带什么上下文」，
不再各自拼 prompt / 拆条 / 记账。

范围纪律（本刀）：只定义数据结构，不含 IO、不建 LLM 客户端、不读全局配置。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UtteranceIntent:
    """一次发声意图的不可变描述。

    Attributes:
        source: 出站线标识，取值 ``reply|proactive|reminder|important_date|manual``。
        session_key: 归属会话键（用户隔离的唯一维度，显式传入不猜）。
        character_id: 角色 id；空串表示「本次不带角色人设」，Speaker 不会猜
            内置默认角色——调用方必须传入已解析的 id（内置默认角色即 "default"）。
        payload: 可选的调用方上下文（提醒 id、节日名等）；Speaker 只读不写。
    """

    source: str
    session_key: str
    character_id: str = ""
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeliveryResult:
    """一次投递的汇总回执。

    Attributes:
        accepted: 通道是否受理（False 时 ``reason`` 说明原因）。
        accepted_text: 已受理正文的拼接（未受理时只含失败前已受理前缀）。
        segments: 实际交给通道的段序列（顺序与发送顺序一致）。
        channel: 渠道标识，统一取 ``UtteranceIntent.source``。
        reason: 失败原因（成功为空串）；不得包含用户隐私正文。
    """

    accepted: bool
    accepted_text: str = ""
    segments: tuple[str, ...] = ()
    channel: str = ""
    reason: str = ""
