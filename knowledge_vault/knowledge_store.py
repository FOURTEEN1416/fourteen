"""
知识存储 — ChromaDB + SQLite 存储知识
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("knowledge_store")


class KnowledgeStore:
    """
    知识存储

    使用：
    - SQLite: 结构化存储（时间/来源/类型/原文URL）
    - ChromaDB: 向量化存储（语义检索）
    """

    def __init__(self, db_path: str = "data/knowledge.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._chroma = None
        self._init_chroma()

    def _init_db(self) -> None:
        """初始化SQLite数据库"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    persona_name TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL,
                    original_url TEXT,
                    source_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_persona ON knowledge(persona_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON knowledge(created_at)
            """)
            conn.commit()

    def _init_chroma(self) -> None:
        """初始化ChromaDB"""
        try:
            import chromadb
            self._chroma = chromadb.PersistentClient(path=str(self.db_path.parent / "chroma"))  # type: ignore[assignment]
            self._collection = self._chroma.get_or_create_collection("knowledge")  # type: ignore[attr-defined]
            logger.info("ChromaDB initialized")
        except ImportError:
            logger.warning("ChromaDB not available, semantic search disabled")
            self._chroma = None

    def store(
        self,
        persona_name: str,
        content: str,
        title: str = "",
        original_url: str = "",
        source_type: str = "unknown",
        metadata: dict | None = None,
    ) -> int:
        """
        存储知识

        Returns:
            知识ID
        """
        # 存储到SQLite
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge (persona_name, title, content, original_url, source_type, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    persona_name,
                    title,
                    content,
                    original_url,
                    source_type,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            knowledge_id = cursor.lastrowid
            conn.commit()

        # 存储到ChromaDB
        if self._chroma:
            try:
                self._collection.add(
                    ids=[str(knowledge_id)],
                    documents=[content],
                    metadatas=[{
                        "persona_name": persona_name,
                        "title": title,
                        "source_type": source_type,
                    }],
                )
            except Exception as e:  # noqa: BLE001

                logger.warning("ChromaDB add failed: %s", e)

        logger.debug("Stored knowledge %d for %s", knowledge_id, persona_name)
        return knowledge_id  # type: ignore[return-value]

    def search(
        self,
        persona_name: str,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        搜索知识

        Args:
            persona_name: 人设名称
            query: 查询文本
            limit: 最大结果数

        Returns:
            匹配的知识列表
        """
        if self._chroma:
            try:
                results = self._collection.query(
                    query_texts=[query],
                    n_results=limit,
                    where={"persona_name": persona_name},
                )

                if results["ids"]:
                    return [
                        {
                            "id": int(id_),
                            "content": doc,
                            "metadata": meta,
                        }
                        for id_, doc, meta in zip(
                            results["ids"][0],
                            results["documents"][0],
                            results["metadatas"][0], strict=False,
                        )
                    ]  # noqa: BLE001

            except Exception as e:  # noqa: BLE001

                logger.warning("ChromaDB query failed: %s", e)

        # 降级到SQLite全文搜索
        return self._sqlite_search(persona_name, query, limit)

    def _sqlite_search(
        self,
        persona_name: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """SQLite搜索"""
        # 转义LIKE查询中的特殊字符
        escaped_query = query.replace("%", "\\%").replace("_", "\\_")

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, title, content, original_url, source_type, created_at
                FROM knowledge
                WHERE persona_name = ? AND content LIKE ? ESCAPE '\\'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (persona_name, f"%{escaped_query}%", limit),
            )

            return [dict(row) for row in cursor.fetchall()]

    def get_recent(
        self,
        persona_name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """获取最近的知识"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, title, content, original_url, source_type, created_at
                FROM knowledge
                WHERE persona_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (persona_name, limit),
            )

            return [dict(row) for row in cursor.fetchall()]
