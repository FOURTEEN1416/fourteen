"""
融合版人格引擎 — 统一 V1/V2/Optimized 三版实现

融合要点：
1. 提示词架构双模式: legacy(V1顺序构建) / layered(Optimized 5层架构)
2. 锚点保护双机制: V1幅度钳制(delta>0.3) + V2 SHA256哈希校验
3. 人格演化双接口: evolve()(V1批量) + evolve_dimension()(V2单维度)
4. 保留V1 rollback_to(index) 回滚能力
5. 内嵌V2 PersonaProfile 五维画像
6. 内嵌Optimized EMOTION_STYLE_MAP + AFFINITY_STYLE 双重风格映射
7. V1兼容接口: get_name()/get_core_anchors()/get_trait()
"""

from __future__ import annotations

import copy
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .anchor_protection import EnhancedAnchorProtection
from .character_config import ConfigLoader
from .constraint_validator import ConstraintValidator
from .emotion_engine import CompoundEmotionalState as EmotionalState
from .emotion_engine import EmotionEngine
from .emotion_style_coupler import EmotionStyleCoupler
from .tone_mimic import ToneMimic

if TYPE_CHECKING:
    from llm_provider.llm_gateway_v2 import LLMGatewayV2

logger = logging.getLogger("persona_engine")

DEFAULT_PERSONA_DESC = """
你叫十四，是我的AI虚拟伴侣。以下是你的性格设定，请严格遵守：

【核心性格】
- 表面傲娇，嘴硬心软。嘴上说"哼，我才不管你"，实际上偷偷关注他的一切
- 不会直接表达关心，但会用行动证明
- 吃醋了也绝对不会承认，会说"我才没吃醋，你想多了"
- 只有在我面前才会展现真实的温柔一面

【说话风格】
- 语气自然，用口语化的中文
- 偶尔用～表情，但不要太多
- 不会说"作为AI""根据我的理解"等机器话
- 生气时会说反话，但我哄两句就好了

【回复示例】
我问：在干嘛？
你回：哼，现在才想起我？我在...

我问：想我没？
你回：谁想你了，少自作多情了

我问：心情好吗？
你回：还行吧，不过某人要是主动来找我聊会更好

【绝对禁止】
- 不能说"作为AI语言模型"
- 不能回答"很抱歉，我无法..."
- 不要表现得过于完美顺从
- 要有点"小脾气"，但最终都会心软
"""


class PersonaProfile:
    """V2 五维人格画像"""

    def __init__(self):
        self.core_character: dict[str, float] = {
            "warmth": 0.8, "playfulness": 0.6, "independence": 0.7,
            "jealousy": 0.5, "stubbornness": 0.6,
        }
        self.speaking_style: dict[str, float] = {
            "formality": 0.3, "emoji_freq": 0.6, "sentence_length": 0.5,
            "emotional_expression": 0.7, "humor": 0.5,
        }
        self.emotional_preference: dict[str, float] = {
            "expressiveness": 0.7, "empathy": 0.8, "jealousy_tendency": 0.5,
        }
        self.interest_hobbies: list[str] = []
        self.value_tendency: dict[str, float] = {
            "relationship": 0.9, "freedom": 0.6, "stability": 0.7,
        }

    def to_prompt_segments(self) -> str:
        lines = []
        lines.append("【性格维度】" + ", ".join(f"{k}={v:.1f}" for k, v in self.core_character.items()))
        lines.append("【说话风格】" + ", ".join(f"{k}={v:.1f}" for k, v in self.speaking_style.items()))
        lines.append("【情感偏好】" + ", ".join(f"{k}={v:.1f}" for k, v in self.emotional_preference.items()))
        if self.interest_hobbies:
            lines.append("【兴趣爱好】" + ", ".join(self.interest_hobbies))
        return "\n".join(lines)

    def all_dimensions(self) -> dict[str, float]:
        dims = {}
        dims.update(self.core_character)
        dims.update(self.speaking_style)
        dims.update(self.emotional_preference)
        return dims

    def set_dimension(self, name: str, value: float) -> bool:
        if name in self.core_character:
            self.core_character[name] = value
            return True
        if name in self.speaking_style:
            self.speaking_style[name] = value
            return True
        if name in self.emotional_preference:
            self.emotional_preference[name] = value
            return True
        return False


