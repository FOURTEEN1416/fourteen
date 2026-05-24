"""
增强提示词引擎 — 6层架构

从PersonaEngine的5层架构扩展到6层，新增"情境化行为层"，
深度集成EmotionStyleCoupler的风格指导和锚点强化。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from my_character.emotion_engine import CompoundEmotionalState
    from my_character.emotion_style_coupler import EmotionStyleCoupler
    from my_character.style_enhancer_v2 import StyleEnhancerV2
    from my_character.contextual_behavior import ContextualBehavior
    from my_character.dynamic_anchor import DynamicAnchorSystem
    from my_character.persona_engine import PersonaEngine

logger = logging.getLogger("enhanced_prompt_engine")


@dataclass
class TimeContext:
    hour: int = 12
    period: str = "afternoon"
    is_weekend: bool = False
    is_holiday: bool = False
    holiday_name: str = ""

    @classmethod
    def now(cls) -> TimeContext:
        now = datetime.now()
        hour = now.hour
        if 6 <= hour < 9:
            period = "morning"
        elif 9 <= hour < 12:
            period = "forenoon"
        elif 12 <= hour < 14:
            period = "afternoon"
        elif 14 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 22:
            period = "evening"
        elif 22 <= hour < 24:
            period = "night"
        else:
            period = "late_night"
        is_weekend = now.weekday() >= 5
        return cls(hour=hour, period=period, is_weekend=is_weekend)


@dataclass
class PromptContext:
    emotion_state: Optional[CompoundEmotionalState] = None
    memory_context: Optional[Dict] = None
    chat_history: str = ""
    chat_summary: str = ""
    rag_context: str = ""
    user_input: str = ""
    few_shot_examples: Optional[List[str]] = None
    style_prompt: str = ""
    chat_round: int = 0
    time_context: Optional[TimeContext] = None
    character_overrides: Optional[Dict] = None

    @property
    def emotion_dict(self) -> Dict[str, Any]:
        if self.emotion_state is None:
            return {}
        if hasattr(self.emotion_state, "to_dict"):
            return self.emotion_state.to_dict()
        return {}

    @property
    def anchor_context(self) -> Any:
        try:
            from my_character.persona_utils import build_anchor_context
            result = build_anchor_context(
                emotion_state=self.emotion_state,
                chat_round=self.chat_round,
            )
            if result is not None:
                return result
        except ImportError:
            pass
        from my_character.dynamic_anchor import AnchorContext
        return AnchorContext(
            affinity=0, energy=1.0, emotion_type="平常", chat_round=self.chat_round,
        )


@dataclass
class CouplerFeedback:
    suggested_intensity_adjustment: float = 0.0
    suggested_evolution_direction: Dict[str, float] = field(default_factory=dict)


class EnhancedPromptEngine:
    """增强提示词引擎 — 6层架构"""

    LAYER_BASE = "base"
    LAYER_EMOTION = "emotion"
    LAYER_MEMORY = "memory"
    LAYER_STYLE = "style"
    LAYER_CONSTRAINT = "constraint"
    LAYER_CONTEXTUAL = "contextual"

    def __init__(
        self,
        persona_engine: Optional[PersonaEngine] = None,
        style_coupler: Optional[EmotionStyleCoupler] = None,
        style_enhancer: Optional[StyleEnhancerV2] = None,
        contextual_behavior: Optional[ContextualBehavior] = None,
        dynamic_anchors: Optional[DynamicAnchorSystem] = None,
        constraint_validator: Optional[Any] = None,
    ):
        self._persona = persona_engine
        self._coupler = style_coupler
        self._style_enhancer = style_enhancer
        self._contextual = contextual_behavior
        self._anchors = dynamic_anchors
        self._validator = constraint_validator

    def build_prompt(self, context: PromptContext) -> str:
        """构建6层架构的完整提示词

        Args:
            context: 提示词构建上下文

        Returns:
            6层提示词拼接结果（层间以双换行分隔），空层自动跳过
        """
        layers = [
            self._build_base_layer(context),
            self._build_emotion_layer(context),
            self._build_memory_layer(context),
            self._build_style_layer(context),
            self._build_constraint_layer(context),
            self._build_contextual_layer(context),
        ]

        if self._anchors and self._anchors.should_reinforce():
            layers.append(self._build_anchor_reinforcement_layer(context))

        return "\n\n".join(layer for layer in layers if layer)

    def _build_base_layer(self, context: PromptContext) -> str:
        if self._persona and hasattr(self._persona, "_persona"):
            persona_data = self._persona._persona
            name = persona_data.get("name", "十四")
            anchors = persona_data.get("anchors", [])
            anchor_text = "\n".join(f"- {a}" for a in anchors[:8]) if anchors else ""
            if anchor_text:
                return f"# 角色设定\n你是{name}。\n\n## 核心性格\n{anchor_text}"
        return ""

    def _build_emotion_layer(self, context: PromptContext) -> str:
        if context.emotion_state is None:
            return ""
        emotion_dict = context.emotion_dict
        if not emotion_dict:
            return ""
        primary = emotion_dict.get("primary_emotion", "平常")
        intensity = emotion_dict.get("primary_intensity", 0.5)
        energy = emotion_dict.get("energy", 1.0)
        affinity = emotion_dict.get("affinity", 0)
        return (
            f"# 当前情感状态\n"
            f"当前情感：{primary}（强度：{intensity:.1f}）\n"
            f"能量水平：{energy:.1f}\n"
            f"好感度等级：{affinity}\n"
            f"请根据以上情感状态调整回复的语气和情感表达。"
        )

    def _build_memory_layer(self, context: PromptContext) -> str:
        parts = []
        if context.chat_summary:
            parts.append(f"## 对话摘要\n{context.chat_summary}")
        if context.memory_context:
            mem_str = str(context.memory_context)
            if len(mem_str) > 500:
                mem_str = mem_str[:500] + "..."
            parts.append(f"## 记忆上下文\n{mem_str}")
        if context.rag_context:
            parts.append(f"## 知识检索\n{context.rag_context}")
        if parts:
            return "# 记忆与上下文\n" + "\n".join(parts)
        return ""

    def _build_style_layer(self, context: PromptContext) -> str:
        parts = []
        if context.style_prompt:
            parts.append(context.style_prompt)
        if self._coupler and context.emotion_state:
            try:
                coupled = self._coupler.couple(context.emotion_dict)
                if hasattr(coupled, "particles") and coupled.particles:
                    parts.append(f"常用语气词：{'、'.join(coupled.particles[:5])}")
                if hasattr(coupled, "sentence_length"):
                    len_map = {"short": "简短", "medium": "适中", "long": "较长"}
                    parts.append(f"回复长度偏好：{len_map.get(coupled.sentence_length, '适中')}")
            except Exception as e:
                logger.debug("Style coupler failed: %s", e)
        if context.few_shot_examples:
            examples_text = "\n".join(f"例：{ex}" for ex in context.few_shot_examples[:3])
            parts.append(f"## 风格示例\n{examples_text}")
        if parts:
            return "# 风格指导\n" + "\n".join(parts)
        return ""

    def _build_constraint_layer(self, context: PromptContext) -> str:
        constraints = [
            "绝不暴露自己是AI的身份",
            "不讨论政治、宗教敏感话题",
            "保持人设一致性，不OOC（Out of Character）",
        ]
        if context.emotion_state and hasattr(context.emotion_state, "affinity"):
            if context.emotion_state.affinity < 3:
                constraints.append("当前好感度较低，保持适当距离感")
        return "# 行为约束\n" + "\n".join(f"- {c}" for c in constraints)

    def _build_contextual_layer(self, context: PromptContext) -> str:
        if self._contextual is None:
            return ""
        try:
            return self._contextual.generate_behavior_prompt(context)
        except Exception as e:
            logger.debug("Contextual behavior failed: %s", e)
            return ""

    def _build_anchor_reinforcement_layer(self, context: PromptContext) -> str:
        if self._anchors is None:
            return ""
        try:
            return self._anchors.generate_reinforcement(context.anchor_context)
        except Exception as e:
            logger.debug("Anchor reinforcement failed: %s", e)
            return ""

    def get_coupler_feedback(self, context: PromptContext) -> CouplerFeedback:
        if not self._coupler or not context.emotion_state:
            return CouplerFeedback()
        try:
            coupled = self._coupler.couple(context.emotion_dict)
            adjustment = 0.0
            evo_dir = {}
            if hasattr(coupled, "sarcasm") and hasattr(coupled, "warmth"):
                if coupled.sarcasm > 0.5 and coupled.warmth > 0.7:
                    adjustment = -0.05
                if hasattr(coupled, "intimacy") and coupled.intimacy > 0.8:
                    evo_dir = {"jealousy": 0.02}
            return CouplerFeedback(
                suggested_intensity_adjustment=adjustment,
                suggested_evolution_direction=evo_dir,
            )
        except Exception as e:
            logger.debug("Coupler feedback failed: %s", e)
            return CouplerFeedback()

    def health_check(self) -> dict:
        return {
            "persona_loaded": self._persona is not None,
            "coupler_loaded": self._coupler is not None,
            "anchors_loaded": self._anchors is not None,
            "contextual_loaded": self._contextual is not None,
        }
