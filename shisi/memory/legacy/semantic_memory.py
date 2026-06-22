from __future__ import annotations

import logging

logger = logging.getLogger("semantic_memory")


class SemanticMemory:
    def __init__(self, vector_memory, structured_memory):
        self._vm = vector_memory
        self._sm = structured_memory

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "",
                 importance: float = 0.5) -> int:
        fact_id = self._sm.add_fact(fact, category, confidence, source)
        self._vm.store_fact(fact, category, confidence)
        coll = self._vm._collections.get("semantic_knowledge")
        if coll:
            try:
                import hashlib
                doc_id = f"sk_{hashlib.md5(fact.encode()).hexdigest()[:12]}"
                meta = {
                    "category": category,
                    "confidence": confidence,
                    "importance": importance,
                }
                coll.add(documents=[fact], metadatas=[meta], ids=[doc_id])
            except Exception as e:  # noqa: BLE001
                logger.debug("semantic_knowledge store failed: %s", e)
        return fact_id  # type: ignore[no-any-return]

    def search(self, query: str, top_k: int = 5) -> dict[str, list[dict]]:
        results: dict[str, list[dict]] = {"vector": [], "exact": []}
        vector_result = self._vm._search("semantic_knowledge", query, top_k)
        results["vector"] = vector_result if isinstance(vector_result, list) else []
        results["exact"] = self._sm.search_facts(query)
        return results

    def get_facts(self, category: str | None = None,
                  min_confidence: float = 0.0, limit: int = 50) -> list[dict]:
        result = self._sm.get_facts(category, min_confidence, limit)
        return result if isinstance(result, list) else []

    def update_confidence(self, fact_id: int, confidence: float):
        self._sm.update_fact_confidence(fact_id, confidence)

    def delete_fact(self, fact_id: int):
        self._sm.delete_fact(fact_id)
