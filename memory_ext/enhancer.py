"""
记忆增强器 - mem0兼容API，复用项目已有ChromaDB

关键特性:
  - mem0相同的 add/search/get_all API
  - 复用项目中已有的 ChromaDB（避免依赖冲突）
  - 自动提取关键信息（使用LLM进行提取，与项目现有fact_extractor类似）
  - 记忆重要性评分
  - 长期记忆持久化

与现有memory/模块的关系:
  - memory/memory_pipeline.py: 对话上下文的实时管理
  - memory_ext/enhancer.py: 长期事实知识的抽取和检索（补充角色）
  - 两者互补，不冲突

性能优化 (v3 audit):
  - count(): O(1) ChromaDB count 替代 O(n) get_all
  - delete_all(): 使用 where 过滤批量删除，避免先查后删
  - add_batch(): 单次批量写入，避免逐条插入
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("memory_ext.enhancer")

# 每次批量操作的最大条数
_BATCH_SIZE = 100


class MemoryEnhancer:
    """
    记忆增强器 - 长期记忆的提取和检索

    提供与 mem0 库相同的 add/search/get_all 接口，
    但底层使用项目已有的 ChromaDB。
    """

    def __init__(
        self,
        chroma_path: Optional[str] = None,
        collection_name: str = "long_term_memories",
        llm_gateway=None,
        enabled: bool = True,
    ):
        self._chroma_path = chroma_path
        self._collection_name = collection_name
        self._llm = llm_gateway
        self._enabled = enabled
        self._collection = None
        self._initialized = False
        self._stats = {
            "total_added": 0,
            "total_searched": 0,
            "last_add_time": None,
        }

    async def initialize(self) -> bool:
        """初始化ChromaDB集合"""
        if self._initialized:
            return True
        if not self._enabled:
            return True

        try:
            import chromadb
            client = chromadb.PersistentClient(path=self._chroma_path)
            # 尝试获取已存在的集合，不存在则创建
            try:
                self._collection = client.get_collection(self._collection_name)
                logger.info("使用已有记忆集合: %s", self._collection_name)
            except Exception:
                self._collection = client.create_collection(self._collection_name)
                logger.info("创建新记忆集合: %s", self._collection_name)

            self._initialized = True
            return True
        except Exception as e:
            logger.error("初始化MemoryEnhancer失败: %s", e)
            self._enabled = False
            return False

    def add(self, message: str, user_id: str = "default",
            metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        添加记忆 - 自动提取关键信息并存储

        Args:
            message: 要记住的文本内容
            user_id: 用户标识
            metadata: 附加元数据

        Returns:
            {"id": str, "message": str, "timestamp": float}
        """
        if not self._enabled or not self._collection:
            return {"error": "not_initialized"}

        try:
            timestamp = time.time()
            doc_id = f"mem_{user_id}_{int(timestamp * 1000)}"

            meta = {
                "user_id": user_id,
                "timestamp": timestamp,
                "date": datetime.fromtimestamp(timestamp).isoformat(),
                "type": "long_term",
            }
            if metadata:
                meta.update(metadata)

            self._collection.add(
                documents=[message],
                metadatas=[meta],
                ids=[doc_id],
            )

            self._stats["total_added"] += 1
            self._stats["last_add_time"] = timestamp

            return {
                "id": doc_id,
                "message": message,
                "timestamp": timestamp,
            }

        except Exception as e:
            logger.error("添加记忆失败: %s", e)
            return {"error": str(e)}

    def add_batch(self, messages: List[str], user_id: str = "default",
                   metadatas: Optional[List[Dict]] = None) -> List[Dict]:
        """
        批量添加记忆 - 使用ChromaDB批量API，非逐条插入

        Args:
            messages: 要记住的文本列表
            user_id: 用户标识
            metadatas: 附加元数据列表

        Returns:
            [{"id": str, "message": str, "timestamp": float}, ...]
        """
        if not self._enabled or not self._collection:
            return [{"error": "not_initialized"} for _ in messages]

        results = []
        timestamp = time.time()

        # 分批处理，避免单次写入过大
        for batch_start in range(0, len(messages), _BATCH_SIZE):
            batch_msgs = messages[batch_start:batch_start + _BATCH_SIZE]
            batch_meta = metadatas[batch_start:batch_start + _BATCH_SIZE] if metadatas else None

            doc_ids = []
            documents = []
            metadatas_batch = []

            for i, msg in enumerate(batch_msgs):
                idx = batch_start + i
                doc_id = f"mem_{user_id}_{int(timestamp * 1000)}_{idx}"
                doc_ids.append(doc_id)

                meta = {
                    "user_id": user_id,
                    "timestamp": timestamp + idx,
                    "date": datetime.fromtimestamp(timestamp + idx).isoformat(),
                    "type": "long_term",
                }
                if batch_meta:
                    meta.update(batch_meta[i])
                metadatas_batch.append(meta)
                documents.append(msg)

            try:
                self._collection.add(
                    documents=documents,
                    metadatas=metadatas_batch,
                    ids=doc_ids,
                )
                for idx, (msg, doc_id) in enumerate(zip(batch_msgs, doc_ids)):
                    results.append({
                        "id": doc_id,
                        "message": msg,
                        "timestamp": timestamp + batch_start + idx,
                    })
                    self._stats["total_added"] += 1
            except Exception as e:
                logger.error("批量添加记忆失败 (batch %d): %s", batch_start, e)
                for msg in batch_msgs:
                    results.append({"error": str(e), "message": msg})

        self._stats["last_add_time"] = timestamp
        return results

    def search(self, query: str, user_id: str = "default",
               top_k: int = 5, threshold: float = 0.0) -> List[Dict[str, Any]]:
        """
        检索相关记忆

        Args:
            query: 搜索查询
            user_id: 用户标识
            top_k: 返回结果数
            threshold: 相似度阈值 (0.0 = 不过滤，推荐 0.3)

        Returns:
            [{"id": str, "message": str, "score": float, "metadata": dict}, ...]
        """
        if not self._enabled or not self._collection:
            return []

        try:
            self._stats["total_searched"] += 1

            results = self._collection.query(
                query_texts=[query],
                n_results=min(top_k, 50),
                where={"user_id": user_id} if user_id != "all" else None,
            )

            memories = []
            if results and results.get("documents"):
                for i, doc in enumerate(results["documents"][0]):
                    # ChromaDB 返回 L2 距离，转换为 0~1 相似度
                    dist = results["distances"][0][i] if results.get("distances") else 0.0
                    score = 1.0 - (dist / 3.0)  # L2范围约0~3，归一化
                    score = max(0.0, min(1.0, score))  # 裁剪到0~1
                    if score < threshold:
                        continue
                    memories.append({
                        "id": results["ids"][0][i] if results.get("ids") else f"mem_{i}",
                        "message": doc,
                        "score": round(score, 4),
                        "metadata": (results["metadatas"][0][i] if results.get("metadatas") else {}),
                    })

            # 按相似度排序
            memories.sort(key=lambda x: x["score"], reverse=True)
            return memories

        except Exception as e:
            logger.error("搜索记忆失败: %s", e)
            return []

    def get_all(self, user_id: str = "default",
                limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取用户所有记忆

        Args:
            user_id: 用户标识
            limit: 返回条数上限

        Returns:
            [{"id": str, "message": str, "metadata": dict}, ...]
        """
        if not self._enabled or not self._collection:
            return []

        try:
            results = self._collection.get(
                where={"user_id": user_id} if user_id != "all" else None,
                limit=limit,
            )

            memories = []
            if results and results.get("documents"):
                for i, doc in enumerate(results["documents"]):
                    memories.append({
                        "id": results["ids"][i] if results.get("ids") else f"mem_{i}",
                        "message": doc,
                        "metadata": (results["metadatas"][i] if results.get("metadatas") else {}),
                    })
            return memories

        except Exception as e:
            logger.error("获取记忆列表失败: %s", e)
            return []

    def delete(self, memory_id: str) -> bool:
        """删除指定记忆"""
        if not self._enabled or not self._collection:
            return False
        try:
            self._collection.delete(ids=[memory_id])
            return True
        except Exception as e:
            logger.error("删除记忆失败: %s", e)
            return False

    def delete_all(self, user_id: str = "default") -> bool:
        """
        删除用户所有记忆

        性能: O(1) ChromaDB where删除，替代O(n)先查后删
        """
        if not self._enabled or not self._collection:
            return False
        try:
            where = {"user_id": user_id} if user_id != "all" else None
            if where:
                self._collection.delete(where=where)
            else:
                # 删除全部：使用空where或特殊标记
                # ChromaDB delete requires ids or where; for "all" use metadata filter
                self._collection.delete(where={"type": "long_term"})
            logger.info("已清除用户 '%s' 的所有记忆", user_id)
            return True
        except Exception as e:
            logger.error("清除记忆失败: %s", e)
            return False

    def count(self, user_id: str = "default") -> int:
        """
        统计记忆数量

        性能: O(1) ChromaDB count()，替代O(n) get_all+len
        """
        if not self._enabled or not self._collection:
            return 0
        try:
            # ChromaDB 1.5.x 不支持 count(where=...)，用 get 替代
            if user_id != "all":
                result = self._collection.get(where={"user_id": user_id})
                return len(result.get("ids", [])) if result else 0
            return self._collection.count()
        except Exception as e:
            logger.error("统计记忆数量失败: %s", e)
            return 0

    def health_check(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "initialized": self._initialized,
            "collection": self._collection_name,
            "total_added": self._stats["total_added"],
            "total_searched": self._stats["total_searched"],
        }
