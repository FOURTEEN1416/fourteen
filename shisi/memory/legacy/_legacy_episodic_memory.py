"""
情景记忆 — 对话片段归档（memory_pipeline 内联版本）

基于向量检索 + 摘要压缩 + 分层存储的对话片段归档。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

logger = logging.getLogger("episodic_memory")


class EpisodicMemory:
    """情景记忆 — 对话片段归档（向量检索 + 摘要压缩 + 分层存储）"""

    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory
        self._cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()

    def store_episode(self, messages: list[dict], summary: str = "",
                      importance: float = 0.5, session_id: str = "") -> str:
        if not messages:
            return ""
        episode_id = f"ep_{int(time.time())}_{hashlib.md5(str(messages).encode()).hexdigest()[:8]}"
        if not summary:
            summary = self._generate_summary(messages)
        content = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages
        )
        metadata = {
            "type": "episode",
            "episode_id": episode_id,
            "session_id": session_id,
            "summary": summary,
            "importance": importance,
            "message_count": len(messages),
            "timestamp": time.time(),
        }
        try:
            self._vm.store_text_sync(content, metadata)
            self._sm.add_episode(episode_id, summary, importance, metadata)
            logger.debug("Episode stored: %s", episode_id)
            return episode_id
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to store episode: %s", e)
            return ""

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        try:
            return self._vm.search_sync(query, top_k=top_k,  # type: ignore[no-any-return]
                                   filter_dict={"type": "episode"})
        except Exception as e:  # noqa: BLE001
            logger.warning("Episode search failed: %s", e)
            return []

    def _generate_summary(self, messages: list[dict]) -> str:
        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        if not user_msgs:
            return ""
        if len(user_msgs[-1]) > 10:
            return user_msgs[-1][:100]  # type: ignore[no-any-return]
        longest = max(user_msgs, key=len, default="")
        return longest[:100] if longest else ""
