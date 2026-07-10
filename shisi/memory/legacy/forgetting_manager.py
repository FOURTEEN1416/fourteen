"""
遗忘管理器 — 指数衰减遗忘模型

基于信息重要性分层设置衰减率，模拟艾宾浩斯遗忘曲线。
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger("forgetting_manager")


class ForgettingManager:
    """指数衰减遗忘模型 — 重要性分层衰减率"""

    def __init__(self, lambda_low: float = 0.1, lambda_high: float = 0.01):
        self.lambda_low = lambda_low
        self.lambda_high = lambda_high

    def retrieval_weight(self, importance: float,
                         days_since_access: float) -> float:
        lam = self.lambda_high if importance >= 0.7 else self.lambda_low
        weight = importance * math.exp(-lam * days_since_access)
        return max(0.0, min(1.0, weight))

    def should_delete(self, importance: float, days_since_access: float,
                      threshold: float = 0.05) -> bool:
        return self.retrieval_weight(importance, days_since_access) < threshold
