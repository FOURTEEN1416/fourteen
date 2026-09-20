"""兼容入口：语义记忆已收敛到 `semantic_memory.py`（含 user_key 隔离）。"""

from .semantic_memory import SemanticMemory

__all__ = ["SemanticMemory"]
