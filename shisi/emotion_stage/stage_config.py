"""情感阶段配置加载 — 从shisi.yaml加载阶段定义。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import get_config


@dataclass
class StageDefinition:
    name: str
    affinity_min: float
    affinity_max: float
    features: list[str] = field(default_factory=list)


@dataclass
class EmotionStageConfig:
    stages: list[StageDefinition] = field(default_factory=list)
    allow_backward: bool = False

    @classmethod
    def from_yaml(cls) -> "EmotionStageConfig":
        raw = get_config("emotion_stage")
        if not raw:
            return cls.default()

        stages = []
        for s in raw.get("stages", []):
            stages.append(StageDefinition(
                name=s.get("name", "未知"),
                affinity_min=s.get("affinity_min", 0),
                affinity_max=s.get("affinity_max", 100),
                features=s.get("features", []),
            ))

        return cls(
            stages=stages,
            allow_backward=raw.get("allow_backward", False),
        )

    @classmethod
    def default(cls) -> "EmotionStageConfig":
        return cls(stages=[
            StageDefinition("陌生", 0, 25, ["基础对话"]),
            StageDefinition("熟悉", 25, 50, ["基础对话", "个人话题"]),
            StageDefinition("亲密", 50, 75, ["基础对话", "个人话题", "亲密话题", "专属表情"]),
            StageDefinition("羁绊", 75, 100, ["基础对话", "个人话题", "亲密话题", "专属表情", "深层秘密", "特殊称呼"]),
        ])
