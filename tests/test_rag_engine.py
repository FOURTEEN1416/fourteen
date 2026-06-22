# TODO: shisi 后等效迁移
#
# 符号迁移表（shisi 暂无与原根 rag_engine/ 模块 API 兼容的等价实现，保留原 import）：
#   - rag_engine.rag_engine.RAGEngineV2     → shisi 无等价类（RAGEngineV2 是 RAG 编排器，shisi.knowledge/ 仅提供 CharacterKnowledgeService 负责索引/检索，缺少 engine 级别抽象）
#   - rag_engine.rag_engine.KeywordRetriever → shisi.knowledge.retriever.KeywordRetriever（shisi 版本 API 不同：构造无参；search 返回 RetrievalResult 而非 list[dict]；测试断言无法兼容）
#   - rag_engine.rag_engine.Reranker        → shisi 无等价类（RRF 融合逻辑未迁出）
#   - rag_engine.rag_engine.ContextBudgetMgr → shisi 无等价类
#   - rag_engine.rag_engine.HallucinationGuard → shisi 无等价类
#   - rag_engine.rag_engine.BM25Index       → shisi.knowledge.retriever.BM25Retriever（API 不同：构造仅 k1/b；search 返回 RetrievalResult；缺 _build/_load_facts/_corpus/_docs/_index/_dirty/_last_build_time 等内部属性）
#   - rag_engine.rag_engine.HAS_BM25        → shisi 无等价导出
# 原因：shisi.knowledge/ 当前仅含 KeywordRetriever / BM25Retriever / CharacterKnowledgeService，
#       其 API 与 rag_engine/ 内部类不同（构造签名、search 返回结构、内部状态字段均不兼容），
#       强行替换会破坏测试断言。等待后续 shisi 提供 adapter 或重新组织 rag_engine 模块后再切换。
"""单元测试: RAG引擎 — 深度版"""
import sys

import pytest

# 根目录 rag_engine/ 已迁移到 shisi.knowledge.legacy/；根目录物理删除后本文件可继续运行。
pytest.importorskip("shisi.knowledge.legacy", reason="根目录 rag_engine/ 已迁移到 shisi/")

sys.path.insert(0, ".")


# ═══════════════════════════════════════════════════════════════
#  RAGEngineV2 方法和属性验证
# ═══════════════════════════════════════════════════════════════

def test_rag_engine_import():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    assert RAGEngineV2 is not None


def test_rag_engine_has_retrieve():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    assert hasattr(RAGEngineV2, "retrieve")
    assert callable(RAGEngineV2.retrieve)


def test_rag_engine_has_validate_reply():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    assert hasattr(RAGEngineV2, "validate_reply")
    assert callable(RAGEngineV2.validate_reply)


def test_rag_engine_has_health_check():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    assert hasattr(RAGEngineV2, "health_check")
    assert callable(RAGEngineV2.health_check)


def test_rag_engine_init_attributes():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    assert hasattr(engine, "_keyword_retriever")
    assert hasattr(engine, "_reranker")
    assert hasattr(engine, "_budget_mgr")
    assert engine._hallucination_guard is None


def test_rag_engine_init_with_semantic():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    class MockSemantic:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    engine = RAGEngineV2(MockVM(), MockSM(), semantic_memory=MockSemantic())
    assert engine._hallucination_guard is not None


def test_rag_engine_health_check():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    health = engine.health_check()
    assert health["available"] is True
    assert "bm25_available" in health


def test_rag_engine_validate_reply_no_guard():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    ok, claim = engine.validate_reply("普通回复")
    assert ok is True
    assert claim == ""


def test_rag_engine_retrieve_result_keys():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    result = engine.retrieve("测试查询")
    assert "results" in result
    assert "style_examples" in result
    assert "total_vector" in result
    assert "total_keyword" in result


def test_rag_engine_retrieve_top_k():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    result = engine.retrieve("测试", top_k=3)
    assert isinstance(result["results"], list)


# ═══════════════════════════════════════════════════════════════
#  KeywordRetriever 验证
# ═══════════════════════════════════════════════════════════════

def test_keyword_retriever_import():
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever
    assert KeywordRetriever is not None


def test_keyword_retriever_search():
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever
    class MockSM:
        def search_facts(self, kw):
            if kw == "cat":
                return [{"fact": "用户喜欢猫", "category": "preference", "confidence": 0.8}]
            return []
    kr = KeywordRetriever(MockSM())
    results = kr.search("I like cat")
    assert len(results) >= 1
    assert results[0]["source"] == "keyword"


