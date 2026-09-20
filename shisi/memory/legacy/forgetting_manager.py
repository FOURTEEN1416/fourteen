"""
遗忘管理器 — 指数衰减遗忘模型

基于信息重要性分层设置衰减率，模拟艾宾浩斯遗忘曲线。
2026-09-20 B4：接入 access_count 回忆强化——被反复检索/注入的事实等效重要性提升。
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger("forgetting_manager")


class ForgettingManager:
    """指数衰减遗忘模型 — 重要性分层衰减率 + 回忆强化"""

    def __init__(self, lambda_low: float = 0.1, lambda_high: float = 0.01,
                 recall_bonus_per_access: float = 0.05,
                 recall_bonus_cap: float = 0.3):
        self.lambda_low = lambda_low
        self.lambda_high = lambda_high
        self.recall_bonus_per_access = recall_bonus_per_access
        self.recall_bonus_cap = recall_bonus_cap

    def effective_importance(self, importance: float, access_count: int = 0) -> float:
        """回忆强化后的等效重要性（越回忆越牢，封顶防止无限膨胀）。"""
        bonus = min(
            self.recall_bonus_cap,
            self.recall_bonus_per_access * max(0, int(access_count or 0)),
        )
        return max(0.0, min(1.0, float(importance) + bonus))

    def retrieval_weight(self, importance: float,
                         days_since_access: float,
                         access_count: int = 0) -> float:
        eff = self.effective_importance(importance, access_count)
        # 回忆刷新衰减时钟：每次访问把等效天数压低（越回忆越接近“刚记住”）
        days_eff = max(0.0, float(days_since_access) * (0.7 ** min(int(access_count or 0), 8)))
        lam = self.lambda_high if eff >= 0.7 else self.lambda_low
        weight = eff * math.exp(-lam * days_eff)
        return max(0.0, min(1.0, weight))

    def should_delete(self, importance: float, days_since_access: float,
                      threshold: float = 0.05,
                      access_count: int = 0) -> bool:
        return self.retrieval_weight(
            importance, days_since_access, access_count
        ) < threshold
