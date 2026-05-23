"""
向量记忆系统 — 基于 ChromaDB

管理三种向量记忆：
1. chat_history: 聊天历史（用于语义检索）
2. user_facts: 用户事实知识
3. emotion_logs: 情绪变化日志
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vector_memory")

try:
    import chromadb

    # 使用最稳定的导入路径; chromadb v0.4+ 兼容
    from chromadb.api.models.Collection import Collection  # type: ignore
    from chromadb.utils import embedding_functions
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False
    Collection = Any  # type: ignore


class VectorMemory:
    """
    ChromaDB 向量记忆封装

    三种 Collection：
    - chat_history: 对话记录
    - user_facts: 用户事实
    - emotion_logs: 情绪日志
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
        """初始化 ChromaDB 连接和 collections"""
        try:
            os.makedirs(self.chroma_path, exist_ok=True)
            client = chromadb.PersistentClient(path=self.chroma_path)  # type: ignore
            ef = embedding_functions.DefaultEmbeddingFunction()  # type: ignore

            for name in self.COLLECTIONS:
                try:
                    self._collections[name] = client.get_or_create_collection(
                        name=name,
                        embedding_function=ef,  # type: ignore
                    )
                except Exception as e:
                    logger.warning("Failed to init collection '%s': %s", name, e)

            logger.info("VectorMemory ready: %s", self.chroma_path)
        except Exception as e:
            logger.error("ChromaDB init failed: %s", e)

    # ── 聊天历史 ──────────────────────────────────────────

    def store_chat(self, user_msg: str, reply: str, metadata: Optional[dict] = None) -> Optional[str]:
        """存储一轮对话"""
        coll = self._collections.get("chat_history")
        if coll is None:
            return None

        doc = f"User: {user_msg}\nAssistant: {reply}"
        meta = {
            "timestamp": datetime.now().isoformat(),
            "type": "chat",
            "user_msg_len": len(user_msg),
            "reply_len": len(reply),
        }
        if metadata:
            meta.update(metadata)

        doc_id = f"chat_{hashlib.md5(doc.encode()).hexdigest()[:12]}"

        try:
            coll.add(documents=[doc], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:
            logger.warning("store_chat failed: %s", e)
            return None

    def search_chats(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """语义搜索聊天历史"""
        return self._search("chat_history", query, top_k)

    # ── 用户事实 ──────────────────────────────────────────

    def store_fact(self, fact: str, category: str = "general", confidence: float = 0.5) -> Optional[str]:
        """存储用户事实"""
        coll = self._collections.get("user_facts")
        if coll is None:
            return None

        doc_id = f"fact_{hashlib.md5(fact.encode()).hexdigest()[:12]}"

        meta = {
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "confidence": confidence,
        }

        try:
            coll.add(documents=[fact], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:
            logger.warning("store_fact failed: %s", e)
            return None

    def add_batch(self, documents: List[str], metadatas: List[Dict[str, Any]],
                  ids: List[str], collection: str = "user_facts") -> bool:
        """批量添加文档"""
        if len(documents) != len(metadatas) or len(documents) != len(ids):
            logger.error("add_batch: documents/metadatas/ids length mismatch")
            return False
        coll = self._collections.get(collection)
        if coll is None:
            return False
        try:
            coll.add(documents=documents, metadatas=metadatas, ids=ids)
            return True
        except Exception as e:
            logger.warning("add_batch failed: %s", e)
            return False

    def search_facts(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """语义搜索用户事实"""
        return self._search("user_facts", query, top_k)

    def get_all_facts(self) -> List[str]:
        """获取所有事实"""
        coll = self._collections.get("user_facts")
        if coll is None:
            return []
        try:
            results = coll.get()
            return results.get("documents", [])
        except Exception as e:
            logger.warning("get_all_facts failed: %s", e)
            return []

    # ── 情绪日志 ──────────────────────────────────────────

    def store_emotion_log(self, emotion: str, intensity: float, trigger: str = "") -> Optional[str]:
        """记录情绪变化"""
        coll = self._collections.get("emotion_logs")
        if coll is None:
            return None

        doc = f"情感: {emotion}, 强度: {intensity:.2f}, 触发: {trigger}"
        meta = {
            "timestamp": datetime.now().isoformat(),
            "emotion": emotion,
            "intensity": intensity,
            "trigger": trigger,
        }

        try:
            doc_id = f"emotion_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            coll.add(documents=[doc], metadatas=[meta], ids=[doc_id])
            return doc_id
        except Exception as e:
            logger.warning("store_emotion_log failed: %s", e)
            return None

    def get_recent_emotions(self, n: int = 10) -> List[Dict[str, Any]]:
        """获取最近 N 条情绪记录"""
        coll = self._collections.get("emotion_logs")
        if coll is None:
            return []

        try:
            results = coll.get(limit=n)
            if not results or not results.get("metadatas"):
                return []
            items = []
            for meta, doc in zip(results["metadatas"], results["documents"]):
                items.append({"metadata": meta, "content": doc})
            return items
        except Exception as e:
            logger.warning("get_recent_emotions failed: %s", e)
            return []

    # ── 通用 ──────────────────────────────────────────────

    def _search(self, collection_name: str, query: str, top_k: int) -> List[Dict[str, Any]]:
        """通用语义搜索"""
        coll = self._collections.get(collection_name)
        if coll is None:
            return []

        try:
            results = coll.query(query_texts=[query], n_results=top_k)
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
        except Exception as e:
            logger.warning("Search failed on %s: %s", collection_name, e)
            return []

    def search(self, query: str, top_k: int = 5, filter_dict: Optional[dict] = None) -> List[Dict[str, Any]]:
        """通用语义搜索 (memory_pipeline 调用入口)

        Args:
            query: 搜索文本
            top_k: 返回条数
            filter_dict: 过滤器，type -> collection_name 映射:
                "episode" → "episodic_memory"
                "fact"    → "user_facts"
        """
        collection_map = {
            "episode": "episodic_memory",
            "fact": "user_facts",
        }
        if filter_dict and isinstance(filter_dict, dict):
            coll_name = collection_map.get(filter_dict.get("type", ""))
            if coll_name:
                return self._search(coll_name, query, top_k)
        # 默认: 搜所有 collection, 合并结果
        all_results = []
        for coll in self._collections.values():
            if coll is not None:
                try:
                    res = coll.query(query_texts=[query], n_results=top_k)
                    if res and res.get("documents"):
                        for i, doc in enumerate(res["documents"][0]):
                            meta = res["metadatas"][0][i] if res.get("metadatas") else {}
                            all_results.append({
                                "content": doc,
                                "metadata": meta,
                                "distance": res["distances"][0][i] if res.get("distances") else 0,
                            })
                except Exception:
                    pass
        return all_results

    def store_text(self, text: str, metadata: Optional[dict] = None,
                   collection: str = "episodic_memory") -> Optional[str]:
        """存储任意文本到指定 collection (memory_pipeline EpisodicMemory 调用)"""
        coll = self._collections.get(collection)
        if coll is None:
            return None
        doc_id = f"text_{hashlib.md5(text.encode()).hexdigest()[:12]}"
        try:
            coll.add(documents=[text], metadatas=[metadata or {}], ids=[doc_id])
            return doc_id
        except Exception as e:
            logger.warning("store_text failed: %s", e)
            return None

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "chromadb_available": HAS_CHROMADB,
            "collections": {
                name: coll is not None for name, coll in self._collections.items()
            },
            "path": self.chroma_path,
        }