def test_keyword_retriever_top_k_limit():
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever
    class MockSM:
        def search_facts(self, kw):
            return [{"fact": f"fact_{i}", "category": "g", "confidence": 0.5} for i in range(10)]
    kr = KeywordRetriever(MockSM())
    results = kr.search("测试", top_k=2)
    assert len(results) <= 2


def test_keyword_retriever_dedup():
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever
    class MockSM:
        def search_facts(self, kw):
            return [{"fact": "相同内容", "category": "g", "confidence": 0.5}]
    kr = KeywordRetriever(MockSM())
    results = kr.search("a b")
    contents = [r["content"] for r in results]
    assert len(contents) == len(set(contents))


# ═══════════════════════════════════════════════════════════════
#  Reranker 验证
# ═══════════════════════════════════════════════════════════════

def test_reranker_import():
    from shisi.knowledge.legacy.rag_engine import Reranker
    assert Reranker is not None


def test_reranker_default_weights():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    assert rr.vector_weight == 0.7
    assert rr.keyword_weight == 0.3


def test_reranker_custom_weights():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker(vector_weight=0.5, keyword_weight=0.5)
    assert rr.vector_weight == 0.5
    assert rr.keyword_weight == 0.5


def test_reranker_rerank_empty():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    results = rr.rerank([], [])
    assert results == []


def test_reranker_rerank_vector_only():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    vec = [{"content": "a", "distance": 0.2}]
    results = rr.rerank(vec, [])
    assert len(results) == 1
    assert results[0]["content"] == "a"
    assert results[0]["score"] > 0


def test_reranker_rerank_threshold():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    vec = [{"content": "a", "distance": 0.99}]
    results = rr.rerank(vec, [], threshold=0.9)
    assert len(results) == 0


def test_reranker_rerank_mixed():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    vec = [{"content": "a", "distance": 0.3}]
    kw = [{"content": "a", "confidence": 0.8}]
    results = rr.rerank(vec, kw)
    assert len(results) == 1
    assert results[0]["score"] > 0


# ═══════════════════════════════════════════════════════════════
#  ContextBudgetMgr 验证
# ═══════════════════════════════════════════════════════════════

def test_budget_mgr_import():
    from shisi.knowledge.legacy.rag_engine import ContextBudgetMgr
    assert ContextBudgetMgr is not None


def test_budget_mgr_defaults():
    from shisi.knowledge.legacy.rag_engine import ContextBudgetMgr
    bm = ContextBudgetMgr()
    assert bm.max_tokens == 4096
    assert bm.retrieval_ratio == 0.4


def test_budget_mgr_truncate_under_budget():
    from shisi.knowledge.legacy.rag_engine import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=4096, retrieval_ratio=0.4)
    items = [{"content": f"item_{i}"} for i in range(5)]
    result = bm.truncate(items, estimated_tokens_per_item=100)
    assert len(result) == 5


def test_budget_mgr_truncate_over_budget():
    from shisi.knowledge.legacy.rag_engine import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=400, retrieval_ratio=0.5)
    items = [{"content": f"item_{i}"} for i in range(100)]
    result = bm.truncate(items, estimated_tokens_per_item=100)
    assert len(result) < 100
    assert len(result) == 2


def test_budget_mgr_custom_params():
    from shisi.knowledge.legacy.rag_engine import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=8192, retrieval_ratio=0.6)
    assert bm.max_tokens == 8192
    assert bm.retrieval_ratio == 0.6


# ═══════════════════════════════════════════════════════════════
#  HallucinationGuard 验证
# ═══════════════════════════════════════════════════════════════

def test_hallucination_guard_import():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    assert HallucinationGuard is not None


def test_hallucination_guard_safe_reply():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("今天天气真好")
    assert ok is True
    assert claim == ""


def test_hallucination_guard_unverifiable_claim():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is False
    assert claim == "猫"


def test_hallucination_guard_verifiable_claim():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5):
            return {"vector": [{"content": "喜欢猫"}], "exact": [{"content": "喜欢猫"}]}
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is True


# ═══════════════════════════════════════════════════════════════
#  HAS_BM25 标志验证
# ═══════════════════════════════════════════════════════════════

