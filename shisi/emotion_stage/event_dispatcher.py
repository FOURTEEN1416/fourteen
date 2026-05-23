"""阶段变更事件分发器 — 观察者模式。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("shisi.emotion_stage.event_dispatcher")


@dataclass
class StageChangeEvent:
    character_id: str
    old_stage: str
    new_stage: str
    old_index: int
    new_index: int
    affinity: float


class EventDispatcher:
    def __init__(self):
        self._listeners: list[Callable[[StageChangeEvent], Any]] = []

    def subscribe(self, listener: Callable[[StageChangeEvent], Any]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[StageChangeEvent], Any]) -> None:
        self._listeners = [fn for fn in self._listeners if fn is not listener]

    async def dispatch(self, event: StageChangeEvent) -> None:
        for listener in self._listeners:
            try:
                result = listener(event)
                if hasattr(result, '__await__'):
                    await result
            except Exception as e:
                logger.error("事件监听器异常: %s", e)

    def dispatch_sync(self, event: StageChangeEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error("事件监听器异常: %s", e)
