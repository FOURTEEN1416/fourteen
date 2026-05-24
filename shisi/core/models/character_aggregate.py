"""角色聚合根 — 统一身份/人设/情感/元数据"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .affinity_level import AffinityLevel
from .emotion_type import EmotionType
from .emotional_state import EmotionalState
from .persona_profile import PersonaProfile


class CharacterAggregate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    description: str = ""
    avatar_url: str | None = None
    tags: list[str] = Field(default_factory=list)

    persona: PersonaProfile = Field(default_factory=PersonaProfile)
    emotional_state: EmotionalState = Field(default_factory=EmotionalState)

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    version: int = 1

    source_format: str = "chara_card_v2"
    source_data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("角色名称不能为空")
        return v.strip()

    def update_emotion(self, message: str) -> EmotionalState:
        msg = message.lower()

        affection_delta = 0.2
        positive_words = ["想", "喜欢", "爱", "好", "乖", "棒", "对不起", "宝贝"]
        negative_words = ["烦", "滚", "闭嘴", "无语", "讨厌"]

        for word in positive_words:
            if word in msg:
                affection_delta += 1.0
        for word in negative_words:
            if word in msg:
                affection_delta -= 0.5

        new_points = self.emotional_state.affection_points + affection_delta

        new_level = self.emotional_state.affinity_level
        while (
            new_level < AffinityLevel.BOND
            and new_points >= AffinityLevel(new_level.value + 1).threshold
        ):
            new_level = AffinityLevel(new_level.value + 1)

        new_state = EmotionalState(
            primary_emotion=self._detect_emotion(msg),
            intensity=0.7 if any(w in msg for w in ["非常", "很", "特别"]) else 0.5,
            energy=max(0.0, self.emotional_state.energy - 0.02),
            affinity_level=new_level,
            affection_points=new_points,
        )

        self.emotional_state = new_state
        self.updated_at = datetime.now()
        return new_state

    def _detect_emotion(self, message: str) -> EmotionType:
        if any(w in message for w in ["想", "爱", "亲", "抱抱"]):
            return EmotionType.LOVELY
        if any(w in message for w in ["生气", "讨厌", "烦"]):
            return EmotionType.ANGRY
        if any(w in message for w in ["伤心", "难过", "哭"]):
            return EmotionType.SAD
        if any(w in message for w in ["哈哈", "嘻嘻", "开心"]):
            return EmotionType.HAPPY
        if "困" in message or "累" in message:
            return EmotionType.TIRED
        return EmotionType.NEUTRAL

    def build_system_prompt(self, user_message: str = "", chat_history: str = "") -> str:
        parts = [
            f"# 角色设定\n\n你是{self.name}。",
            self.description,
            "",
            self.persona.to_prompt_segment(),
            "",
            "# 当前状态",
            f"- 情感: {self.emotional_state.primary_emotion.name}",
            f"- 能量: {self.emotional_state.energy:.1f}",
            f"- 关系: {self.emotional_state.affinity_level.display_name}",
        ]

        if chat_history:
            parts.extend(["", "# 对话历史", chat_history])

        if user_message:
            parts.extend(["", f"用户: {user_message}", f"{self.name}:"])

        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "avatar_url": self.avatar_url,
            "tags": self.tags,
            "persona": self.persona.to_dict(),
            "emotional_state": self.emotional_state.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "version": self.version,
            "source_format": self.source_format,
        }

    @classmethod
    def from_legacy_card(cls, card_data: dict) -> CharacterAggregate:
        data = card_data.get("data", card_data)

        char_id = card_data.get("character_id") or str(uuid.uuid4())[:8]

        persona = PersonaProfile()
        if "personality_traits" in card_data:
            traits = card_data["personality_traits"]
            persona = PersonaProfile(
                warmth=traits.get("warmth", 0.7),
                playfulness=traits.get("playfulness", 0.5),
                independence=traits.get("independence", 0.6),
                jealousy=traits.get("jealousy", 0.4),
                stubbornness=traits.get("stubbornness", 0.5),
            )

        if "core_anchors" in card_data:
            persona.core_anchors = card_data["core_anchors"]
        elif "personality" in data:
            persona.core_anchors = [data["personality"][:100]]

        affinity_level = AffinityLevel.STRANGER
        affection_points = 0.0
        if "affinity" in card_data:
            affinity_val = int(card_data["affinity"])
            affinity_level = AffinityLevel(min(8, max(0, affinity_val)))
        if "affection_points" in card_data:
            affection_points = max(0.0, float(card_data["affection_points"]))

        emotional_state = EmotionalState(
            affinity_level=affinity_level,
            affection_points=affection_points,
        )

        return cls(
            id=char_id,
            name=data.get("name", "未命名角色"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            persona=persona,
            emotional_state=emotional_state,
            source_format=card_data.get("spec", "chara_card_v2"),
            source_data=card_data,
        )
