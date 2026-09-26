"""
情景记忆 — 对话片段归档（memory_pipeline 内联版本）

基于向量检索 + 摘要压缩 + 分层存储的对话片段归档。
"""

from __future__ import annotations

import hashlib
import json
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
                      importance: float = 0.5, session_id: str = "", character_id: str = "") -> str:
        if not messages:
            return ""
        source = [session_id, character_id, [
            {k: row.get(k) for k in ("id", "turn_id", "role", "content", "timestamp")} for row in messages
        ]]
        episode_id = "ep_" + hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        if not summary:
            summary = self._generate_summary(messages)
        content = "\n".join(
            f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages
        )
        metadata = {
            "type": "episode",
            "episode_id": episode_id,
            "session_id": session_id,
            "character_id": character_id,
            "summary": summary,
            "importance": importance,
            "message_count": len(messages),
            "timestamp": time.time(),
        }
        try:
            if self._vm is None or not self._vm.store_text_sync(content, metadata):
                return ""
            # 2026-09-21：StructuredMemory 无 add_episode 时跳过结构化旁路，
            # 避免生产日志刷 "Failed to store episode"；向量侧已带 session_id meta。
            if hasattr(self._sm, "add_episode"):
                self._sm.add_episode(episode_id, summary, importance, metadata)
            logger.debug("Episode stored: %s session=%s", episode_id, session_id)
            return episode_id
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to store episode: %s", e)
            return ""

    def search(self, query: str, top_k: int = 5,
               session_id: str | None = None, character_id: str = "") -> list[dict]:
        """归属和类型过滤下推到向量查询，在 top_k 截断之前执行。"""
        filters = {"type": "episode"}
        if session_id is not None:
            filters["session_id"] = str(session_id)
        if character_id:
            filters["character_id"] = character_id
        try:
            raw = self._vm.search_sync(query, top_k=top_k, filter_dict=filters) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("Episode search failed: %s", e)
            return []
        return [r for r in raw if all((r.get("metadata") or {}).get(k) == v
                                     for k, v in filters.items())][:top_k]

    def _generate_summary(self, messages: list[dict]) -> str:
        from .conversation_summarizer import ConversationSummarizer

        rows = ConversationSummarizer._source_rows(messages[-2:], 70)
        return "；".join(f"{row['speaker']}曾说「{row['content']}」" for row in rows)
