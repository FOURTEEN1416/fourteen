"""薄包装层 — Orchestrator 已合并至 ``orchestrator.optimized_orchestrator``。

历史背景：项目曾同时维护根目录 ``orchestrator.py``（全量模式）和
``orchestrator/optimized_orchestrator.py``（快速模式），两者共享约 70% 代码
但独立维护，导致逻辑漂移和维护负担。

合并方案：
    * 处理逻辑统一由 ``OptimizedOrchestrator`` 提供。
    * ``Orchestrator`` 作为向后兼容别名导出。
    * 旧代码 ``from orchestrator import Orchestrator`` 继续可用
      （Python 包优先于同名模块，``orchestrator/`` 包会遮蔽本文件）。

注意：此文件仅用于文档/兼容目的；实际导入会解析到 ``orchestrator/`` 包。
"""

from __future__ import annotations

from orchestrator.optimized_orchestrator import OptimizedOrchestrator

# 向后兼容别名
Orchestrator = OptimizedOrchestrator

__all__ = ["Orchestrator", "OptimizedOrchestrator"]