def test_has_bm25_flag():
    from shisi.knowledge.legacy.rag_engine import HAS_BM25
    assert isinstance(HAS_BM25, bool)


# ═══════════════════════════════════════════════════════════════
#  BM25Index 缓存与 dirty 标记验证
# ═══════════════════════════════════════════════════════════════

def test_bm25_index_caches_facts_and_respects_dirty():
    from shisi.knowledge.legacy.rag_engine import BM25Index

    class FakeSM:
        def __init__(self):
            self.facts = [
                {"fact": "I love cats", "category": "preference", "confidence": 0.9},
                {"fact": "I love dogs", "category": "preference", "confidence": 0.8},
                {"fact": "I love birds", "category": "preference", "confidence": 0.7},
            ]
            self.call_count = 0

        def get_facts(self, min_confidence, limit):
            self.call_count += 1
            return self.facts

    sm = FakeSM()
    idx = BM25Index(sm, ttl_seconds=60.0)

    # 首次 search 触发加载
    results1 = idx.search("cats")
    assert sm.call_count == 1
    assert len(results1) > 0

    # 60s 内再次 search 应使用缓存，不重新加载
    results2 = idx.search("cats")
    assert sm.call_count == 1  # 未增加
    assert len(results2) == len(results1)

    # 标记 dirty 后再次 search 应重新加载
    idx.mark_dirty()
    sm.facts.append({"fact": "I also love rabbits", "category": "preference", "confidence": 0.8})
    results3 = idx.search("rabbits")
    assert sm.call_count == 2
    assert any("rabbits" in r["content"] for r in results3)


def test_bm25_index_ttl_rebuilds_index():
    from shisi.knowledge.legacy.rag_engine import BM25Index

    class FakeSM:
        def __init__(self):
            self.call_count = 0

        def get_facts(self, min_confidence, limit):
            self.call_count += 1
            return [
                {"fact": "time test fact one", "category": "general", "confidence": 0.5},
                {"fact": "time test fact two", "category": "general", "confidence": 0.5},
                {"fact": "time test fact three", "category": "general", "confidence": 0.5},
            ]

    sm = FakeSM()
    idx = BM25Index(sm, ttl_seconds=-1.0)
    idx.search("time")
    assert sm.call_count == 1
    idx.search("time")
    assert sm.call_count == 2  # TTL<0 视为立即过期


def test_rag_engine_mark_dirty_propagates():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2

    class MockVM:
        def search_chats_sync(self, q, k):
            return []

    class MockSM:
        def get_facts(self, min_confidence, limit):
            return [{"fact": "测试", "category": "general", "confidence": 0.5}]

    engine = RAGEngineV2(MockVM(), MockSM())
    assert hasattr(engine, "mark_dirty")
    engine.mark_dirty()
    assert engine._keyword_retriever._bm25._dirty is True


# ═══════════════════════════════════════════════════════════════
#  边界与异常路径
# ═══════════════════════════════════════════════════════════════


def test_bm25_index_load_facts_returns_empty_when_no_sm():
    from shisi.knowledge.legacy.rag_engine import BM25Index
    idx = BM25Index(None)
    assert idx._load_facts() == []


def test_bm25_index_build_skips_empty_facts():
    from shisi.knowledge.legacy.rag_engine import BM25Index

    class FakeSM:
        def get_facts(self, min_confidence, limit):
            return [
                {"fact": "", "category": "general", "confidence": 0.5},
                {"fact": "valid fact", "category": "general", "confidence": 0.6},
            ]

    idx = BM25Index(FakeSM())
    idx._build()
    assert "valid fact" in idx._corpus
    assert "" not in idx._corpus


def test_bm25_index_search_empty_query_returns_empty():
    from shisi.knowledge.legacy.rag_engine import BM25Index

    class FakeSM:
        def get_facts(self, min_confidence, limit):
            return [{"fact": "valid fact", "category": "general", "confidence": 0.6}]

    idx = BM25Index(FakeSM())
    assert idx.search("") == []
    assert idx.search("?!@#") == []


def test_bm25_index_search_exception_returns_empty():
    import time

    from shisi.knowledge.legacy.rag_engine import BM25Index

    class FakeIndex:
        def get_scores(self, query):
            raise RuntimeError("boom")

    class FakeSM:
        def get_facts(self, min_confidence, limit):
            return [{"fact": "valid fact", "category": "general", "confidence": 0.6}]

    idx = BM25Index(FakeSM())
    idx._corpus = ["valid fact"]
    idx._docs = [{"fact": "valid fact", "category": "general", "confidence": 0.6}]
    idx._index = FakeIndex()
    idx._dirty = False
    idx._last_build_time = time.monotonic()
    assert idx.search("valid") == []


