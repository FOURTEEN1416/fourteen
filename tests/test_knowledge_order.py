"""6b：知识注入排序不变量。

审计发现：CharacterKnowledgeService.search 双路交错合并（扩展路占首位）后，
RetrievalResult.get_top 又按 .score 重排 → 交错序被彻底打散；且
KeywordRetriever.search 把分数写回共享索引对象，扩展路/原路两次检索互相
覆盖分数（量纲不可比却冒充可比）。
"""

from __future__ import annotations

from shisi.knowledge.character_knowledge_service import (
    CharacterKnowledgeService,
    _expand_query,
)
from shisi.knowledge.retriever import (
    KeywordRetriever,
    KnowledgeChunk,
    RetrievalResult,
)


class _FakeRetriever:
    """按 query 精确匹配返回预置结果，分数人为设定。"""

    def __init__(self, results: dict[str, list[KnowledgeChunk]]):
        self._results = results

    def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        return RetrievalResult(chunks=list(self._results[query]))


def _svc_with(results: dict[str, list[KnowledgeChunk]]) -> CharacterKnowledgeService:
    svc = CharacterKnowledgeService(use_bm25=False)
    svc._retrievers["c1"] = _FakeRetriever(results)  # type: ignore[assignment]
    return svc


QUERY = "你家里有什么人"


def test_interleaved_order_survives_get_top_and_prompt_context():
    expanded = _expand_query(QUERY)
    assert expanded  # 信号词「家里/家人」必须触发扩展路

    ext = [KnowledgeChunk(content="扩展块A", score=1.0),
           KnowledgeChunk(content="扩展块B", score=0.5)]
    base = [KnowledgeChunk(content="原路块1", score=9.0),
            KnowledgeChunk(content="原路块2", score=8.0)]
    svc = _svc_with({expanded: ext, QUERY: base})

    result = svc.search("c1", QUERY, top_k=4)
    # 交错且首位给扩展路（措辞更贴近知识库原文）
    assert [c.content for c in result.chunks] == ["扩展块A", "原路块1", "扩展块B", "原路块2"]
    # get_top 不得按 score 重排（原路分数更高，重排会破坏交错）
    assert [c.content for c in result.get_top(4)] == ["扩展块A", "原路块1", "扩展块B", "原路块2"]
    ctx = result.to_prompt_context(k=4)
    assert ctx.index("扩展块A") < ctx.index("原路块1") < ctx.index("扩展块B")


def test_merges_dedup_by_content():
    expanded = _expand_query(QUERY)
    shared = KnowledgeChunk(content="重合块", score=2.0)
    svc = _svc_with({
        expanded: [shared, KnowledgeChunk(content="仅扩展", score=1.0)],
        QUERY: [KnowledgeChunk(content="重合块", score=7.0)],
    })
    result = svc.search("c1", QUERY, top_k=5)
    contents = [c.content for c in result.chunks]
    assert contents.count("重合块") == 1
    assert contents == ["重合块", "仅扩展"]


def test_search_returns_copies_and_never_mutates_index():
    retriever = KeywordRetriever()
    chunks = [
        KnowledgeChunk(content="昭阳是米彩的恋人在杂志编辑部工作"),
        KnowledgeChunk(content="米彩是卓阳饭店的总经理性格倔强"),
    ]
    retriever.index(chunks)
    assert all(c.score == 0.0 for c in chunks)

    r1 = retriever.search("昭阳 恋人", top_k=2)
    assert all(not any(r1.chunks[i] is chunks[j] for j in range(len(chunks)))
               for i in range(len(r1.chunks)))
    # 索引对象分数不被检索污染
    assert all(c.score == 0.0 for c in chunks)

    # 二次检索（不同 query）分数互不覆盖：结果分数来自本次计算
    r2 = retriever.search("米彩 总经理", top_k=1)
    top = r2.get_top(1)[0]
    assert "米彩" in top.content
    assert top.score > 0.0
    assert all(c.score == 0.0 for c in chunks)
