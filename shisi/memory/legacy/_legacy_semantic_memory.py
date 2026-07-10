"""
语义记忆 — 提取的事实和知识（memory_pipeline 内联版本）

去重 + 冲突检测 + 置信度管理，基于向量+结构化双重存储。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger("semantic_memory")


class SemanticMemory:
    """语义记忆 — 提取的事实和知识（去重 + 冲突检测 + 置信度管理）"""

    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory
        self._fact_cache: set[int] = set()

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "") -> bool:
        # 使用 hashlib.md5 替代 hash()，确保进程间一致性
        fact_hash = hashlib.md5(fact.encode()).hexdigest()
        if fact_hash in self._fact_cache:
            return False
        try:
            similar = self._sm.search_facts(fact)
            if similar and len(similar) > 0:
                first = similar[0]
                if isinstance(first, dict) and first.get("similarity", 0) > 0.9:
                    logger.debug("Similar fact exists, skipping: %s...", fact[:30])
                    return False
        except Exception as e:  # noqa: BLE001
            logger.debug("Similar fact search failed, skipping dedup: %s", e)
        try:
            self._sm.add_fact(fact, category, confidence, source)
            self._fact_cache.add(fact_hash)
            try:
                self._vm.store_text_sync(fact, {
                    "type": "fact",
                    "category": category,
                    "confidence": confidence,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to store fact vector: %s", e)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to add fact: %s", e)
            return False

    def search(self, query: str, top_k: int = 5) -> dict[str, list]:
        results: dict[str, list] = {"vector": [], "structured": []}
        try:
            vector_results = self._vm.search_sync(query, top_k=top_k,
                                             filter_dict={"type": "fact"})
            results["vector"] = vector_results
            structured_results = self._sm.search_facts(query)
            results["structured"] = structured_results
        except Exception as e:  # noqa: BLE001
            logger.warning("Fact search failed: %s", e)
        return results

    def extract_facts_from_message(self, message: str) -> list[dict]:
        facts = []
        patterns = [
            (r"我喜欢(.+)", "preference"),
            (r"我讨厌(.+)", "dislike"),
            (r"我是(.+)", "identity"),
            (r"我在(.+)(工作|上学)", "occupation"),
            (r"我的(.+)是(.+)", "attribute"),
        ]
        for pattern, category in patterns:
            matches = re.findall(pattern, message)
            for match in matches:
                fact_text = match if isinstance(match, str) else match[-1]
                facts.append({
                    "fact": fact_text.strip(),
                    "category": category,
                    "confidence": 0.6,
                })
        return facts

    def get_facts(self, category: str | None = None,
                  limit: int = 50) -> list[dict]:
        """获取事实列表，按分类过滤"""
        try:
            raw = self._sm.get_facts(category, limit=limit)
            return [
                {
                    "id": r.get("id", 0),
                    "fact": r.get("fact", r.get("content", "")),
                    "category": r.get("category", category or "general"),
                    "confidence": r.get("confidence", 0.5),
                    "source": r.get("source", ""),
                    "created_at": str(r.get("created_at", "")),
                }
                for r in (raw or [])
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("get_facts failed: %s", e)
            return []