def test_keyword_retriever_bm25_results_sliced_to_top_k():
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever

    class FakeSM:
        def get_facts(self, min_confidence, limit):
            return [
                {"fact": f"I love {animal}", "category": "g", "confidence": 0.5}
                for animal in ("cats", "dogs", "birds", "rabbits", "fish")
            ]

        def search_facts(self, kw):
            return []

    kr = KeywordRetriever(FakeSM())
    # "love" 出现在多个文档中且分数为正，验证 top_k 切片
    results = kr.search("love", top_k=2)
    assert 0 < len(results) <= 2


def test_keyword_retriever_fallback_without_bm25(monkeypatch):
    from shisi.knowledge.legacy import rag_engine as rag_mod
    from shisi.knowledge.legacy.rag_engine import KeywordRetriever

    monkeypatch.setattr(rag_mod, "HAS_BM25", False)

    class FakeSM:
        def search_facts(self, kw):
            return [{"fact": f"match_{kw}", "category": "g", "confidence": 0.6}]

    kr = KeywordRetriever(FakeSM())
    results = kr.search("hello world", top_k=5)
    contents = {r["content"] for r in results}
    assert "match_hello" in contents or "match_world" in contents


def test_reranker_skips_empty_content():
    from shisi.knowledge.legacy.rag_engine import Reranker
    rr = Reranker()
    vec = [{"content": ""}, {"content": "real"}]
    kw = [{"content": ""}, {"content": "real"}]
    results = rr.rerank(vec, kw)
    assert all(r["content"] for r in results)


def test_hallucination_guard_list_results_verified():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5):
            return [{"content": "喜欢猫"}]
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is True
    assert claim == ""


def test_hallucination_guard_list_results_unverifiable():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5):
            return []
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is False
    assert claim == "猫"


def test_hallucination_guard_search_exception_returns_safe():
    from shisi.knowledge.legacy.rag_engine import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5):
            raise RuntimeError("semantic memory down")
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is True
    assert claim == ""


def test_rag_engine_retrieve_vector_search_exception_logged():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2

    class BadVM:
        def search_chats_sync(self, q, k):
            raise RuntimeError("vector down")

    class MockSM:
        def search_facts(self, q): return []

    engine = RAGEngineV2(BadVM(), MockSM())
    result = engine.retrieve("query")
    assert result["total_vector"] == 0
    assert result["total_keyword"] == 0
    assert isinstance(result["results"], list)


def test_rag_engine_retrieve_tone_mimic_exception_ignored():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2

    class MockVM:
        def search_chats_sync(self, q, k): return []

    class MockSM:
        def search_facts(self, q): return []

    class BadTone:
        def retrieve_style_examples(self, query, top_k=3):
            raise RuntimeError("tone down")

    engine = RAGEngineV2(MockVM(), MockSM(), tone_mimic=BadTone())
    result = engine.retrieve("query")
    assert result["style_examples"] == []


@pytest.mark.asyncio
async def test_rag_engine_retrieve_async_timeout():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2

    class MockVM:
        def search_chats_sync(self, q, k): return []

    class MockSM:
        def search_facts(self, q): return []

    engine = RAGEngineV2(MockVM(), MockSM(), query_timeout=0.001)

    def slow_retrieve(query, top_k):
        import time
        time.sleep(0.1)
        return {"results": []}

    engine.retrieve = slow_retrieve  # type: ignore[method-assign]
    result = await engine.retrieve_async("query")
    assert result["results"] == []
    assert result["total_vector"] == 0
    assert result["total_keyword"] == 0


def test_rag_engine_validate_reply_delegates_to_guard():
    from shisi.knowledge.legacy.rag_engine import RAGEngineV2

    class MockVM:
        def search_chats_sync(self, q, k): return []

    class MockSM:
        def search(self, q, top_k=5):
            return {"vector": [], "exact": []}

    engine = RAGEngineV2(MockVM(), MockSM(), semantic_memory=MockSM())
    ok, claim = engine.validate_reply("你喜欢猫。")
    assert ok is False
    assert claim == "猫"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All rag_engine tests passed!")
