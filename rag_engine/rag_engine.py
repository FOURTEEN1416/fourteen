from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

logger = logging.getLogger("rag_engine")

_DEFAULT_QUERY_TIMEOUT = 5.0

# BM25Okapi 自动检测（rank_bm25 已安装则启用关键词打分）
try:
    from rank_bm25 import BM25Okapi  # noqa: F401
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False


def _tokenize(text: str) -> list[str]:
    """简单中文/英文分词：按非单词字符切分。"""
    return [t.lower() for t in re.findall(r"\w+", text) if len(t) > 1]


class BM25Index:
    """基于结构化记忆中事实的 BM25 索引，支持事件驱动刷新与 TTL 缓存。"""

    # 默认缓存有效期（秒），避免每次查询都重新加载事实
    DEFAULT_TTL_SECONDS = 60.0

    def __init__(self, structured_memory, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._sm = structured_memory
        self._ttl_seconds = ttl_seconds
        self._corpus: list[str] = []
        self._docs: list[dict[str, Any]] = []
        self._index: Any = None
        self._version: int = -1
        self._last_build_time: float = 0.0
        self._dirty: bool = True

    def _load_facts(self) -> list[dict[str, Any]]:
        if self._sm is None:
            return []
        try:
            return self._sm.get_facts(min_confidence=0.3, limit=500)
        except Exception as e:  # noqa: BLE001
            logger.debug("BM25 load facts failed: %s", e)
            return []

    def _build(self) -> None:
        facts = self._load_facts()
        self._corpus = []
        self._docs = []
        for f in facts:
            text = f.get("fact", "")
            if not text:
                continue
            self._corpus.append(text)
            self._docs.append(f)
        if self._corpus and HAS_BM25:
            tokenized = [_tokenize(c) for c in self._corpus]
            self._index = BM25Okapi(tokenized)
        else:
            self._index = None
        self._version = len(self._corpus)
        self._dirty = False
        self._last_build_time = time.monotonic()

    def mark_dirty(self) -> None:
        """事实变更时调用，触发下次查询重建索引。"""
        self._dirty = True

    def ensure_built(self) -> None:
        """延迟构建/刷新索引：首次、被标记 dirty 或缓存过期时重建。"""
        if self._index is None or self._dirty:
            self._build()
            return

        elapsed = time.monotonic() - self._last_build_time
        if elapsed > self._ttl_seconds:
            self._build()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        self.ensure_built()
        if not self._index or not query:
            return []
        try:
            tokenized_query = _tokenize(query)
            if not tokenized_query:
                return []
            scores = self._index.get_scores(tokenized_query)
            ranked = sorted(
                enumerate(scores),
                key=lambda x: x[1],
                reverse=True,
            )
            results = []
            for idx, score in ranked[:top_k]:
                if score <= 0:
                    continue
                doc = self._docs[idx]
                results.append({
                    "content": self._corpus[idx],
                    "source": "bm25",
                    "category": doc.get("category", ""),
                    "confidence": doc.get("confidence", 0.5),
                    "bm25_score": float(score),
                })
            return results
        except Exception as e:  # noqa: BLE001
            logger.debug("BM25 search failed: %s", e)
            return []


class KeywordRetriever:
    """关键词检索器：优先使用 BM25，不可用时回退到简单关键词匹配。"""

    def __init__(self, structured_memory, ttl_seconds: float = BM25Index.DEFAULT_TTL_SECONDS):
        self._sm = structured_memory
        self._bm25 = BM25Index(structured_memory, ttl_seconds=ttl_seconds)

    def mark_dirty(self) -> None:
        """事实变更时调用，触发 BM25 索引重建。"""
        self._bm25.mark_dirty()

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if HAS_BM25:
            results = self._bm25.search(query, top_k=top_k * 2)
            if results:
                return results[:top_k]

        # Fallback：简单关键词匹配
        keywords = re.findall(r"\w+", query)
        results = []
        for kw in keywords:
            facts = self._sm.search_facts(kw) if self._sm else []
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
    """RRF（Reciprocal Rank Fusion）重排序器。

    比简单加权更适合融合不同量纲的分数（向量距离 vs BM25 分数）。
    保留 vector_weight/keyword_weight/threshold 参数以兼容旧测试。
    """

    def __init__(
        self,
        k: float = 60.0,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ):
        self.k = k
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight

    def rerank(
        self,
        vector_results: list[dict],
        keyword_results: list[dict],
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[dict]:
        scores: dict[str, float] = {}
        metadata: dict[str, dict[str, Any]] = {}

        for rank, r in enumerate(vector_results):
            content = r.get("content", "")
            if not content:
                continue
            scores[content] = scores.get(content, 0.0) + 1.0 / (self.k + rank + 1)
            metadata[content] = {**metadata.get(content, {}), **r, "source": "fusion"}

        for rank, r in enumerate(keyword_results):
            content = r.get("content", "")
            if not content:
                continue
            scores[content] = scores.get(content, 0.0) + 1.0 / (self.k + rank + 1)
            metadata[content] = {**metadata.get(content, {}), **r, "source": "fusion"}

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for content, score in ranked[:top_k]:
            if score < threshold:
                continue
            results.append({
                "content": content,
                "score": score,
                **metadata.get(content, {}),
            })
        return results


class ContextBudgetMgr:
    def __init__(self, max_context_tokens: int = 4096, retrieval_ratio: float = 0.4):
        self.max_tokens = max_context_tokens
        self.retrieval_ratio = retrieval_ratio

    def truncate(self, items: list[dict], estimated_tokens_per_item: int = 100) -> list[dict]:
        budget = int(self.max_tokens * self.retrieval_ratio)
        max_items = budget // estimated_tokens_per_item
        if len(items) <= max_items:
            return items
        return items[:max_items]


class HallucinationGuard:
    def __init__(self, semantic_memory):
        self._sm = semantic_memory

    def check(self, reply: str) -> tuple[bool, str]:
        patterns = [
            re.compile(r"你说过(.+?)。"),
            re.compile(r"你喜欢(.+?)。"),
            re.compile(r"你不喜欢(.+?)。"),
        ]
        for pattern in patterns:
            match = pattern.search(reply)
            if match:
                claim = match.group(1)
                # search 返回格式取决于 semantic_memory 实现，兼容 list 和 dict
                try:
                    results = self._sm.search(claim, top_k=3)
                    if isinstance(results, list):
                        if results:
                            continue  # 有结果，声明有据可查
                        return False, claim
                    elif isinstance(results, dict):
                        if results.get("vector") or results.get("exact") or results.get("results"):
                            continue
                        return False, claim
                except Exception as e:  # noqa: BLE001
                    # semantic_memory 不可用或 search 接口异常，跳过检查
                    logger.debug("Hallucination guard search failed, skipping check: %s", e)
                    continue
        return True, ""


class RAGEngineV2:
    def __init__(self, vector_memory, structured_memory, semantic_memory=None,
                 tone_mimic=None, max_context_tokens: int = 4096,
                 query_timeout: float = _DEFAULT_QUERY_TIMEOUT,
                 bm25_ttl_seconds: float = BM25Index.DEFAULT_TTL_SECONDS):
        self._vm = vector_memory
        self._sm = structured_memory
        self._semantic = semantic_memory
        self._tone_mimic = tone_mimic
        self._query_timeout = query_timeout
        self._keyword_retriever = KeywordRetriever(structured_memory, ttl_seconds=bm25_ttl_seconds)
        self._reranker = Reranker()
        self._budget_mgr = ContextBudgetMgr(max_context_tokens)
        self._hallucination_guard = HallucinationGuard(semantic_memory) if semantic_memory else None

    def mark_dirty(self) -> None:
        """结构化记忆变更时调用，触发关键词索引刷新。"""
        self._keyword_retriever.mark_dirty()

    def retrieve(self, query: str, top_k: int = 5) -> dict[str, Any]:
        vector_results = []
        if self._vm is not None:
            try:
                vector_results = self._vm.search_chats_sync(query, top_k)
            except Exception as e:  # noqa: BLE001
                logger.warning("Vector search failed: %s", e)
        keyword_results = self._keyword_retriever.search(query, top_k)
        merged = self._reranker.rerank(vector_results, keyword_results, top_k=top_k * 2)
        final = self._budget_mgr.truncate(merged)
        style_examples = []
        if self._tone_mimic:
            try:
                style_examples = self._tone_mimic.retrieve_style_examples(query, top_k=3)
            except Exception as e:  # noqa: BLE001
                logger.debug("Style example retrieval failed: %s", e)
        return {
            "results": final,
            "style_examples": style_examples,
            "total_vector": len(vector_results),
            "total_keyword": len(keyword_results),
        }

    async def retrieve_async(self, query: str, top_k: int = 5) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.retrieve, query, top_k),
                timeout=self._query_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("RAG query timed out after %.1fs: %s", self._query_timeout, query[:100])
            return {"results": [], "style_examples": [], "total_vector": 0, "total_keyword": 0}

    def validate_reply(self, reply: str) -> tuple[bool, str]:
        if self._hallucination_guard:
            return self._hallucination_guard.check(reply)
        return True, ""

    def health_check(self) -> dict:
        return {"available": True, "bm25_available": HAS_BM25}
