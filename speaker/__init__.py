"""Speaker 统一出口包（第 1 刀，2026-11-12）。

对外只暴露五个名字：意图/回执两类型、能力模型、话题 facade、Speaker 核心。
调用方禁止从子模块深导入（防止实现细节扩散成第二契约）。
"""

from __future__ import annotations

from speaker.capabilities import CapabilityModel
from speaker.speaker import Speaker
from speaker.thread_state import ThreadState
from speaker.types import DeliveryResult, UtteranceIntent

__all__ = [
    "UtteranceIntent",
    "DeliveryResult",
    "CapabilityModel",
    "ThreadState",
    "Speaker",
]
