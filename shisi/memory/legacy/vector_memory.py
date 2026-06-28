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
import contextlib
import hashlib
import logging
import os
import threading
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

# 进程级缓存：避免重复初始化 embedding function 和 ChromaDB collection
_silence_once: bool = False
_silence_lock = threading.Lock()
_embedding_function: Any | None = None
_embedding_lock = threading.Lock()


@contextlib.contextmanager
def _silence_stdout():
    """屏蔽 onnxruntime C++ 扩展 import 时的 EP Error 噪声；首次调用执行 fd 重定向，后续直接放行。"""
    global _silence_once
    if _silence_once:
        yield
        return
    with _silence_lock:
        if _silence_once:
            yield
            return
        _silence_once = True
    with _do_silence_stdout():
        yield


@contextlib.contextmanager
def _do_silence_stdout():
    """屏蔽 onnxruntime C++ 扩展 import 时的 EP Error 噪声（缺 TensorRT 库）。
    onnxruntime C++ 通过 std::cerr (fd 2) 打印 EP Error，所以必须同时重定向 fd 1+2。
    """
    devnull_path = "nul" if os.name == "nt" else os.devnull
    import sys
    sys.stdout.flush()
    sys.stderr.flush()
    # 重定向 fd 1 (stdout) 和 fd 2 (stderr) — onnxruntime C++ 用 std::cerr
    saved_fd1 = os.dup(1)
    saved_fd2 = os.dup(2)
    d1 = os.open(devnull_path, os.O_WRONLY)
    d2 = os.open(devnull_path, os.O_WRONLY)
    os.dup2(d1, 1)
    os.dup2(d2, 2)
    os.close(d1)
    os.close(d2)
    saved_stdout_file = sys.stdout
    saved_stderr_file = sys.stderr
    try:
        with contextlib.ExitStack() as stack:
            devnull_out = stack.enter_context(open(devnull_path, "w"))
            devnull_err = stack.enter_context(open(devnull_path, "w"))
            sys.stdout = devnull_out
            sys.stderr = devnull_err
            yield
    finally:
        sys.stdout = saved_stdout_file
        sys.stderr = saved_stderr_file
        if saved_fd1 is not None:
            os.dup2(saved_fd1, 1)
            os.close(saved_fd1)
        if saved_fd2 is not None:
            os.dup2(saved_fd2, 2)
            os.close(saved_fd2)


def _run_async(coro):
    """在同步上下文中运行 coroutine；若已处于事件循环中则复用该循环。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已处于事件循环中：提交到同一线程的事件循环，避免创建额外线程/事件循环
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def _get_default_embedding_function() -> Any:
    """进程级单例：避免每次初始化 VectorMemory 都重新加载 onnxruntime 模型。"""
    global _embedding_function
    if _embedding_function is not None:
        return _embedding_function
    with _embedding_lock:
        if _embedding_function is not None:
            return _embedding_function
        with _silence_stdout():
            _embedding_function = embedding_functions.DefaultEmbeddingFunction()
        return _embedding_function


class VectorMemory:
    """
    ChromaDB 向量记忆封装 (async + sync)

    所有公开方法同时提供 async 和 sync 版本：
    - async: store_chat(), search() 等 — 供 async 上下文
    - sync: store_chat_sync(), search_sync() 等 — 供同步上下文

    同一路径的 VectorMemory 实例会被进程级缓存复用，避免重复初始化 collection。
    """

    COLLECTIONS = ["chat_history", "user_facts", "emotion_logs", "episodic_memory", "semantic_knowledge", "emotion_trajectory"]

    _instances: dict[str, VectorMemory] = {}
    _instance_lock = threading.Lock()

    def __new__(cls, chroma_path: str = "./data/chroma_db") -> VectorMemory:
        abs_path = os.path.abspath(chroma_path)
        if abs_path in cls._instances:
            return cls._instances[abs_path]
        with cls._instance_lock:
            if abs_path in cls._instances:
                return cls._instances[abs_path]
            instance = super().__new__(cls)
            cls._instances[abs_path] = instance
            return instance

    def __init__(self, chroma_path: str = "./data/chroma_db"):
        # 单例复用时跳过重复初始化
        if getattr(self, "_initialized", False):
            return
        self.chroma_path = os.path.abspath(chroma_path)
        self._collections: dict[str, Any] = {
            name: None for name in self.COLLECTIONS
        }

        if HAS_CHROMADB:
            self._init()
        else:
            logger.warning("chromadb not installed, VectorMemory runs in fallback mode")
        self._initialized = True

    def _init(self) -> None:
        try:
            os.makedirs(self.chroma_path, exist_ok=True)
            client = chromadb.PersistentClient(path=self.chroma_path)
            ef = _get_default_embedding_function()
            for name in self.COLLECTIONS:
                try:
                    self._collections[name] = client.get_or_create_collection(
                        name=name,
                        embedding_function=ef,  # type: ignore
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to init collection '%s': %s", name, e)

            logger.info("VectorMemory ready: %s", self.chroma_path)
        except BaseException as e:  # noqa: BLE001
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
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
                except Exception as e:  # noqa: BLE001
                    logger.warning("向量搜索结果解析失败，跳过该批次: %s", e)
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
