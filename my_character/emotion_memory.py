"""
情感记忆系统 — 记录情感事件与模式

基于现有MemoryPipeline扩展的情感记忆层，
记录情感事件、情感模式、情感偏好历史，支持情感触发预测。
"""

from __future__ import annotations

import logging
import time as time_mod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("emotion_memory")


@dataclass
class EmotionEvent:
    emotion: str
    intensity: float
    trigger: str
    user_message: str
    timestamp: float = 0.0
    affinity_at_trigger: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "emotion": self.emotion,
            "intensity": self.intensity,
            "trigger": self.trigger,
            "user_message": self.user_message[:100],
            "timestamp": self.timestamp,
            "affinity_at_trigger": self.affinity_at_trigger,
        }


@dataclass
class EmotionPattern:
    emotion: str
    frequency: float
    common_triggers: List[str]
    avg_intensity: float
    trend: str = "stable"


class EmotionMemorySystem:
    """情感记忆系统 — 记录情感事件与模式"""

    MAX_EVENTS = 500

    def __init__(self, memory_pipeline: Optional[Any] = None):
        self._memory = memory_pipeline
        self._emotion_events: deque = deque(maxlen=self.MAX_EVENTS)
        self._emotion_patterns: Dict[str, EmotionPattern] = {}

    def record_event(
        self,
        emotion: str,
        intensity: float,
        trigger: str,
        user_message: str = "",
        affinity: int = 0,
    ) -> None:
        event = EmotionEvent(
            emotion=emotion,
            intensity=max(0.0, min(1.0, intensity)),
            trigger=trigger,
            user_message=user_message[:100],
            timestamp=time_mod.time(),
            affinity_at_trigger=affinity,
        )
        self._emotion_events.append(event)
        logger.debug("Emotion event recorded: %s (%.2f)", emotion, intensity)

    def detect_patterns(self, window: int = 50) -> List[EmotionPattern]:
        recent = list(self._emotion_events)[-window:]
        if not recent:
            return []

        emotion_data: Dict[str, List[EmotionEvent]] = {}
        for event in recent:
            emotion_data.setdefault(event.emotion, []).append(event)

        patterns = []
        total = len(recent)
        for emotion, events in emotion_data.items():
            freq = len(events) / total
            triggers = [e.trigger for e in events if e.trigger]
            trigger_counts: Dict[str, int] = {}
            for t in triggers:
                trigger_counts[t] = trigger_counts.get(t, 0) + 1
            common_triggers = sorted(trigger_counts, key=trigger_counts.get, reverse=True)[:5]
            avg_intensity = sum(e.intensity for e in events) / len(events)

            trend = "stable"
            if len(events) >= 5:
                half = len(events) // 2
                first_half_avg = sum(e.intensity for e in events[:half]) / half
                second_half_avg = sum(e.intensity for e in events[half:]) / (len(events) - half)
                if second_half_avg > first_half_avg + 0.1:
                    trend = "increasing"
                elif second_half_avg < first_half_avg - 0.1:
                    trend = "decreasing"

            pattern = EmotionPattern(
                emotion=emotion,
                frequency=round(freq, 4),
                common_triggers=common_triggers,
                avg_intensity=round(avg_intensity, 4),
                trend=trend,
            )
            patterns.append(pattern)
            self._emotion_patterns[emotion] = pattern

        patterns.sort(key=lambda p: p.frequency, reverse=True)
        return patterns

    def predict_trigger(self, user_message: str) -> Optional[str]:
        if not self._emotion_patterns:
            self.detect_patterns()

        for emotion, pattern in self._emotion_patterns.items():
            for trigger in pattern.common_triggers:
                if trigger and trigger in user_message:
                    return emotion
        return None

    def get_emotion_history(self, limit: int = 20) -> List[EmotionEvent]:
        return list(self._emotion_events)[-limit:]

    def get_dominant_emotion(self, window: int = 50) -> Tuple[str, float]:
        recent = list(self._emotion_events)[-window:]
        if not recent:
            return ("平常", 0.0)

        emotion_counts: Dict[str, int] = {}
        for event in recent:
            emotion_counts[event.emotion] = emotion_counts.get(event.emotion, 0) + 1

        if not emotion_counts:
            return ("平常", 0.0)

        dominant = max(emotion_counts, key=emotion_counts.get)
        freq = emotion_counts[dominant] / len(recent)
        return (dominant, round(freq, 4))

    def get_emotion_summary(self) -> Dict[str, Any]:
        dominant, freq = self.get_dominant_emotion()
        patterns = self.detect_patterns()
        return {
            "total_events": len(self._emotion_events),
            "dominant_emotion": dominant,
            "dominant_frequency": freq,
            "top_patterns": [
                {"emotion": p.emotion, "frequency": p.frequency, "trend": p.trend}
                for p in patterns[:5]
            ],
        }

    def health_check(self) -> dict:
        return {
            "events_count": len(self._emotion_events),
            "patterns_count": len(self._emotion_patterns),
        }
