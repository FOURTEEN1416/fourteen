"""人格应用服务 — 统一接入 shisi 角色聚合根与 prompt_builder。

底层不再直接复用 PersonaEngine 的完整 system prompt，而是：
1. 用 shisi CharacterAggregate + prompt_builder 生成基础角色/状态/记忆 prompt；
2. 把 PersonaEngine 的 5 层人格对齐规则（情感层、风格层、约束层、身份自指、锚点保护）
   作为"注入层"附加到结果中。

对外接口保持不变，PersonaEngine 仍被保留以提供 engine 属性、一致性检查等能力。
"""

from __future__ import annotations

import logging
from typing import Any

from my_character.persona_engine import PersonaEngine
from shisi.core.models.affinity_level import AffinityLevel
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.models.emotion_type import EmotionType
from shisi.core.models.emotional_state import EmotionalState
from shisi.core.models.persona_profile import PersonaProfile as ShisiPersonaProfile
from shisi.core.services import prompt_builder

logger = logging.getLogger("shisi.application.persona_service")


class PersonaService:
    """复用 shisi 角色模型与 prompt_builder，并注入 PersonaEngine 人格对齐规则。"""

    _EMOTION_MAP: dict[str, EmotionType] = {
        "开心": EmotionType.HAPPY,
        "伤心": EmotionType.SAD,
        "生气": EmotionType.ANGRY,
        "撒娇": EmotionType.LOVELY,
        "吃醋": EmotionType.JEALOUS,
        "傲娇": EmotionType.SULLEN,
        "温柔": EmotionType.CARING,
        "调皮": EmotionType.PLAYFUL,
        "疲惫": EmotionType.TIRED,
        "平常": EmotionType.NEUTRAL,
    }

    def __init__(
        self,
        config_loader: Any,
        llm_gateway: Any,
        emotion_engine: Any | None = None,
        **engine_kwargs: Any,
    ) -> None:
        self._engine = PersonaEngine(
            config_loader=config_loader,
            llm_gateway=llm_gateway,
            emotion_engine=emotion_engine,
            **engine_kwargs,
        )

    def build_system_prompt(
        self,
        emotion_state: Any = None,
        memory_context: str = "",
        rag_context: str = "",
        chat_summary: str = "",
        world_info: str = "",
    ) -> str:
        """构建系统提示词。

        流程：
        1. 将 emotion_state 映射为 shisi EmotionalState；
        2. 用 PersonaEngine 的配置构造 CharacterAggregate；
        3. 通过 prompt_builder 生成基础 prompt（角色设定 + 人设 + 状态 + 对话历史）；
        4. 注入 PersonaEngine 的人格对齐规则：世界信息、RAG、情感层、风格层、约束层。
        """
        effective_emotion = (
            emotion_state if emotion_state is not None else self._engine.emotion.state
        )

        character = self._build_character(effective_emotion)
        chat_history = self._build_chat_history(memory_context, chat_summary)

        base_prompt = prompt_builder.build(
            character,
            user_message="",
            chat_history=chat_history,
            use_knowledge=False,
            use_storyline=False,
        )

        injection_parts: list[str] = []

        if world_info:
            injection_parts.append(f"# 世界与时间\n{world_info}")

        if rag_context:
            injection_parts.append(f"# 角色知识库\n{rag_context}")

        emotion_layer = self._safe_engine_layer(
            "emotion", self._engine._build_emotion_layer, effective_emotion
        )
        if emotion_layer:
            injection_parts.append(emotion_layer)

        emotion_style = self._safe_engine_layer(
            "emotion_style", self._engine._build_emotion_style_segment, effective_emotion
        )
        if emotion_style:
            injection_parts.append(emotion_style)

        style_layer = self._safe_engine_layer(
            "style", self._engine._build_style_layer, effective_emotion, "", None
        )
        if style_layer:
            injection_parts.append(style_layer)

        constraint_layer = self._safe_engine_layer(
            "constraint", self._engine._build_constraint_layer
        )
        if constraint_layer:
            injection_parts.append(constraint_layer)

        if not injection_parts:
            return base_prompt

        return f"{base_prompt}\n\n" + "\n\n".join(injection_parts)

    @property
    def engine(self) -> PersonaEngine:
        """暴露底层引擎，供 consistency_checker 等仍依赖 PersonaEngine 的组件使用。"""
        return self._engine

    # ── 内部构建 ──────────────────────────────────────────────

    def _build_character(self, emotion_state: Any) -> CharacterAggregate:
        """根据 PersonaEngine 的配置构造 shisi CharacterAggregate。"""
        name = self._engine.get_name()
        description = self._engine._persona.get("description", "")
        persona = self._build_shisi_persona()
        emotional_state = self._map_emotional_state(emotion_state)

        character = CharacterAggregate(name=name, description=description, persona=persona)
        character.emotional_state = emotional_state
        return character

    def _build_shisi_persona(self) -> ShisiPersonaProfile:
        """将 PersonaEngine 的人格画像映射为 shisi PersonaProfile。"""
        profile = self._engine.profile
        traits = self._engine._persona.get("personality_traits", {})

        def _trait(name: str, fallback: float) -> float:
            if name in traits:
                return float(traits[name])
            return float(getattr(profile, name, fallback) or fallback)

        return ShisiPersonaProfile(
            warmth=_trait("warmth", profile.core_character.get("warmth", 0.7)),
            playfulness=_trait("playfulness", profile.core_character.get("playfulness", 0.5)),
            independence=_trait("independence", profile.core_character.get("independence", 0.6)),
            jealousy=_trait("jealousy", profile.core_character.get("jealousy", 0.4)),
            stubbornness=_trait("stubbornness", profile.core_character.get("stubbornness", 0.5)),
            formality=profile.speaking_style.get("formality", 0.3),
            emoji_frequency=profile.speaking_style.get("emoji_freq", 0.6),
            sentence_length=profile.speaking_style.get("sentence_length", 0.5),
            emotional_expression=profile.speaking_style.get("emotional_expression", 0.7),
            humor=profile.speaking_style.get("humor", 0.5),
            core_anchors=self._engine.get_core_anchors(),
        )

    def _map_emotional_state(self, emotion_state: Any) -> EmotionalState:
        """将 PersonaEngine/外部 emotion_state 映射为 shisi EmotionalState。"""
        if emotion_state is None:
            return EmotionalState()

        if isinstance(emotion_state, dict):
            primary = emotion_state.get("primary_emotion")
            if primary is None:
                primary_obj = emotion_state.get("primary", {})
                primary = primary_obj.get("type", "平常") if isinstance(primary_obj, dict) else primary_obj
            intensity = float(
                emotion_state.get("intensity")
                or emotion_state.get("primary", {}).get("intensity", 0.5)
            )
            energy = float(emotion_state.get("energy", 1.0))
            affinity = emotion_state.get("affinity", 0)
            level = affinity.get("level", 0) if isinstance(affinity, dict) else affinity
            affection_points = float(emotion_state.get("affection_points", 0.0))
        else:
            primary = getattr(emotion_state, "primary_emotion", None)
            if hasattr(primary, "value"):
                primary = primary.value
            elif primary is None:
                primary = "平常"

            intensity = float(
                getattr(emotion_state, "primary_intensity", getattr(emotion_state, "intensity", 0.5))
            )
            energy = float(getattr(emotion_state, "energy", 1.0))
            affinity = getattr(emotion_state, "affinity", 0)
            level = affinity.get("level", 0) if isinstance(affinity, dict) else affinity
            affection_points = float(getattr(emotion_state, "affection_points", 0.0))

        emotion_type = self._EMOTION_MAP.get(str(primary), EmotionType.NEUTRAL)
        affinity_level = AffinityLevel(min(8, max(0, int(level or 0))))

        return EmotionalState(
            primary_emotion=emotion_type,
            intensity=intensity,
            energy=energy,
            affinity_level=affinity_level,
            affection_points=affection_points,
        )

    def _build_chat_history(self, memory_context: Any, chat_summary: str) -> str:
        """将记忆上下文与对话摘要格式化为 prompt_builder 可用的 chat_history 字符串。"""
        if isinstance(memory_context, dict):
            return self._engine._build_memory_layer(memory_context, chat_summary)

        parts: list[str] = []
        if chat_summary:
            parts.append(f"## 早期对话摘要\n{chat_summary}")
        if memory_context:
            parts.append(f"## 最近对话\n{memory_context}")
        return "\n\n".join(parts)

    def _safe_engine_layer(self, name: str, builder: Any, *args: Any, **kwargs: Any) -> str:
        """安全调用 PersonaEngine 的私有构建方法，失败时返回空字符串并记录日志。"""
        try:
            return builder(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.debug("注入层 %s 构建失败（非阻塞）", name, exc_info=True)
            return ""
