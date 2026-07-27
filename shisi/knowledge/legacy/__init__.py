"""shisi.knowledge.legacy — RAG 引擎核心模块。

命名说明：`legacy` 是历史命名（迁移自原根 `rag_engine/` 目录），
此处的模块**仍在活跃使用**，提供 RAGEngineV2 / KeywordRetriever / Reranker
/ ContextBudgetMgr / HallucinationGuard 等 RAG 核心抽象，
被 tests/test_rag_engine.py 深度引用。
**不要按字面意思当作"待删除"处理。**
"""

"""RAG 引擎 — 向量 + 关键词检索、重排序、上下文预算管理、幻觉检测"""

from shisi.knowledge.legacy.rag_engine import (
    ContextBudgetMgr,
    HallucinationGuard,
    KeywordRetriever,
    RAGEngineV2,
    Reranker,
)

__all__ = [
    "RAGEngineV2",
    "KeywordRetriever",
    "Reranker",
    "ContextBudgetMgr",
    "HallucinationGuard",
]
