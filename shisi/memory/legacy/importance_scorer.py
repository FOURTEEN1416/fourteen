from __future__ import annotations

import logging

logger = logging.getLogger("importance_scorer")

FACT_TYPE_WEIGHTS = {
    "health": 1.0,
    "relationship": 0.9,
    "preference": 0.8,
    "event": 0.7,
    "work": 0.6,
    "hobby": 0.5,
    "general": 0.3,
}


class ImportanceScorer:
    def __init__(self, type_weights: dict[str, float] | None = None):
        self.type_weights = type_weights or FACT_TYPE_WEIGHTS

    def score(self, emotion_intensity: float, fact_type: str,
              confidence: float, is_novel: bool = True,
              days_since_access: int = 0) -> float:
        type_w = self.type_weights.get(fact_type, 0.3)
        novelty = 1.5 if is_novel else 1.0
        raw = emotion_intensity * type_w * confidence * novelty
        raw = max(0.0, min(1.0, raw))
        return raw

# P1-17（2026-09-21 审查修复）：此处的 ForgettingManager/ConflictDetector/
# CrossSessionReasoner 是**无隔离旧副本**（get_pending_events 全表、冲突检测
# 不传 user_key），现役实现分别在同包 forgetting_manager.py /
# conflict_detector.py / cross_session_reasoner.py，全仓零消费者——已删除并入
# docs/DELETION_LOG.md，杜绝 `from … import X` 拿错版本的双实现地雷。
