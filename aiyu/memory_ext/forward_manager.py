"""转发管理器 — 跨角色转发记忆。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aiyu.memory_ext.forward_manager")


class ForwardManager:
    def __init__(self):
        self._forward_log: list[dict[str, Any]] = []

    def forward(self, from_character: str, to_character: str, memory_id: str, memory_content: str = "") -> bool:
        record = {
            "from": from_character,
            "to": to_character,
            "memory_id": memory_id,
            "content": memory_content,
        }
        self._forward_log.append(record)
        logger.info("记忆转发: %s → %s, memory_id=%s", from_character, to_character, memory_id)
        return True

    def get_forwards(self, character_id: str) -> list[dict[str, Any]]:
        return [
            r for r in self._forward_log
            if r["to"] == character_id
        ]
