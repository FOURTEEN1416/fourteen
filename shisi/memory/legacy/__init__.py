"""shisi.memory.legacy — 记忆管线核心模块。

命名说明：`legacy` 是历史命名（迁移自原根 `memory/` 目录），
此处的模块**仍在活跃使用**，是 MemoryService 的底层依赖，
被 shisi/application/memory_service.py 与 tests/test_memory*.py 深度引用。
**不要按字面意思当作"待删除"处理。**
"""
from .diary_summarizer import DiarySummarizer
from .episodic_memory import EpisodicMemory
from .fact_extractor import FactExtractor
from .importance_scorer import (
    ConflictDetector,
    CrossSessionReasoner,
    ForgettingManager,
    ImportanceScorer,
)
from .memory_pipeline import MemoryPipeline
from .semantic_memory import SemanticMemory
from .structured_memory import StructuredMemory
from .vector_memory import VectorMemory
from .working_memory import WorkingMemory

MemoryPipelineV2 = MemoryPipeline
MemoryPipelineOptimized = MemoryPipeline

__all__ = [
    "VectorMemory", "StructuredMemory", "FactExtractor", "DiarySummarizer",
    "MemoryPipeline", "MemoryPipelineV2", "MemoryPipelineOptimized",
    "WorkingMemory", "EpisodicMemory", "SemanticMemory",
    "ImportanceScorer", "ForgettingManager", "ConflictDetector", "CrossSessionReasoner",
]
