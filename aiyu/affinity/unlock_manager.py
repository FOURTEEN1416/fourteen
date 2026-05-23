"""好感度解锁管理 — 阈值→解锁动作映射。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..config import get_config

logger = logging.getLogger("aiyu.affinity.unlock_manager")


@dataclass
class UnlockEvent:
    threshold: int
    unlock_type: str
    name: str


class UnlockManager:
    def __init__(self):
        self._unlocks: list[UnlockEvent] = []
        self._listeners: list[Callable[[str, UnlockEvent], Any]] = []
        self._load_from_config()

    def _load_from_config(self) -> None:
        raw = get_config("affinity", "unlocks", [])
        for u in raw:
            self._unlocks.append(UnlockEvent(
                threshold=u.get("threshold", 0),
                unlock_type=u.get("type", ""),
                name=u.get("name", ""),
            ))

    def check_unlocks(self, character_id: str, old_affinity: float, new_affinity: float) -> list[UnlockEvent]:
        newly_unlocked = []
        for u in self._unlocks:
            if old_affinity < u.threshold <= new_affinity:
                newly_unlocked.append(u)
                logger.info("好感度解锁: %s → %s (阈值%d)", character_id, u.name, u.threshold)
                for listener in self._listeners:
                    try:
                        listener(character_id, u)
                    except Exception as e:
                        logger.error("解锁事件监听器异常: %s", e)
        return newly_unlocked

    def get_unlocks_at(self, affinity: float) -> list[UnlockEvent]:
        return [u for u in self._unlocks if affinity >= u.threshold]

    def subscribe(self, listener: Callable[[str, UnlockEvent], Any]) -> None:
        self._listeners.append(listener)
