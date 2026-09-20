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

    def search(self, query: str, top_k: int = 5,
               session_id: str | None = None) -> list[dict[str, Any]]:
        """情景检索。session_id 非 None 时按 meta.session_id 过滤。

        2026-09-21：旧实现全局 Chroma 检索无用户维度，多用户并发会串台。
        空 session_id 元数据的历史片段不注入任何具体会话。
        """
        raw = self._vm._search("episodic_memory", query, top_k * 3 if session_id else top_k)
        items = raw if isinstance(raw, list) else []
        if session_id is None:
            return items[:top_k]
        sid = str(session_id)
        filtered = []
        for r in items:
            meta = r.get("metadata") or {}
            ep_session = str(meta.get("session_id") or r.get("session_id") or "")
            if ep_session and ep_session == sid:
                filtered.append(r)
            elif not ep_session and sid:
                # 无归属元数据的片段不得注入具体会话
                continue
        return filtered[:top_k]

    def get_recent_episodes(self, n: int = 10,
                            session_id: str | None = None) -> list[dict[str, Any]]:
        coll = self._vm._collections.get("episodic_memory")
        if coll is None:
            return []
        try:
            results = coll.get(limit=n * 3 if session_id else n)
            if not results or not results.get("documents"):
                return []
            items = []
            for doc, meta in zip(results["documents"], results.get("metadatas", [{}] * len(results["documents"])), strict=False):
                meta = meta or {}
                if session_id is not None:
                    ep_session = str(meta.get("session_id") or "")
                    if not ep_session or ep_session != str(session_id):
                        continue
                items.append({"content": doc, "metadata": meta})
            return items[:n]
        except Exception as e:  # noqa: BLE001
            logger.warning("get_recent_episodes failed: %s", e)
            return []
