"""
知识宝库模块 — 给空壳人设装上实时大脑

让AI不仅说话语气像，而且真的"知道"这个人的一切
"""

from .content_extractor import ContentExtractor
from .feed_reader import FeedReader
from .knowledge_injector import KnowledgeInjector
from .knowledge_store import KnowledgeStore
from .persona_rewriter import PersonaRewriter
from .scheduler import KnowledgeScheduler
from .search_collector import SearchCollector

__all__ = [
    "FeedReader",
    "SearchCollector",
    "ContentExtractor",
    "PersonaRewriter",
    "KnowledgeStore",
    "KnowledgeInjector",
    "KnowledgeScheduler",
]
