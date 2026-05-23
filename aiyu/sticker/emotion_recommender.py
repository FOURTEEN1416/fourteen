"""情感驱动推荐器 — Jaccard相似度匹配。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("aiyu.sticker.emotion_recommender")


class EmotionRecommender:
    def recommend(
        self,
        emotion_tags: list[str],
        all_stickers: list[dict[str, Any]],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if not emotion_tags or not all_stickers:
            return []

        start = time.perf_counter()
        scored: list[tuple[float, dict[str, Any]]] = []

        for sticker in all_stickers:
            tags_raw = sticker.get("emotion_tags", "[]")
            if isinstance(tags_raw, str):
                try:
                    sticker_tags = json.loads(tags_raw)
                except json.JSONDecodeError:
                    sticker_tags = []
            else:
                sticker_tags = tags_raw

            similarity = self._jaccard(emotion_tags, sticker_tags)
            if similarity > 0:
                scored.append((similarity, sticker))

        scored.sort(key=lambda x: x[0], reverse=True)
        elapsed_ms = (time.perf_counter() - start) * 1000

        if elapsed_ms > 200:
            logger.warning("表情推荐耗时%.1fms > 200ms", elapsed_ms)

        return [s for _, s in scored[:limit]]

    @staticmethod
    def _jaccard(a: list[str], b: list[str]) -> float:
        if not a or not b:
            return 0.0
        set_a = set(a)
        set_b = set(b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0
