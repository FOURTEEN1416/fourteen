from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("rag_engine_v2")

try:
    from rank_bm25 import BM25Okapi  # noqa: F401
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False


class KeywordRetriever:
    def __init__(self, structured_memory):
        self._sm = structured_memory

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        keywords = re.findall(r'\w+', query)
        results = []
        for kw in keywords:
            facts = self._sm.search_facts(kw)
            for fact in facts:
                results.append({
                    "content": fact.get("fact", ""),
                    "source": "keyword",
                    "keyword": kw,
                    "category": fact.get("category", ""),
                    "confidence": fact.get("confidence", 0),
                })
        seen = set()
        unique = []
        for r in results:
            key = r["content"]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique[:top_k]


class Reranker:
    def __init__(self, vector_weight: float = 0.7, keyword_weight: float = 0.3):
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

    def rerank(self, vector_results: List[Dict], keyword_results: List[Dict],
               threshold: float = 0.3) -> List[Dict]:
        scored = {}
        for r in vector_results:
            content = r.get("content", "")
            distance = r.get("distance", 1.0)
            similarity = 1.0 - distance
            scored[content] = scored.get(content, 0) + similarity * self.vector_weight
        for r in keyword_results:
            content = r.get("content", "")
            conf = r.get("confidence", 0.5)
            scored[content] = scored.get(content, 0) + conf * self.keyword_weight
        results = [
            {"content": content, "score": score}
            for content, score in sorted(scored.items(), key=lambda x: x[1], reverse=True)
            if score >= threshold
        ]
        return results


class ContextBudgetMgr:
    def __init__(self, max_context_tokens: int = 4096, retrieval_ratio: float = 0.4):
        self.max_tokens = max_context_tokens
        self.retrieval_ratio = retrieval_ratio

    def truncate(self, items: List[Dict], estimated_tokens_per_item: int = 100) -> List[Dict]:
        budget = int(self.max_tokens * self.retrieval_ratio)
        max_items = budget // estimated_tokens_per_item
        if len(items) <= max_items:
            return items
        return items[:max_items]


class HallucinationGuard:
    def __init__(self, semantic_memory):
        self._sm = semantic_memory

    def check(self, reply: str) -> Tuple[bool, str]:
        patterns = [
            re.compile(r"你说过(.+?)。"),
            re.compile(r"你喜欢(.+?)。"),
            re.compile(r"你不喜欢(.+?)。"),
        ]
        for pattern in patterns:
            match = pattern.search(reply)
            if match:
                claim = match.group(1)
                results = self._sm.search(claim, top_k=3)
                if not results.get("vector") and not results.get("exact"):
                    return False, claim
        return True, ""


class RAGEngineV2:
    def __init__(self, vector_memory, structured_memory, semantic_memory=None,
                 tone_mimic=None, max_context_tokens: int = 4096):
        self._vm = vector_memory
        self._sm = structured_memory
        self._semantic = semantic_memory
        self._tone_mimic = tone_mimic
        self._keyword_retriever = KeywordRetriever(structured_memory)
        self._reranker = Reranker()
        self._budget_mgr = ContextBudgetMgr(max_context_tokens)
        self._hallucination_guard = HallucinationGuard(semantic_memory) if semantic_memory else None

    def retrieve(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        vector_results = self._vm.search_chats_sync(query, top_k)
        keyword_results = self._keyword_retriever.search(query, top_k)
        merged = self._reranker.rerank(vector_results, keyword_results)
        final = self._budget_mgr.truncate(merged)
        style_examples = []
        if self._tone_mimic:
            try:
                style_examples = self._tone_mimic.retrieve_style_examples(query, top_k=3)
            except Exception:
                pass
        return {
            "results": final,
            "style_examples": style_examples,
            "total_vector": len(vector_results),
            "total_keyword": len(keyword_results),
        }

    def validate_reply(self, reply: str) -> Tuple[bool, str]:
        if self._hallucination_guard:
            return self._hallucination_guard.check(reply)
        return True, ""

    def health_check(self) -> dict:
        return {"available": True, "bm25_available": HAS_BM25}
