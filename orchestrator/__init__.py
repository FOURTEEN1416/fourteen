"""orchestrator 包 — 对话编排器、语音检测、会话锁、运行模式。

合并说明：根目录 ``orchestrator.py`` 已删除，编排逻辑统一由本包提供。
``Orchestrator`` 是 ``OptimizedOrchestrator`` 的向后兼容别名，
旧代码 ``from orchestrator import Orchestrator`` 继续可用。
"""

from __future__ import annotations

from orchestrator.optimized_orchestrator import OptimizedOrchestrator
from orchestrator.voice_detector import detect_voice_request
from orchestrator.session_locks import SessionLockManager

# 向后兼容别名：旧代码通过 ``from orchestrator import Orchestrator`` 引用编排器。
# 根目录 ``orchestrator.py`` 已删除，``orchestrator/`` 包是唯一的编排入口。
# 此别名确保旧导入路径仍然可用。
Orchestrator = OptimizedOrchestrator

__all__ = [
    "OptimizedOrchestrator",
    "Orchestrator",
    "detect_voice_request",
    "SessionLockManager",
]
