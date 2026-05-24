"""单元测试: RAG引擎 — 深度版"""
import sys

sys.path.insert(0, ".")


# ═══════════════════════════════════════════════════════════════
#  RAGEngineV2 方法和属性验证
# ═══════════════════════════════════════════════════════════════

def test_rag_engine_import():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert RAGEngineV2 is not None


def test_rag_engine_has_retrieve():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert hasattr(RAGEngineV2, "retrieve")
    assert callable(RAGEngineV2.retrieve)


def test_rag_engine_has_validate_reply():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert hasattr(RAGEngineV2, "validate_reply")
    assert callable(RAGEngineV2.validate_reply)


def test_rag_engine_has_health_check():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert hasattr(RAGEngineV2, "health_check")
    assert callable(RAGEngineV2.health_check)


def test_rag_engine_init_attributes():
    from rag_engine.rag_engine_v2 import RAGEngineV2
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
    from rag_engine.rag_engine_v2 import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    class MockSemantic:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    engine = RAGEngineV2(MockVM(), MockSM(), semantic_memory=MockSemantic())
    assert engine._hallucination_guard is not None


def test_rag_engine_health_check():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    health = engine.health_check()
    assert health["available"] is True
    assert "bm25_available" in health


def test_rag_engine_validate_reply_no_guard():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    class MockVM:
        def search_chats_sync(self, q, k): return []
    class MockSM:
        def search_facts(self, q): return []
    engine = RAGEngineV2(MockVM(), MockSM())
    ok, claim = engine.validate_reply("普通回复")
    assert ok is True
    assert claim == ""


def test_rag_engine_retrieve_result_keys():
    from rag_engine.rag_engine_v2 import RAGEngineV2
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
    from rag_engine.rag_engine_v2 import RAGEngineV2
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
    from rag_engine.rag_engine_v2 import KeywordRetriever
    assert KeywordRetriever is not None


def test_keyword_retriever_search():
    from rag_engine.rag_engine_v2 import KeywordRetriever
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
    from rag_engine.rag_engine_v2 import KeywordRetriever
    class MockSM:
        def search_facts(self, kw):
            return [{"fact": f"fact_{i}", "category": "g", "confidence": 0.5} for i in range(10)]
    kr = KeywordRetriever(MockSM())
    results = kr.search("测试", top_k=2)
    assert len(results) <= 2


def test_keyword_retriever_dedup():
    from rag_engine.rag_engine_v2 import KeywordRetriever
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
    from rag_engine.rag_engine_v2 import Reranker
    assert Reranker is not None


def test_reranker_default_weights():
    from rag_engine.rag_engine_v2 import Reranker
    rr = Reranker()
    assert rr.vector_weight == 0.7
    assert rr.keyword_weight == 0.3


def test_reranker_custom_weights():
    from rag_engine.rag_engine_v2 import Reranker
    rr = Reranker(vector_weight=0.5, keyword_weight=0.5)
    assert rr.vector_weight == 0.5
    assert rr.keyword_weight == 0.5


def test_reranker_rerank_empty():
    from rag_engine.rag_engine_v2 import Reranker
    rr = Reranker()
    results = rr.rerank([], [])
    assert results == []


def test_reranker_rerank_vector_only():
    from rag_engine.rag_engine_v2 import Reranker
    rr = Reranker()
    vec = [{"content": "a", "distance": 0.2}]
    results = rr.rerank(vec, [])
    assert len(results) == 1
    assert results[0]["content"] == "a"
    assert results[0]["score"] > 0


def test_reranker_rerank_threshold():
    from rag_engine.rag_engine_v2 import Reranker
    rr = Reranker()
    vec = [{"content": "a", "distance": 0.99}]
    results = rr.rerank(vec, [], threshold=0.9)
    assert len(results) == 0


def test_reranker_rerank_mixed():
    from rag_engine.rag_engine_v2 import Reranker
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
    from rag_engine.rag_engine_v2 import ContextBudgetMgr
    assert ContextBudgetMgr is not None


def test_budget_mgr_defaults():
    from rag_engine.rag_engine_v2 import ContextBudgetMgr
    bm = ContextBudgetMgr()
    assert bm.max_tokens == 4096
    assert bm.retrieval_ratio == 0.4


def test_budget_mgr_truncate_under_budget():
    from rag_engine.rag_engine_v2 import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=4096, retrieval_ratio=0.4)
    items = [{"content": f"item_{i}"} for i in range(5)]
    result = bm.truncate(items, estimated_tokens_per_item=100)
    assert len(result) == 5


def test_budget_mgr_truncate_over_budget():
    from rag_engine.rag_engine_v2 import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=400, retrieval_ratio=0.5)
    items = [{"content": f"item_{i}"} for i in range(100)]
    result = bm.truncate(items, estimated_tokens_per_item=100)
    assert len(result) < 100
    assert len(result) == 2


def test_budget_mgr_custom_params():
    from rag_engine.rag_engine_v2 import ContextBudgetMgr
    bm = ContextBudgetMgr(max_context_tokens=8192, retrieval_ratio=0.6)
    assert bm.max_tokens == 8192
    assert bm.retrieval_ratio == 0.6


# ═══════════════════════════════════════════════════════════════
#  HallucinationGuard 验证
# ═══════════════════════════════════════════════════════════════

def test_hallucination_guard_import():
    from rag_engine.rag_engine_v2 import HallucinationGuard
    assert HallucinationGuard is not None


def test_hallucination_guard_safe_reply():
    from rag_engine.rag_engine_v2 import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("今天天气真好")
    assert ok is True
    assert claim == ""


def test_hallucination_guard_unverifiable_claim():
    from rag_engine.rag_engine_v2 import HallucinationGuard
    class MockSM:
        def search(self, q, top_k=5): return {"vector": [], "exact": []}
    hg = HallucinationGuard(MockSM())
    ok, claim = hg.check("你喜欢猫。")
    assert ok is False
    assert claim == "猫"


def test_hallucination_guard_verifiable_claim():
    from rag_engine.rag_engine_v2 import HallucinationGuard
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
    from rag_engine.rag_engine_v2 import HAS_BM25
    assert isinstance(HAS_BM25, bool)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All rag_engine tests passed!")
