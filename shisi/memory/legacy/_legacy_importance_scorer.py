"""
重要性评分器 — 关键词 + 情感 + 信息密度 + 时间衰减（memory_pipeline 内联版本）

基于关键词、情感标签、内容长度和信息密度对消息进行重要性评分。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("importance_scorer")


class ImportanceScorer:
    """重要性评分器 — 关键词 + 情感 + 信息密度 + 时间衰减"""

    KEYWORD_WEIGHTS = {
        "喜欢": 0.3, "爱": 0.4, "想": 0.2,
        "重要": 0.3, "记住": 0.3, "别忘": 0.3,
        "生日": 0.5, "纪念日": 0.5, "约定": 0.4,
        "生气": 0.3, "难过": 0.3, "开心": 0.2,
    }

    EMOTION_WEIGHTS = {
        "生气": 0.3, "难过": 0.3, "开心": 0.1,
        "撒娇": 0.2, "吃醋": 0.25, "傲娇": 0.15,
    }

    def score(self, content: str, emotion: str = "",
              context: dict | None = None) -> float:
        s = 0.3
        for keyword, weight in self.KEYWORD_WEIGHTS.items():
            if keyword in content:
                s += weight
        s += self.EMOTION_WEIGHTS.get(emotion, 0.1)
        s += min(0.2, len(content) / 500)
        if context and context.get("is_response_to_question"):
            s += 0.1
        return min(1.0, s)

    def should_retain(self, importance: float, days_old: float,
                      access_count: int = 0) -> bool:
        if importance >= 0.8:
            return True
        time_decay = max(0, 1 - days_old / 30)
        access_bonus = min(0.3, access_count * 0.05)
        final_score = importance * time_decay + access_bonus
        return final_score >= 0.2
