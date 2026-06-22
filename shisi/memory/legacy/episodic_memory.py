from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("episodic_memory")


class EpisodicMemory:
    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory

    def store_episode(self, messages: list[dict], summary: str = "",
                      importance: float = 0.5, session_id: str = "") -> str | None:
        if not messages:
            return None
        content_parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            text = msg.get("content", "")
            content_parts.append(f"{role}: {text}")
        doc = "\n".join(content_parts)
        meta = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "type": "episode",
            "session_id": session_id,
            "importance": importance,
            "message_count": len(messages),
            "summary": summary[:200] if summary else "",
        }
        coll = self._vm._collections.get("episodic_memory")
        if coll is None:
            return None
        doc_id = f"ep_{hashlib.md5(doc.encode()).hexdigest()[:12]}"
        try:
            coll.add(documents=[doc], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:  # noqa: BLE001
            logger.warning("store_episode failed: %s", e)
            return None

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        result = self._vm._search("episodic_memory", query, top_k)
        return result if isinstance(result, list) else []

    def get_recent_episodes(self, n: int = 10) -> list[dict[str, Any]]:
        coll = self._vm._collections.get("episodic_memory")
        if coll is None:
            return []
        try:
            results = coll.get(limit=n)
            if not results or not results.get("documents"):
                return []
            items = []
            for doc, meta in zip(results["documents"], results.get("metadatas", [{}] * len(results["documents"])), strict=False):
                items.append({"content": doc, "metadata": meta})
            return items
        except Exception as e:  # noqa: BLE001
            logger.warning("get_recent_episodes failed: %s", e)
            return []
