"""
向量记忆系统 — 基于 ChromaDB (async + sync 兼容)

管理三种向量记忆：
1. chat_history: 聊天历史（用于语义检索）
2. user_facts: 用户事实知识
3. emotion_logs: 情绪变化日志

核心方法提供 async 和 sync 两种入口：
- async 方法（store_chat, search 等）供 async 上下文调用
- sync 方法（store_chat_sync, search_sync 等）供同步上下文调用
内部通过 asyncio.to_thread 将 ChromaDB 同步操作移至线程池。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("vector_memory")

try:
    import chromadb
    from chromadb.api.models.Collection import Collection
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    Collection = Any  # type: ignore


def _run_async(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


class VectorMemory:
    """
    ChromaDB 向量记忆封装 (async + sync)

    所有公开方法同时提供 async 和 sync 版本：
    - async: store_chat(), search() 等 — 供 async 上下文
    - sync: store_chat_sync(), search_sync() 等 — 供同步上下文
    """

    COLLECTIONS = ["chat_history", "user_facts", "emotion_logs", "episodic_memory", "semantic_knowledge", "emotion_trajectory"]

    def __init__(self, chroma_path: str = "./data/chroma_db"):
        self.chroma_path = os.path.abspath(chroma_path)
        self._collections: dict[str, Any] = {
            name: None for name in self.COLLECTIONS
        }

        if HAS_CHROMADB:
            self._init()
        else:
            logger.warning("chromadb not installed, VectorMemory runs in fallback mode")

    def _init(self) -> None:
        try:
            os.makedirs(self.chroma_path, exist_ok=True)
            client = chromadb.PersistentClient(path=self.chroma_path)
            ef = embedding_functions.DefaultEmbeddingFunction()
            for name in self.COLLECTIONS:
                try:
                    self._collections[name] = client.get_or_create_collection(
                        name=name,
                        embedding_function=ef,  # type: ignore
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to init collection '%s': %s", name, e)

            logger.info("VectorMemory ready: %s", self.chroma_path)
        except Exception as e:  # noqa: BLE001
            logger.error("ChromaDB init failed: %s", e)

    # ── 聊天历史 ──────────────────────────────────────────

    async def store_chat(self, user_msg: str, reply: str, metadata: dict | None = None) -> str | None:
        coll = self._collections.get("chat_history")
        if coll is None:
            return None
        doc = f"User: {user_msg}\nAssistant: {reply}"
        meta = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "type": "chat",
            "user_msg_len": len(user_msg),
            "reply_len": len(reply),
        }
        if metadata:
            meta.update(metadata)
        doc_id = f"chat_{hashlib.md5(doc.encode()).hexdigest()[:12]}"
        try:
            await asyncio.to_thread(coll.add, documents=[doc], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:  # noqa: BLE001
            logger.warning("store_chat failed: %s", e)
            return None

    def store_chat_sync(self, user_msg: str, reply: str, metadata: dict | None = None) -> str | None:
        return _run_async(self.store_chat(user_msg, reply, metadata))  # type: ignore[no-any-return]

    async def search_chats(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return await self._search("chat_history", query, top_k)

    def search_chats_sync(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return _run_async(self.search_chats(query, top_k))  # type: ignore[no-any-return]

    # ── 用户事实 ──────────────────────────────────────────

    async def store_fact(self, fact: str, category: str = "general", confidence: float = 0.5) -> str | None:
        coll = self._collections.get("user_facts")
        if coll is None:
            return None
        doc_id = f"fact_{hashlib.md5(fact.encode()).hexdigest()[:12]}"
        meta = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "category": category,
            "confidence": confidence,
        }
        try:
            await asyncio.to_thread(coll.add, documents=[fact], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:  # noqa: BLE001
            logger.warning("store_fact failed: %s", e)
            return None

    def store_fact_sync(self, fact: str, category: str = "general", confidence: float = 0.5) -> str | None:
        return _run_async(self.store_fact(fact, category, confidence))  # type: ignore[no-any-return]

    async def add_batch(self, documents: list[str], metadatas: list[dict[str, Any]],
                  ids: list[str], collection: str = "user_facts") -> bool:
        if len(documents) != len(metadatas) or len(documents) != len(ids):
            logger.error("add_batch: documents/metadatas/ids length mismatch")
            return False
        coll = self._collections.get(collection)
        if coll is None:
            return False
        try:
            await asyncio.to_thread(coll.add, documents=documents, metadatas=metadatas, ids=ids)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("add_batch failed: %s", e)
            return False

    def add_batch_sync(self, documents: list[str], metadatas: list[dict[str, Any]],
                  ids: list[str], collection: str = "user_facts") -> bool:
        return _run_async(self.add_batch(documents, metadatas, ids, collection))  # type: ignore[no-any-return]

    async def search_facts(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return await self._search("user_facts", query, top_k)

    def search_facts_sync(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return _run_async(self.search_facts(query, top_k))  # type: ignore[no-any-return]

    async def get_all_facts(self) -> list[str]:
        coll = self._collections.get("user_facts")
        if coll is None:
            return []
        try:
            results = await asyncio.to_thread(coll.get)
            return results.get("documents", [])  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001
            logger.warning("get_all_facts failed: %s", e)
            return []

    def get_all_facts_sync(self) -> list[str]:
        return _run_async(self.get_all_facts())  # type: ignore[no-any-return]

    # ── 情绪日志 ──────────────────────────────────────────

    async def store_emotion_log(self, emotion: str, intensity: float, trigger: str = "") -> str | None:
        coll = self._collections.get("emotion_logs")
        if coll is None:
            return None
        doc = f"情感: {emotion}, 强度: {intensity:.2f}, 触发: {trigger}"
        meta = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "emotion": emotion,
            "intensity": intensity,
            "trigger": trigger,
        }
        try:
            doc_id = f"emotion_{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            await asyncio.to_thread(coll.add, documents=[doc], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:  # noqa: BLE001
            logger.warning("store_emotion_log failed: %s", e)
            return None

    def store_emotion_log_sync(self, emotion: str, intensity: float, trigger: str = "") -> str | None:
        return _run_async(self.store_emotion_log(emotion, intensity, trigger))  # type: ignore[no-any-return]

    async def get_recent_emotions(self, n: int = 10) -> list[dict[str, Any]]:
        coll = self._collections.get("emotion_logs")
        if coll is None:
            return []
        try:
            results = await asyncio.to_thread(coll.get, limit=n)
            if not results or not results.get("metadatas"):
                return []
            items = []
            for meta, doc in zip(results["metadatas"], results["documents"], strict=False):
                items.append({"metadata": meta, "content": doc})
            return items
        except Exception as e:  # noqa: BLE001
            logger.warning("get_recent_emotions failed: %s", e)
            return []

    def get_recent_emotions_sync(self, n: int = 10) -> list[dict[str, Any]]:
        return _run_async(self.get_recent_emotions(n))  # type: ignore[no-any-return]

    # ── 通用 ──────────────────────────────────────────────

    async def _search(self, collection_name: str, query: str, top_k: int) -> list[dict[str, Any]]:
        coll = self._collections.get(collection_name)
        if coll is None:
            return []
        try:
            results = await asyncio.to_thread(coll.query, query_texts=[query], n_results=top_k)
            if not results or not results.get("documents"):
                return []
            items = []
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                items.append({
                    "content": doc,
                    "metadata": meta,
                    "distance": results["distances"][0][i] if results.get("distances") else 0,
                })
            return items
        except Exception as e:  # noqa: BLE001
            logger.warning("Search failed on %s: %s", collection_name, e)
            return []

    async def search(self, query: str, top_k: int = 5, filter_dict: dict | None = None) -> list[dict[str, Any]]:
        collection_map = {
            "episode": "episodic_memory",
            "fact": "user_facts",
        }
        if filter_dict and isinstance(filter_dict, dict):
            coll_name = collection_map.get(filter_dict.get("type", ""))
            if coll_name:
                return await self._search(coll_name, query, top_k)
        all_results: list[dict[str, Any]] = []
        for coll in self._collections.values():
            if coll is not None:
                try:
                    res = await asyncio.to_thread(coll.query, query_texts=[query], n_results=top_k)
                    if res and res.get("documents"):
                        for i, doc in enumerate(res["documents"][0]):
                            meta = res["metadatas"][0][i] if res.get("metadatas") else {}
                            all_results.append({
                                "content": doc,
                                "metadata": meta,
                                "distance": res["distances"][0][i] if res.get("distances") else 0,
                            })
                except Exception:  # noqa: BLE001
                    pass
        return all_results

    def search_sync(self, query: str, top_k: int = 5, filter_dict: dict | None = None) -> list[dict[str, Any]]:
        return _run_async(self.search(query, top_k, filter_dict))  # type: ignore[no-any-return]

    async def store_text(self, text: str, metadata: dict | None = None,
                   collection: str = "episodic_memory") -> str | None:
        coll = self._collections.get(collection)
        if coll is None:
            return None
        doc_id = f"text_{hashlib.md5(text.encode()).hexdigest()[:12]}"
        try:
            await asyncio.to_thread(coll.add, documents=[text], metadatas=[metadata or {}], ids=[doc_id])
            return doc_id
        except Exception as e:  # noqa: BLE001
            logger.warning("store_text failed: %s", e)
            return None

    def store_text_sync(self, text: str, metadata: dict | None = None,
                   collection: str = "episodic_memory") -> str | None:
        return _run_async(self.store_text(text, metadata, collection))  # type: ignore[no-any-return]

    def health_check(self) -> dict:
        return {
            "chromadb_available": HAS_CHROMADB,
            "collections": {
                name: coll is not None for name, coll in self._collections.items()
            },
            "path": self.chroma_path,
        }
