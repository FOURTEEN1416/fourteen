"""
人格自动演化引擎

基于现有evolve()/evolve_dimension()扩展，
增加自动演化触发（基于交互模式、情感积累、时间周期），
支持演化约束和渐进式变化。
"""

from __future__ import annotations

import logging
import time as time_mod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from my_character.emotion_engine import CompoundEmotionalState, EmotionEngine
    from my_character.emotion_memory import EmotionMemorySystem
    from my_character.persona_engine import PersonaEngine

logger = logging.getLogger("evolution_engine")


@dataclass
class EvolutionContext:
    emotion_state: Optional[CompoundEmotionalState] = None
    recent_emotion_events: List[Any] = field(default_factory=list)
    chat_round: int = 0
    affinity_change: int = 0


@dataclass
class EvolutionTrigger:
    type: str
    dimension: str
    delta: float
    reason: str


@dataclass
class EvolutionResult:
    triggered: bool
    trigger: Optional[EvolutionTrigger] = None
    dimension: str = ""
    before: float = 0.0
    after: float = 0.0
    delta: float = 0.0
    reason: str = ""


class PersonaEvolutionEngine:
    """人格自动演化引擎"""

    EMOTION_DIMENSION_MAP = {
        "开心": ("playfulness", 0.02),
        "撒娇": ("warmth", 0.02),
        "傲娇": ("stubbornness", 0.01),
        "生气": ("independence", 0.01),
        "伤心": ("warmth", -0.01),
        "温柔": ("warmth", 0.02),
        "吃醋": ("jealousy", 0.02),
    }

    def __init__(
        self,
        persona_engine: Optional[PersonaEngine] = None,
        emotion_engine: Optional[EmotionEngine] = None,
        emotion_memory: Optional[EmotionMemorySystem] = None,
        evolution_config: Optional[Dict] = None,
    ):
        self._persona = persona_engine
        self._emotion = emotion_engine
        self._emotion_memory = emotion_memory
        self._config = evolution_config
        self._cooldown_remaining: int = 0
        self._last_evolution_time: float = 0.0
        self._evolution_history: List[EvolutionResult] = []

    def set_engines(self, persona_engine: PersonaEngine, emotion_engine: EmotionEngine) -> None:
        self._persona = persona_engine
        self._emotion = emotion_engine

    def check_and_evolve(self, context: EvolutionContext) -> Optional[EvolutionResult]:
        """检查是否满足自动演化条件，满足则执行单维度演化

        Args:
            context: 演化上下文（含emotion_state/chat_round/affinity_change等）

        Returns:
            EvolutionResult（触发时）或 None（冷却中/无条件满足时）
        """
        if not self._can_evolve():
            return None

        trigger = self._detect_evolution_trigger(context)
        if trigger is None:
            return None

        result = self._execute_evolution(trigger, context)
        cooldown = 10
        if self._config and hasattr(self._config, "cooldown_rounds"):
            cooldown = self._config.cooldown_rounds
        self._cooldown_remaining = cooldown
        self._last_evolution_time = time_mod.time()

        return result

    def _can_evolve(self) -> bool:
        if self._config and hasattr(self._config, "enabled") and not self._config.enabled:
            return False
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return False
        return True

    def _detect_evolution_trigger(self, context: EvolutionContext) -> Optional[EvolutionTrigger]:
        trigger = self._detect_emotion_pattern_trigger(context)
        if trigger:
            return trigger

        trigger = self._detect_affinity_change_trigger(context)
        if trigger:
            return trigger

        trigger = self._detect_time_period_trigger(context)
        if trigger:
            return trigger

        return None

    def _detect_emotion_pattern_trigger(self, context: EvolutionContext) -> Optional[EvolutionTrigger]:
        if not context.recent_emotion_events:
            if self._emotion_memory and hasattr(self._emotion_memory, "detect_patterns"):
                patterns = self._emotion_memory.detect_patterns(window=50)
                for pattern in patterns:
                    if pattern.frequency > 0.4 and pattern.trend == "increasing":
                        dim_delta = self.EMOTION_DIMENSION_MAP.get(pattern.emotion)
                        if dim_delta:
                            dimension, delta = dim_delta
                            return EvolutionTrigger(
                                type="emotion_pattern",
                                dimension=dimension,
                                delta=delta,
                                reason=f"情感'{pattern.emotion}'频繁触发(f={pattern.frequency:.2f})且呈上升趋势",
                            )
            return None

        emotion_counts: Dict[str, int] = {}
        for event in context.recent_emotion_events:
            emotion = event.emotion if hasattr(event, "emotion") else str(event)
            emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1

        total = len(context.recent_emotion_events)
        for emotion, count in emotion_counts.items():
            if count / total > 0.5:
                dim_delta = self.EMOTION_DIMENSION_MAP.get(emotion)
                if dim_delta:
                    dimension, delta = dim_delta
                    return EvolutionTrigger(
                        type="emotion_pattern",
                        dimension=dimension,
                        delta=delta,
                        reason=f"情感'{emotion}'在近期占主导({count}/{total})",
                    )
        return None

    def _detect_affinity_change_trigger(self, context: EvolutionContext) -> Optional[EvolutionTrigger]:
        if context.affinity_change > 0:
            return EvolutionTrigger(
                type="affinity_change",
                dimension="warmth",
                delta=0.02,
                reason=f"好感度升级(Δ={context.affinity_change})，增强温暖维度",
            )
        return None

    def _detect_time_period_trigger(self, context: EvolutionContext) -> Optional[EvolutionTrigger]:
        if context.chat_round > 0 and context.chat_round % 50 == 0:
            return EvolutionTrigger(
                type="time_period",
                dimension="independence",
                delta=0.01,
                reason=f"长期交互({context.chat_round}轮)，微调独立性",
            )
        return None

    def _execute_evolution(self, trigger: EvolutionTrigger, context: EvolutionContext) -> EvolutionResult:
        if self._persona is None:
            return EvolutionResult(triggered=False, reason="PersonaEngine not set")

        max_delta = 0.05
        if self._config and hasattr(self._config, "max_delta"):
            max_delta = self._config.max_delta

        clamped_delta = max(-max_delta, min(max_delta, trigger.delta))

        protected = ("warmth",)
        if self._config and hasattr(self._config, "protected_dimensions"):
            protected = self._config.protected_dimensions

        if trigger.dimension in protected and abs(clamped_delta) > max_delta * 0.5:
            clamped_delta = max(-max_delta * 0.5, min(max_delta * 0.5, clamped_delta))

        before = 0.5
        if hasattr(self._persona, "get_trait"):
            try:
                before = self._persona.get_trait(trigger.dimension)
            except (KeyError, AttributeError):
                return EvolutionResult(
                    triggered=False,
                    dimension=trigger.dimension,
                    reason=f"维度'{trigger.dimension}'不存在",
                )

        after = max(0.0, min(1.0, before + clamped_delta))

        if hasattr(self._persona, "evolve_dimension"):
            try:
                self._persona.evolve_dimension(trigger.dimension, after, trigger.reason)
            except TypeError:
                try:
                    self._persona.evolve_dimension(trigger.dimension, after, reason=trigger.reason)
                except Exception as e:
                    logger.error("Evolve dimension failed: %s", e)
                    return EvolutionResult(triggered=False, reason=str(e))
        elif hasattr(self._persona, "set_trait"):
            self._persona.set_trait(trigger.dimension, after)

        result = EvolutionResult(
            triggered=True,
            trigger=trigger,
            dimension=trigger.dimension,
            before=round(before, 4),
            after=round(after, 4),
            delta=round(clamped_delta, 4),
            reason=trigger.reason,
        )
        self._evolution_history.append(result)
        if len(self._evolution_history) > 100:
            self._evolution_history = self._evolution_history[-100:]

        logger.info(
            "Evolution: %s %.4f → %.4f (Δ=%.4f) [%s]",
            trigger.dimension, before, after, clamped_delta, trigger.type,
        )
        return result

    def get_evolution_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [
            {
                "dimension": r.dimension,
                "before": r.before,
                "after": r.after,
                "delta": r.delta,
                "reason": r.reason,
                "trigger_type": r.trigger.type if r.trigger else "",
            }
            for r in self._evolution_history[-limit:]
        ]

    def health_check(self) -> dict:
        return {
            "persona_set": self._persona is not None,
            "emotion_set": self._emotion is not None,
            "memory_set": self._emotion_memory is not None,
            "cooldown": self._cooldown_remaining,
            "evolution_count": len(self._evolution_history),
        }
