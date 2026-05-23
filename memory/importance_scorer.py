from __future__ import annotations

import logging
import math
from typing import Dict, Optional

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
    def __init__(self, type_weights: Optional[Dict[str, float]] = None):
        self.type_weights = type_weights or FACT_TYPE_WEIGHTS

    def score(self, emotion_intensity: float, fact_type: str,
              confidence: float, is_novel: bool = True,
              days_since_access: int = 0) -> float:
        type_w = self.type_weights.get(fact_type, 0.3)
        novelty = 1.5 if is_novel else 1.0
        raw = emotion_intensity * type_w * confidence * novelty
        raw = max(0.0, min(1.0, raw))
        return raw


class ForgettingManager:
    def __init__(self, lambda_low: float = 0.1, lambda_high: float = 0.01):
        self.lambda_low = lambda_low
        self.lambda_high = lambda_high

    def retrieval_weight(self, importance: float, days_since_access: float) -> float:
        lam = self.lambda_high if importance >= 0.7 else self.lambda_low
        weight = importance * math.exp(-lam * days_since_access)
        return max(0.0, min(1.0, weight))

    def should_delete(self, importance: float, days_since_access: float,
                      threshold: float = 0.05) -> bool:
        return self.retrieval_weight(importance, days_since_access) < threshold


class ConflictDetector:
    def __init__(self, semantic_memory):
        self._sm = semantic_memory

    def check_conflict(self, new_fact: str, category: str) -> Optional[Dict]:
        search_results = self._sm.search(new_fact, top_k=3)
        vector_results = search_results.get("vector", [])
        for result in vector_results:
            existing = result.get("content", "")
            distance = result.get("distance", 1.0)
            if distance < 0.3 and existing != new_fact:
                return {
                    "new_fact": new_fact,
                    "existing_fact": existing,
                    "similarity": 1.0 - distance,
                    "category": category,
                    "status": "pending",
                }
        return None


class CrossSessionReasoner:
    def __init__(self, structured_memory):
        self._sm = structured_memory

    def extract_pending_events(self, fact: str) -> Optional[Dict]:
        future_keywords = ["明天", "下周", "周末", "之后", "以后", "即将", "将要"]
        for kw in future_keywords:
            if kw in fact:
                return {"event_desc": fact, "keyword": kw}
        return None

    def store_pending_event(self, event_desc: str, expected_time: Optional[str] = None,
                            session_id: str = ""):
        with self._sm.get_connection() as conn:
            conn.execute(
                "INSERT INTO pending_events (event_desc, expected_time, source_session_id) "
                "VALUES (?, ?, ?)",
                (event_desc, expected_time, session_id),
            )
            conn.commit()

    def get_pending_events(self):
        with self._sm.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_events WHERE is_resolved = 0 "
                "ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_event(self, event_id: int):
        with self._sm.get_connection() as conn:
            conn.execute(
                "UPDATE pending_events SET is_resolved = 1 WHERE id = ?",
                (event_id,),
            )
            conn.commit()
