"""
冲突检测器 — 基于向量相似度的事实冲突检测

检测新提取的事实是否与已有事实冲突/矛盾。
"""

from __future__ import annotations

import logging
from typing import Any

from ._legacy_semantic_memory import SemanticMemory

logger = logging.getLogger("conflict_detector")


class ConflictDetector:
    """基于向量相似度的事实冲突检测"""

    def __init__(self, semantic_memory: SemanticMemory,
                 similarity_threshold: float = 0.3):
        self._sem = semantic_memory
        self._threshold = similarity_threshold

    def check_conflict(self, new_fact: str, category: str) -> dict[str, Any] | None:
        try:
            search_results = self._sem.search(new_fact, top_k=3)
            vector_results = search_results.get("vector", [])
            for result in vector_results:
                existing = result.get("content", "")
                distance = result.get("distance", 1.0)
                if distance < self._threshold and existing != new_fact:
                    return {
                        "new_fact": new_fact,
                        "existing_fact": existing,
                        "similarity": 1.0 - distance,
                        "category": category,
                        "status": "pending",
                    }
        except Exception as e:  # noqa: BLE001
            logger.debug("Conflict detection failed: %s", e)
        return None
