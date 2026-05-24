"""情感状态值对象"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .affinity_level import AffinityLevel
from .emotion_type import EmotionType


@dataclass
class EmotionalState:
    primary_emotion: EmotionType = EmotionType.NEUTRAL
    intensity: float = 0.5
    energy: float = 1.0
    affinity_level: AffinityLevel = AffinityLevel.STRANGER
    affection_points: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        self.intensity = max(0.0, min(1.0, self.intensity))
        self.energy = max(0.0, min(1.0, self.energy))
        self.affection_points = max(0.0, self.affection_points)

    def to_dict(self) -> dict:
        return {
            "primary_emotion": self.primary_emotion.name,
            "intensity": round(self.intensity, 2),
            "energy": round(self.energy, 2),
            "affinity_level": self.affinity_level.value,
            "affinity_name": self.affinity_level.display_name,
            "affection_points": round(self.affection_points, 1),
            "updated_at": self.updated_at.isoformat(),
        }