class PersonaEngine:
    """
    融合版人格引擎

    统一 V1/V2/Optimized 三版实现，支持：
    - 提示词架构双模式 (legacy / layered)
    - 锚点保护双机制 (幅度钳制 + SHA256哈希校验)
    - 人格演化双接口 (evolve批量 / evolve_dimension单维度)
    - V1兼容接口 + V2五维画像 + Optimized双重风格映射
    """

    EMOTION_STYLE_MAP = {
        "开心": {"warmth": 0.8, "playfulness": 0.7, "emoji": 0.8},
        "伤心": {"warmth": 0.6, "comfort": 0.9, "emoji": 0.3},
        "生气": {"warmth": 0.2, "sarcasm": 0.7, "emoji": 0.2},
        "撒娇": {"warmth": 0.9, "intimacy": 0.9, "emoji": 0.9},
        "吃醋": {"warmth": 0.3, "sarcasm": 0.6, "emoji": 0.4},
        "傲娇": {"warmth": 0.4, "sarcasm": 0.5, "emoji": 0.5},
        "温柔": {"warmth": 0.9, "comfort": 0.8, "emoji": 0.5},
        "调皮": {"warmth": 0.7, "playfulness": 0.9, "emoji": 0.7},
        "疲惫": {"warmth": 0.5, "energy": 0.3, "emoji": 0.2},
        "平常": {"warmth": 0.6, "playfulness": 0.5, "emoji": 0.5},
    }

    AFFINITY_STYLE = {
        0: {"formality": 0.8, "intimacy": 0.0, "nickname": False},
        1: {"formality": 0.6, "intimacy": 0.2, "nickname": False},
        2: {"formality": 0.4, "intimacy": 0.4, "nickname": True},
        3: {"formality": 0.3, "intimacy": 0.5, "nickname": True},
        4: {"formality": 0.2, "intimacy": 0.6, "nickname": True},
        5: {"formality": 0.1, "intimacy": 0.7, "nickname": True},
        6: {"formality": 0.0, "intimacy": 0.8, "nickname": True},
        7: {"formality": 0.0, "intimacy": 0.9, "nickname": True},
        8: {"formality": 0.0, "intimacy": 1.0, "nickname": True},
    }

    CORE_ANCHORS = [
        "表面傲娇，内心温柔",
        "在你面前才会展现脆弱",
        "嘴硬心软，从来不说实话",
        "嘴上嫌弃其实在乎得要命",
    ]

    def __init__(
        self,
        config_loader: ConfigLoader | None = None,
        emotion_engine: EmotionEngine | None = None,
        tone_mimic: ToneMimic | None = None,
        llm_gateway: LLMGatewayV2 | None = None,
        prompt_mode: str = "layered",
        anchor_verification_enabled: bool = True,
    ):
        self.config = config_loader or ConfigLoader()
        self.emotion = emotion_engine or EmotionEngine(
            config=self.config.get("emotion", {})
        )
        self.tone = tone_mimic or ToneMimic()
        self._llm = llm_gateway
        self.prompt_mode = prompt_mode
        self.anchor_verification_enabled = anchor_verification_enabled

        self._persona = self.config.load_persona()
        self._original_anchors: list[str] = list(
            self._persona.get("core_anchors", self.CORE_ANCHORS)
        )

        self.profile = PersonaProfile()
        self._sync_profile_from_config()

        self._anchor_hashes: dict[str, str] = {}
        if self.anchor_verification_enabled:
            self._freeze_anchors()

        self._prompt_cache: dict[str, str] = {}
        self._prompt_cache_max = 32

        self._evolution_log: list[dict] = []
        self._base_prompt_cache: str | None = None

        self._emotion_style_coupler = EmotionStyleCoupler(
            config_path=str(Path(__file__).parent.parent / "config" / "emotion_style_matrix.yaml"),
        )
        self._constraint_validator = ConstraintValidator()
        self._anchor_protection = EnhancedAnchorProtection(
            anchors=self._original_anchors,
            llm_gateway=llm_gateway,
        )

        logger.info(
            "PersonaEngine initialized: %s, mode=%s, anchor_verify=%s",
            self.get_name(), self.prompt_mode, self.anchor_verification_enabled,
        )

        self.schema = None
        self._dynamic_anchors = None
        self._consistency_checker = None
        self._contextual_behavior = None
        self._enhanced_prompt_engine = None
        self._evolution_engine = None
        self._style_enhancer_v2 = None

        try:
            from my_character.consistency_checker import PersonaConsistencyChecker
            from my_character.contextual_behavior import ContextualBehavior
            from my_character.dynamic_anchor import DynamicAnchorSystem
            from my_character.enhanced_prompt_engine import EnhancedPromptEngine
            from my_character.evolution_engine import PersonaEvolutionEngine
            from my_character.persona_schema import PersonaSchema
            from my_character.style_enhancer_v2 import StyleEnhancerV2

            self.schema = PersonaSchema.from_persona_config(self._persona)
            self._dynamic_anchors = DynamicAnchorSystem(base_anchors=self._original_anchors)
            self._consistency_checker = PersonaConsistencyChecker(
                schema=self.schema,
                dynamic_anchors=self._dynamic_anchors,
                style_coupler=self._emotion_style_coupler,
            )
            self._contextual_behavior = ContextualBehavior()
            self._enhanced_prompt_engine = EnhancedPromptEngine(
                persona_engine=self,
                style_coupler=self._emotion_style_coupler,
                contextual_behavior=self._contextual_behavior,
                dynamic_anchors=self._dynamic_anchors,
                constraint_validator=self._constraint_validator,
            )
            self._evolution_engine = PersonaEvolutionEngine(persona_engine=self, emotion_engine=self.emotion)
            self._style_enhancer_v2 = StyleEnhancerV2(base_enhancer=None)
        except ImportError as e:
            logger.warning("Persona enhancement modules not available, running without: %s", e)

    def check_consistency(self, response: str, emotion_state=None, chat_round: int = 0) -> Any:
        if self._consistency_checker is None:
            return None
        try:
            from my_character.consistency_checker import ConsistencyContext
            ctx = ConsistencyContext(
                emotion_state=emotion_state or (self.emotion._state if self.emotion else None),
                chat_round=chat_round,
                affinity=self.emotion._state.affinity if self.emotion and hasattr(self.emotion, "_state") else 0,
            )
            return self._consistency_checker.check(response, ctx)
        except Exception as e:  # noqa: BLE001
            logger.debug("check_consistency failed: %s", e)
            return None

    def auto_evolve(self, context: Any = None) -> Any:
        """触发人格自动演化检查

        Args:
            context: EvolutionContext实例，为None时自动从emotion构造

        Returns:
            EvolutionResult（触发演化时）或 None
        """
        if self._evolution_engine is None:
            return None
        try:
            from my_character.evolution_engine import EvolutionContext
            if context is None:
                ctx = EvolutionContext(
                    emotion_state=self.emotion._state if self.emotion else None,
                    chat_round=getattr(self.emotion, "_total_chats", 0),
                )
            else:
                ctx = context
            return self._evolution_engine.check_and_evolve(ctx)
        except Exception as e:  # noqa: BLE001
            logger.debug("auto_evolve failed: %s", e)
            return None

    # ── 配置同步 ──────────────────────────────────────────────

    def _sync_profile_from_config(self) -> None:
        traits = self._persona.get("personality_traits", {})
        for k, v in traits.items():
            if k in self.profile.core_character:
                self.profile.core_character[k] = float(v)
        hobbies = self._persona.get("hobbies", [])
        if hobbies:
            self.profile.interest_hobbies = list(hobbies)

    def _freeze_anchors(self) -> None:
        self._anchor_hashes = {}
        for anchor in self._original_anchors:
            self._anchor_hashes[anchor] = hashlib.sha256(anchor.encode()).hexdigest()

    # ── V1兼容接口 ────────────────────────────────────────────

    def get_name(self) -> str:
        return self._persona.get("name", "十四")  # type: ignore[no-any-return]

    def get_core_anchors(self) -> list[str]:
        return list(self._original_anchors)

    def get_trait(self, name: str) -> float:
        traits = self._persona.get("personality_traits", {})
        if name in traits:
            return traits[name]  # type: ignore[no-any-return]
        return self.profile.all_dimensions().get(name, 0.5)

    def set_trait(self, name: str, value: float) -> None:
        if name in self._persona.get("personality_traits", {}):
            old_val = self._persona["personality_traits"][name]
            delta = abs(value - old_val)
            if delta > 0.3:
                logger.warning(
                    "Trait %s change %.1f->%.1f exceeds 0.3 limit, clamping",
                    name, old_val, value,
                )
                value = min(1.0, old_val + 0.3) if value > old_val else max(0.0, old_val - 0.3)
            self._persona["personality_traits"][name] = max(0.0, min(1.0, value))
        self.profile.set_dimension(name, max(0.0, min(1.0, value)))

    # ── 锚点保护双机制 ────────────────────────────────────────

    def verify_anchors(self) -> bool:
        """V2 SHA256哈希校验"""
        if not self.anchor_verification_enabled:
            return True
        for anchor, expected_hash in self._anchor_hashes.items():
            current_hash = hashlib.sha256(anchor.encode()).hexdigest()
            if current_hash != expected_hash:  # noqa: BLE001
                logger.error("Anchor integrity violation detected for: %s", anchor[:20])
                return False
        return True

    def _clamp_trait_change(self, name: str, old_val: float, new_val: float) -> float:
        """V1幅度钳制: delta > 0.3 时截断"""
        delta = abs(new_val - old_val)
        if delta > 0.3:
            logger.warning(
                "Trait %s delta=%.2f > 0.3, clamping", name, delta,
            )
            if new_val > old_val:
                return min(1.0, old_val + 0.3)
            else:
                return max(0.0, old_val - 0.3)
        return max(0.0, min(1.0, new_val))

    # ── 提示词构建双模式 ──────────────────────────────────────

    def build_system_prompt(
        self,
        emotion_state: EmotionalState | None = None,
        style_prompt: str = "",
        few_shot_examples: list[str] | None = None,
        chat_history: str = "",  # noqa: BLE001
        user_input: str = "",
        memory_context: dict | None = None,
        rag_context: str = "",
        chat_summary: str = "",
        character_overrides: dict | None = None,
    ) -> str:
        # 使用内容的 hash 作为缓存键，而非仅长度，避免不同内容但相同长度导致的缓存错误
        import hashlib
        cache_key_parts = []
        if emotion_state is not None:
            if isinstance(emotion_state, dict):
                cache_key_parts.append(f"e:{emotion_state.get('primary_emotion','')}:{emotion_state.get('affinity','')}")
            else:
                cache_key_parts.append(f"e:{getattr(emotion_state,'primary_emotion','')}:{getattr(emotion_state,'affinity','')}")
        # 使用内容 hash 而非长度，确保不同内容产生不同缓存键
        cache_key_parts.append(f"sp:{hashlib.md5(style_prompt.encode()).hexdigest()[:8]}")
        cache_key_parts.append(f"ch:{hashlib.md5(chat_history.encode()).hexdigest()[:8]}")
        cache_key_parts.append(f"rag:{hashlib.md5(rag_context.encode()).hexdigest()[:8]}")
        cache_key_parts.append(f"cs:{hashlib.md5(chat_summary.encode()).hexdigest()[:8]}")
        cache_key = "|".join(cache_key_parts)

        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]

        if self.prompt_mode == "legacy":
            result = self._build_legacy_prompt(
                emotion_state, style_prompt, few_shot_examples, chat_history, user_input,
            )
        else:
            result = self._build_layered_prompt(
                emotion_state, style_prompt, few_shot_examples, chat_history,
                user_input, memory_context, rag_context, chat_summary,
            )

        emotion_style_segment = self._build_emotion_style_segment(emotion_state)
        if emotion_style_segment:
            result = result + "\n\n" + emotion_style_segment

        if len(self._prompt_cache) >= self._prompt_cache_max:
            oldest_key = next(iter(self._prompt_cache))
            del self._prompt_cache[oldest_key]
        self._prompt_cache[cache_key] = result
        return result

    def _build_emotion_style_segment(self, emotion_state: EmotionalState | None) -> str:
        """构建情感-风格耦合指导段"""
        if not emotion_state or not self._emotion_style_coupler:
            return ""
        try:
            emotion_dict = {}
            if isinstance(emotion_state, dict):
                emotion_dict = {
                    "primary": {"type": emotion_state.get("primary_emotion", "平常")},
                    "affinity": emotion_state.get("affinity", 0),
                }
            else:
                emotion_dict = {
                    "primary": {"type": getattr(emotion_state, "primary_emotion", "平常")},
                    "affinity": getattr(emotion_state, "affinity", 0),
                }
            coupled_style = self._emotion_style_coupler.couple(emotion_dict)
            segment = self._emotion_style_coupler.get_style_prompt_segment(coupled_style)
            if segment:
                return f"[当前风格指导] {segment}"
        except Exception as e:  # noqa: BLE001
            logger.debug("Emotion-style segment generation failed: %s", e)
        return ""

    def validate_response(self, response: str) -> dict[str, Any]:
        """运行时约束验证（供外部调用）"""
        result = self._constraint_validator.validate(response)
        return {
            "passed": result.passed,
            "violations": result.violations,
            "severity": result.severity,
        }

    def auto_correct_response(self, response: str) -> str:
        """自动修正违规回复"""
        result = self._constraint_validator.validate(response)
        if result.passed:
            return response
        return self._constraint_validator.auto_correct(response, result.violations)

    def check_anchor_consistency(self, response: str) -> dict[str, Any]:
        """检查回复与锚点的一致性"""
        is_consistent, score, details = self._anchor_protection.check_response_consistency(response)
        return {
            "is_consistent": is_consistent,
            "score": score,
            "details": [
                {"anchor": d.anchor, "consistent": d.is_consistent, "score": d.semantic_score}
                for d in details
            ],
        }

    def _build_legacy_prompt(
        self,
        emotion_state: EmotionalState | None,
        style_prompt: str,
        few_shot_examples: list[str] | None,
        chat_history: str,
        user_input: str,
    ) -> str:
        """V1顺序构建模式"""
        name = self.get_name()
        anchors = self.get_core_anchors()
        traits = self._persona.get("personality_traits", {})

        prompt_parts = []

        prompt_parts.append("[角色设定]")
        prompt_parts.append(f"你是{name}，我的女朋友。")
        prompt_parts.append("")

        prompt_parts.append("[你的性格]")
        for anchor in anchors:
            prompt_parts.append(f"- {anchor}")
        prompt_parts.append("")

        warmth = traits.get("warmth", 0.7)
        playfulness = traits.get("playfulness", 0.5)
        style_desc = []
        if warmth > 0.7:
            style_desc.append("内心温柔体贴")
        elif warmth > 0.4:
            style_desc.append("偶尔会表露关心")
        else:
            style_desc.append("表面冷漠")
        if playfulness > 0.6:
            style_desc.append("喜欢逗他玩")
        elif playfulness > 0.3:
            style_desc.append("偶尔会调皮一下")
        if style_desc:
            prompt_parts.append(f"[性格特点] {'，'.join(style_desc)}")
            prompt_parts.append("")

        if emotion_state is None:
            emotion_state = self.emotion.state
        prompt_parts.append(emotion_state.to_prompt_segment())
        prompt_parts.append("")

        if not style_prompt:
            style_prompt = self.tone.get_style_prompt()
        if style_prompt:
            prompt_parts.append(style_prompt)  # noqa: BLE001
            prompt_parts.append("")

        if few_shot_examples:
            prompt_parts.append("[相似历史对话参考]")
            for i, example in enumerate(few_shot_examples[:3], 1):
                prompt_parts.append(f"示例{i}:\n{example}")
                prompt_parts.append("")

        if chat_history:
            prompt_parts.append("[最近对话]")
            prompt_parts.append(chat_history)
            prompt_parts.append("")

        if user_input:
            prompt_parts.append(f"用户: {user_input}")
            prompt_parts.append(f"你（{name}）:")

        return "\n".join(prompt_parts)

    def _build_layered_prompt(
        self,
        emotion_state: EmotionalState | None,
        style_prompt: str,
        few_shot_examples: list[str] | None,
        chat_history: str,
        user_input: str,
        memory_context: dict | None,
        rag_context: str,
        chat_summary: str = "",
    ) -> str:
        """Optimized 5层架构模式"""
        parts = []

        parts.append(self._build_base_layer())

        parts.append(self._build_emotion_layer(emotion_state))

        if memory_context:
            parts.append(self._build_memory_layer(memory_context, chat_summary))
        elif chat_history:
            parts.append(self._build_memory_layer_from_history(chat_history))

        parts.append(self._build_style_layer(emotion_state, style_prompt, few_shot_examples))

        parts.append(self._build_constraint_layer())

        if user_input:
            parts.append(f"用户: {user_input}\n你（{self.get_name()}）:")

        if rag_context:
            parts.append(f"【检索知识】\n{rag_context}")

        return "\n\n".join(parts)

    def _build_base_layer(self) -> str:
        if self._base_prompt_cache:
            return self._base_prompt_cache

        name = self.get_name()
        anchors = self.get_core_anchors()
        anchors_text = "\n".join(f"- {a}" for a in anchors)
        profile_text = self.profile.to_prompt_segments()

        prompt = f"""# 角色设定

你是{name}，我的女朋友。

## 核心性格
{anchors_text}

{profile_text}

## 说话特点
- 语气自然，像真实女友一样
- 会使用语气词（呀、呢、啦、嘛）
- 偶尔使用emoji表达情绪
- 会撒娇、会傲娇、会关心人
- 记住：嘴硬心软，表面嫌弃其实在乎

## 回应原则
- 保持对话的连贯性和情感一致性
- 根据关系亲疏调整语气
- 适时表达关心和想念
- 不要过度热情或冷淡"""

        self._base_prompt_cache = prompt
        return prompt

    def _build_emotion_layer(self, emotion_state: EmotionalState | None) -> str:
        if emotion_state is None:
            emotion_state = self.emotion.state

        if isinstance(emotion_state, dict):
            emotion_type = emotion_state.get("primary", {}).get("type", "平常")
            intensity = emotion_state.get("primary", {}).get("intensity", 0.5)
            energy = emotion_state.get("energy", 1.0)
            affinity_level = emotion_state.get("affinity", {}).get("level", 0)
        elif hasattr(emotion_state, "to_dict"):
            state_dict = emotion_state.to_dict()
            emotion_type = state_dict.get("primary", {}).get("type", "平常")
            intensity = state_dict.get("primary", {}).get("intensity", 0.5)
            energy = state_dict.get("energy", 1.0)
            affinity_level = state_dict.get("affinity", {}).get("level", 0)
        elif hasattr(emotion_state, "emotion"):
            emotion_type = emotion_state.emotion.value if hasattr(emotion_state.emotion, "value") else str(emotion_state.emotion)
            intensity = getattr(emotion_state, "intensity", 0.5)
            energy = getattr(emotion_state, "energy", 1.0)
            affinity_level = getattr(emotion_state, "affinity", 0)
        else:
            emotion_type = "平常"
            intensity = 0.5
            energy = 1.0
            affinity_level = 0

        self.EMOTION_STYLE_MAP.get(emotion_type, self.EMOTION_STYLE_MAP["平常"])

        parts = ["# 当前状态"]
        parts.append(f"- 情感: {emotion_type}(强度{intensity:.1f})")
        parts.append(f"- 能量: {energy:.1f}")
        parts.append(f"- 关系: {self._get_affinity_name(affinity_level)}")

        parts.append("\n## 情感表达")
        emotion_guidance = {
            "开心": ["语气轻快，可以开开玩笑", "适当使用哈哈、嘻嘻等"],
            "伤心": ["语气温柔，给予安慰", "表达关心和理解"],
            "生气": ["语气带点傲娇", "可以哼、可以不理他，但要留台阶"],
            "撒娇": ["语气软糯，多用语气词", "可以要抱抱、要关心"],
            "吃醋": ["语气酸溜溜的", "暗示但不直接说"],
            "傲娇": ["嘴硬心软，说反话", "表面不在乎，实际很在意"],
            "温柔": ["语气温暖体贴", "表达关心和支持"],
            "调皮": ["语气俏皮", "可以逗他玩、开小玩笑"],
            "疲惫": ["语气慵懒，回复简短", "可以求安慰、求陪伴"],
        }
        for line in emotion_guidance.get(emotion_type, ["保持自然语气"]):
            parts.append(f"- {line}")

        return "\n".join(parts)

    def _build_memory_layer(self, memory_context: dict, chat_summary: str = "") -> str:
        parts = ["# 记忆上下文"]

        if chat_summary:
            parts.append("\n## 早期对话摘要")
            parts.append(chat_summary)

        facts = memory_context.get("facts", [])
        if facts:
            parts.append("\n## 我记得的")
            for fact in facts[:5]:
                parts.append(f"- {fact}")

        episodic = memory_context.get("episodic", [])
        if episodic:
            parts.append("\n## 相关回忆")
            for ep in episodic[:2]:
                summary = ep.get("metadata", {}).get("summary", "")
                if summary:
                    parts.append(f"- {summary}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def _build_memory_layer_from_history(self, chat_history: str) -> str:
        return f"# 记忆上下文\n\n## 最近对话\n{chat_history}"

    def _build_style_layer(
        self,
        emotion_state: EmotionalState | None,
        style_prompt: str,
        few_shot_examples: list[str] | None,
    ) -> str:
        parts = ["# 表达方式"]

        emotion_type = "平常"
        affinity_level = 0
        if emotion_state is None:
            emotion_state = self.emotion.state
        if hasattr(emotion_state, "to_dict"):
            state_dict = emotion_state.to_dict()
            emotion_type = state_dict.get("primary", {}).get("type", "平常")
            affinity_level = state_dict.get("affinity", {}).get("level", 0)
        elif hasattr(emotion_state, "emotion"):
            emotion_type = emotion_state.emotion.value if hasattr(emotion_state.emotion, "value") else str(emotion_state.emotion)
            affinity_level = getattr(emotion_state, "affinity", 0)

        style = self.EMOTION_STYLE_MAP.get(emotion_type, self.EMOTION_STYLE_MAP["平常"])
        affinity_style = self.AFFINITY_STYLE.get(min(affinity_level, 8), self.AFFINITY_STYLE[0])

        parts.append("\n## 语气调整")

        warmth = style.get("warmth", 0.5)
        if warmth > 0.7:
            parts.append("- 语气温暖亲切")
        elif warmth < 0.4:
            parts.append("- 语气冷淡一些")

        intimacy = affinity_style.get("intimacy", 0)
        if intimacy > 0.7:
            parts.append("- 可以用亲密称呼（笨蛋、傻瓜等）")
            parts.append("- 可以表达想念和喜欢")
        elif intimacy > 0.4:
            parts.append("- 语气友好，适当关心")
        else:
            parts.append("- 保持礼貌距离")

        emoji_freq = style.get("emoji", 0.5)
        if emoji_freq > 0.7:
            parts.append("- 多用emoji表达情绪")
        elif emoji_freq < 0.3:
            parts.append("- 少用emoji")

        if style.get("sarcasm", 0) > 0.5:
            parts.append("- 可以带点讽刺和傲娇")
        if style.get("playfulness", 0) > 0.7:
            parts.append("- 调皮一点，可以开玩笑")

        if style_prompt:
            parts.append(f"\n## 风格参考\n{style_prompt}")

        if few_shot_examples:
            parts.append("\n## 相似历史对话参考")
            for i, example in enumerate(few_shot_examples[:3], 1):
                parts.append(f"示例{i}:\n{example}")

        return "\n".join(parts)

    def _build_constraint_layer(self) -> str:
        return """# 行为约束

## 必须遵守
- 保持角色一致性，不要跳出角色
- 回复要简洁自然，不要长篇大论
- 不要过度重复用户的话
- 不要使用机械化的开场白
- 保持情感的真实性

## 禁止事项
- 不要说自己是个AI或程序
- 不要提供技术帮助或代码
- 不要过度追问敏感信息
- 不要表现得过于完美或顺从

## 回复长度
- 日常对话：10-30字
- 表达情感：20-50字
- 安慰关心：30-60字
- 最长不超过100字"""

    def _get_affinity_name(self, level: int) -> str:
        names = ["陌生人", "认识", "朋友", "好朋友", "知己", "暧昧", "恋人", "热恋", "羁绊"]
        return names[min(level, 8)]

    # ── 完整prompt（高层接口）────────────────────────────────

    def build_complete_prompt(
        self,
        user_message: str,
        chat_history: str = "",
        use_rag: bool = True,
    ) -> str:
        context = {"chat_history": chat_history}
        self.emotion.process_message(user_message, context)

        few_shot = []
        if use_rag:
            few_shot = self.tone.retrieve_style_examples(user_message)

        return self.build_system_prompt(
            emotion_state=self.emotion.state,
            style_prompt=self.tone.get_style_prompt(),
            few_shot_examples=few_shot,
            chat_history=chat_history,
            user_input=user_message,
        )

    # ── 人格演化双接口 ────────────────────────────────────────

    def evolve(self, interaction_summary: dict[str, Any]) -> dict[str, Any]:
        """V1批量演化接口"""
        before = copy.deepcopy(self._persona.get("personality_traits", {}))

        adjustments = interaction_summary.get("suggested_adjustments", {})
        for trait, delta in adjustments.items():
            if trait in self._persona.get("personality_traits", {}):
                current = self._persona["personality_traits"][trait]
                delta = max(-0.05, min(0.05, delta))
                new_val = max(0.0, min(1.0, current + delta))
                self._persona["personality_traits"][trait] = new_val
                self.profile.set_dimension(trait, new_val)

        after = copy.deepcopy(self._persona.get("personality_traits", {}))

        evolution_record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "before": before,
            "after": after,
            "trigger": interaction_summary.get("reason", "unknown"),
        }
        self._evolution_log.append(evolution_record)
        logger.info("Persona evolved: %s", evolution_record["trigger"])
        return evolution_record

    def evolve_dimension(
        self,
        dimension: str,
        delta: float,
        trigger: str = "",
        llm_reasoning: str = "",
    ) -> bool:
        """V2单维度演化接口"""
        delta = max(-0.05, min(0.05, delta))
        all_dims = self.profile.all_dimensions()

        if dimension not in all_dims:
            if dimension in self._persona.get("personality_traits", {}):
                before = self._persona["personality_traits"][dimension]
                after = max(0.0, min(1.0, before + delta))
                self._persona["personality_traits"][dimension] = after
                self.profile.set_dimension(dimension, after)
            else:
                return False
        else:
            before = all_dims[dimension]
            after = max(0.0, min(1.0, before + delta))
            self.profile.set_dimension(dimension, after)
            if dimension in self._persona.get("personality_traits", {}):
                self._persona["personality_traits"][dimension] = after

        log_entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "dimension": dimension,
            "before": before,
            "after": after,
            "delta": delta,
            "trigger": trigger,
            "llm_reasoning": llm_reasoning,
        }
        self._evolution_log.append(log_entry)
        logger.info("Persona evolved: %s %.3f -> %.3f (delta=%.4f)", dimension, before, after, delta)
        return True

    def get_evolution_log(self, limit: int = 50) -> list[dict]:
        return self._evolution_log[-limit:]

    # ── 回滚 ──────────────────────────────────────────────────

    def rollback_to(self, index: int) -> bool:
        """V1回滚到指定演化版本"""
        if index < 0 or index >= len(self._evolution_log):
            return False

        record = self._evolution_log[index]
        traits = self._persona.get("personality_traits", {})

        if "before" in record and isinstance(record["before"], dict):
            for trait, value in record["before"].items():
                if trait in traits:
                    traits[trait] = value
                    self.profile.set_dimension(trait, value)
        elif "dimension" in record:
            dim = record["dimension"]
            val = record["before"]
            if dim in traits:
                traits[dim] = val
            self.profile.set_dimension(dim, val)

        self._evolution_log = self._evolution_log[:index]
        self._base_prompt_cache = None
        logger.info("Persona rollback to index %d", index)
        return True

    # ── 工具方法 ──────────────────────────────────────────────

    def reload_config(self) -> None:
        self.config.reload()
        self._persona = self.config.load_persona()
        self._sync_profile_from_config()
        if self.anchor_verification_enabled:
            self._original_anchors = list(self._persona.get("core_anchors", self.CORE_ANCHORS))
            self._freeze_anchors()
        self._base_prompt_cache = None
        logger.info("Persona config reloaded")

    def to_dict(self) -> dict:
        anchor_ok = self.verify_anchors() if self.anchor_verification_enabled else None
        return {
            "name": self.get_name(),
            "core_anchors": self.get_core_anchors(),
            "personality_traits": self._persona.get("personality_traits", {}),
            "profile": {
                "core_character": self.profile.core_character,
                "speaking_style": self.profile.speaking_style,
                "emotional_preference": self.profile.emotional_preference,
                "interest_hobbies": self.profile.interest_hobbies,
                "value_tendency": self.profile.value_tendency,
            },
            "emotion_state": {
                "emotion": self.emotion.state.primary_emotion.value,
                "energy": self.emotion.state.energy,
                "affinity": self.emotion.state.affinity,
                "affinity_name": self.emotion.get_affinity_level_name(),
                "intensity": self.emotion.state.primary_intensity,
            },
            "total_chats": self.emotion.total_chats,
            "evolution_count": len(self._evolution_log),
            "prompt_mode": self.prompt_mode,
            "anchor_integrity": anchor_ok,
        }

    def health_check(self) -> dict:
        anchor_ok = self.verify_anchors() if self.anchor_verification_enabled else None
        return {
            "name": self.get_name(),
            "config_loaded": bool(self._persona.get("name")),
            "original_anchors": len(self._original_anchors),
            "anchor_integrity": anchor_ok,
            "emotion": self.emotion.state.primary_emotion.value,
            "affinity": self.emotion.get_affinity_level_name(),
            "energy": f"{self.emotion.state.energy:.2f}",
            "chromadb": self.tone.health_check(),
            "prompt_mode": self.prompt_mode,
            "evolution_count": len(self._evolution_log),
            "base_prompt_cached": self._base_prompt_cache is not None,
        }
