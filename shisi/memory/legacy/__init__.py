"""shisi.memory.legacy — 记忆管线核心模块。

命名说明：`legacy` 是历史命名（迁移自原根 `memory/` 目录），
此处的模块**仍在活跃使用**，是 MemoryService 的底层依赖，
被 shisi/application/memory_service.py 与 tests/test_memory*.py 深度引用。
**不要按字面意思当作"待删除"处理。**
"""
from .conflict_detector import ConflictDetector
from .diary_summarizer import DiarySummarizer
from .episodic_memory import EpisodicMemory
from .fact_extractor import FactExtractor
from .forgetting_manager import ForgettingManager
from .importance_scorer import ImportanceScorer
from .memory_pipeline import MemoryPipeline
from .semantic_memory import SemanticMemory
from .structured_memory import StructuredMemory
from .vector_memory import VectorMemory
from .working_memory import WorkingMemory

# P1-17（2026-09-21 审查修复）：ConflictDetector/CrossSessionReasoner/
# ForgettingManager 曾从 importance_scorer 导入——那是**无隔离**的旧副本
# （get_pending_events 全表返回、check_conflict 不带 user_key），与 pipeline
# 实际使用的独立模块形成双实现地雷。包入口现唯一指向现役实现。
# 2026-09-22：CrossSessionReasoner（pending_events 死链）整体拆除出库，
# 见 docs/DELETION_LOG.md。

__all__ = [
    "VectorMemory", "StructuredMemory", "FactExtractor", "DiarySummarizer",
    "MemoryPipeline",
    "WorkingMemory", "EpisodicMemory", "SemanticMemory",
    "ImportanceScorer", "ForgettingManager", "ConflictDetector",
]
