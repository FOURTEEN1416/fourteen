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
        """卡设静态人设段。

        十维全部渲染（旧实现漏渲染 stubbornness / sentence_length / humor →
        前端可调、卡里可存，却对生成零影响 = 假滑块）。
        优先级契约：**本段是卡设基准**，`[当前风格指导]`（情绪-风格耦合段）
        在其之后注入，只作为**当轮情绪叠加**；两者不矛盾时叠加生效，
        矛盾时以耦合段表达的当轮指导为准（因为它更贴近本轮）。
        """
        lines = [
            "【核心性格】",
            f"- 温暖度: {self.warmth:.1f} ({self._describe_warmth()})",
            f"- 调皮度: {self.playfulness:.1f}",
            f"- 独立性: {self.independence:.1f}",
            f"- 吃醋倾向: {self.jealousy:.1f}",
            f"- 固执度: {self.stubbornness:.1f} ({self._describe_stubbornness()})",
            "",
            "【说话风格】",
            f"- 正式度: {self.formality:.1f}",
            f"- 表情符号使用: {self._describe_emoji()}（{self.emoji_frequency:.1f}）",
            f"- 句长: {self.sentence_length:.1f} ({self._describe_sentence_length()})",
            f"- 情感表达: {self.emotional_expression:.1f}",
            f"- 幽默感: {self.humor:.1f} ({self._describe_humor()})",
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

    def _describe_stubbornness(self) -> str:
        if self.stubbornness > 0.7:
            return "很固执，认定的事不易改口"
        if self.stubbornness < 0.3:
            return "很随和，你说什么她都顺着你"
        return "有主见但不拧"

    def _describe_sentence_length(self) -> str:
        bucket = self.sentence_length_bucket()
        if bucket == "short":
            return "句子偏短，一两句说完"
        if bucket == "long":
            return "句子偏长，可以多说几句"
        return "句子长短适中，跟着话题走"

    def sentence_length_bucket(self) -> str:
        """数值句长 → 耦合器档位（short/medium/long）。

        阈值唯一 owner 在此：静态人设段与情绪-风格耦合段必须同一档位，
        否则同一张卡会出现「卡设说短、当轮指导说适中」的自我矛盾。
        """
        if self.sentence_length < 0.34:
            return "short"
        if self.sentence_length > 0.66:
            return "long"
        return "medium"

    def _describe_humor(self) -> str:
        if self.humor > 0.7:
            return "爱开玩笑，可以贫嘴"
        if self.humor < 0.3:
            return "不太开玩笑，说话认真"
        return "偶尔打趣一下"

    def _describe_emoji(self) -> str:
        """emoji_frequency 数值 → 明确的文字指令（裸数字对 LLM 无约束力）"""
        if self.emoji_frequency > 0.7:
            return "可以适当使用，每条最多两个"
        if self.emoji_frequency < 0.3:
            return "不使用表情符号"
        return "每条最多一个，仅在情绪强烈时使用"

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
