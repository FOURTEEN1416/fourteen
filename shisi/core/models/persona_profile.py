"""人格画像值对象 — 9维性格 + 5维说话风格 + 核心锚点"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class PersonaProfile:
    warmth: float = 0.7
    playfulness: float = 0.5
    independence: float = 0.6
    jealousy: float = 0.4
    stubbornness: float = 0.5

    formality: float = 0.3
    emoji_frequency: float = 0.6
    sentence_length: float = 0.5
    emotional_expression: float = 0.7
    humor: float = 0.5

    core_anchors: list[str] = field(default_factory=list)

    _DIMENSIONS: ClassVar[tuple[str, ...]] = (
        "warmth", "playfulness", "independence", "jealousy", "stubbornness",
        "formality", "emoji_frequency", "sentence_length", "emotional_expression", "humor",
    )

    def __post_init__(self):
        for attr in self._DIMENSIONS:
            value = getattr(self, attr)
            setattr(self, attr, max(0.0, min(1.0, value)))

    def to_prompt_segment(self) -> str:
        lines = [
            "【核心性格】",
            f"- 温暖度: {self.warmth:.1f} ({self._describe_warmth()})",
            f"- 调皮度: {self.playfulness:.1f}",
            f"- 独立性: {self.independence:.1f}",
            f"- 吃醋倾向: {self.jealousy:.1f}",
            "",
            "【说话风格】",
            f"- 正式度: {self.formality:.1f}",
            f"- Emoji使用: {self.emoji_frequency:.1f}",
            f"- 情感表达: {self.emotional_expression:.1f}",
        ]
        if self.core_anchors:
            lines.extend(["", "【核心锚点】"] + [f"- {a}" for a in self.core_anchors])
        return "\n".join(lines)

    def _describe_warmth(self) -> str:
        if self.warmth > 0.7:
            return "非常温暖"
        elif self.warmth > 0.4:
            return "温和"
        return "冷淡"

    def to_dict(self) -> dict:
        result = {attr: getattr(self, attr) for attr in self._DIMENSIONS}
        result["core_anchors"] = self.core_anchors
        return result

    @classmethod
    def from_dict(cls, data: dict) -> PersonaProfile:
        return cls(
            **{k: v for k, v in data.items() if k in cls._DIMENSIONS or k == "core_anchors"}
        )

    @classmethod
    def from_emotion_style_map(cls, style_map) -> PersonaProfile:
        return cls(
            warmth=getattr(style_map, "warmth", 0.5),
            playfulness=getattr(style_map, "playfulness", 0.5),
            independence=getattr(style_map, "independence", 0.5),
            jealousy=getattr(style_map, "jealousy", 0.3),
            stubbornness=getattr(style_map, "stubbornness", 0.4),
        )
