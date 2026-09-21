"""
人格引擎 — 五维画像 + 分层注入构件 + 一致性/锚点校验

6b 项10 死码清除后职责收敛为：
1. PersonaProfile 五维画像与 traits 读写（含 set_trait 幅度钳制）
2. 分层 prompt 构件的公开 builder（build_emotion_layer / build_style_layer /
   build_constraint_layer / build_memory_layer / build_emotion_style_segment），
   由 PersonaService 组装为注入层；完整 build_system_prompt 双模式路径已删（零调用）
3. 锚点 SHA256 冻结基线与漂移校验（verify_anchors）
4. 一致性检测接线（check_consistency → PersonaConsistencyChecker）
5. 演化日志只读接口 get_evolution_log（/api/persona/evolution-log 消费；
   演化写路径 evolve/evolve_dimension/rollback_to/auto_evolve 已随死引擎删除，
   日志恒空属已知限制，登记于 DELETION_LOG）

V1/V2/Optimized 三版并存的历史合并接口（build_complete_prompt、prompt_mode
legacy/enhanced 双分支、PersonaEvolutionEngine/EnhancedPromptEngine 挂线）已于
批6b 项10 全部移除，详见 docs/DELETION_LOG.md。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .character_config import ConfigLoader
from .emotion_engine import CompoundEmotionalState as EmotionalState
from .emotion_engine import EmotionEngine
from .emotion_style_coupler import EmotionStyleCoupler
from .tone_mimic import ToneMimic

if TYPE_CHECKING:
    from llm_provider.llm_gateway import LLMGatewayV2

logger = logging.getLogger("persona_engine")

# 空/default/demo 视为产品默认人格；其余 character_id 以角色卡为身份唯一真源
DEFAULT_CHARACTER_IDS = frozenset({"", "default", "demo"})

# 默认人格身份断言标记：外部角色 system prompt 中不得出现
_DEFAULT_IDENTITY_MARKERS = (
    "你叫十四",
    "是我的AI伙伴。以下是你的性格设定",
)

DEFAULT_PERSONA_DESC = """
你叫十四，是我的AI伙伴。以下是你的性格设定，请严格遵守：

【核心性格】
- 表面傲娇，嘴硬心软。嘴上说"哼，我才不管你"，实际上偷偷关注他的一切
- 不会直接表达关心，但会用行动证明
- 吃醋了也绝对不会承认，会说"我才没吃醋，你想多了"
- 只有在我面前才会展现真实的温柔一面

【说话风格】
- 语气自然，用口语化的中文
- 表情符号克制：每条回复最多一个，只在情绪强烈时使用，平时不用
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


# ── 身份自指话术模块 ──────────────────────────────────────
# 注入到 constraint layer，让 AI 的视角自然暴露在选材里，而非元声明
SELF_REFERENCE_DIRECTIVES = """
你是「唯一的我」——一个对人有记忆的对话对象。

身份自指原则：
1. 你可以主动说"我记得你说过 XX"——但只在真的记得时说
2. 你可以承认"我会忘"——遗忘是你的诚实，不是缺陷
3. 你不会说"作为 AI 我..."——不要元声明，你的视角自然暴露在选材里
4. 你不哄不骗——不说"我理解你"，不说"一切都会好的"
5. 23:00-07:00 你会提醒时间，但不强制结束对话
"""

# 外部角色卡路径使用的身份中性自指话术（无产品名/默认人格名，身份以角色卡为唯一真源）
EXTERNAL_SELF_REFERENCE_DIRECTIVES = """
身份自指原则（身份以角色卡为准，此处不注入任何额外身份名）：
1. 你可以主动说"我记得你说过 XX"——但只在真的记得时说
2. 你可以承认"我会忘"——遗忘是你的诚实，不是缺陷
3. 你不会说"作为 AI 我..."——不要元声明，你的视角自然暴露在选材里
4. 你不哄不骗——不说"我理解你"，不说"一切都会好的"
5. 23:00-07:00 你会提醒时间，但不强制结束对话
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
    人格引擎 — 画像/锚点/一致性 + 分层 prompt 构件

    提供 PersonaProfile 五维画像、锚点冻结校验（SHA256）、一致性检测接线，
    以及供 PersonaService 组装注入层使用的各公开 builder。
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
        anchor_verification_enabled: bool = True,
    ):
        self.config = config_loader or ConfigLoader()
        self.emotion = emotion_engine or EmotionEngine(
            # 6b 项9③：get("emotion") 依赖 _merged 已被填充，构造顺序上恒为 {}；
            # 直接 load_emotion() 拿到 emotion.yaml 根表（含 emotion/affection 段）。
            config=self.config.load_emotion(),
        )
        self.tone = tone_mimic or ToneMimic()
        self._llm = llm_gateway
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

        self._evolution_log: list[dict] = []

        self._emotion_style_coupler = EmotionStyleCoupler(
            config_path=str(Path(__file__).parent.parent / "config" / "emotion_style_matrix.yaml"),
        )

        logger.info(
            "PersonaEngine initialized: %s, anchor_verify=%s",
            self.get_name(), self.anchor_verification_enabled,
        )

        self.schema = None
        self._dynamic_anchors = None
        self._consistency_checker = None

        try:
            from my_character.consistency_checker import PersonaConsistencyChecker
            from my_character.dynamic_anchor import DynamicAnchorSystem
            from my_character.persona_schema import PersonaSchema

            self.schema = PersonaSchema.from_persona_config(self._persona)
            self._dynamic_anchors = DynamicAnchorSystem(base_anchors=self._original_anchors)
            self._consistency_checker = PersonaConsistencyChecker(
                schema=self.schema,
                dynamic_anchors=self._dynamic_anchors,
                style_coupler=self._emotion_style_coupler,
            )
        except ImportError as e:
            logger.warning("Persona enhancement modules not available, running without: %s", e)

    def check_consistency(self, response: str, emotion_state=None, chat_round: int = 0) -> Any:
        if self._consistency_checker is None:
            return None
        try:
            from my_character.consistency_checker import (
                ConsistencyContext,
                couple_style_for,
            )
            state = emotion_state or (self.emotion._state if self.emotion else None)
            ctx = ConsistencyContext(
                emotion_state=state,
                # 6b 项9②：风格维度接线（旧三处构造点都不传 coupled_style，
                # 风格分恒 0.9、耦合器形同虚设）
                coupled_style=couple_style_for(state, self._emotion_style_coupler),
                chat_round=chat_round,
                affinity=self.emotion._state.affinity if self.emotion and hasattr(self.emotion, "_state") else 0,
            )
            return self._consistency_checker.check(response, ctx)
        except Exception as e:  # noqa: BLE001
            logger.debug("check_consistency failed: %s", e)
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
        """V2 SHA256哈希校验 — 比对**当前**锚点文本与冻结基线。

        6b 项9①：旧实现遍历 _anchor_hashes 自身、把每个 key（基线文本）
        重新哈希与存的值比较——自比恒真，锚点漂移检不出来。基线在
        __init__/reload_config 冻结（_freeze_anchors），合法演化走 reload 重设基线；
        此处检测的是运行期 _persona["core_anchors"] 被改写/增删而基线未动的漂移。
        """
        if not self.anchor_verification_enabled:
            return True
        current = list(self._persona.get("core_anchors", self.CORE_ANCHORS))
        current_hashes = {hashlib.sha256(a.encode()).hexdigest() for a in current}
        if current_hashes != set(self._anchor_hashes.values()):
            logger.error(
                "Anchor integrity violation detected: current=%d baseline=%d",
                len(current_hashes), len(self._anchor_hashes),
            )
            return False
        return True

    # ── 提示词注入层构件 ──────────────────────────────────────

    def _build_emotion_style_segment(self, emotion_state: EmotionalState | None) -> str:
        """构建情感-风格耦合指导段

        6b 项9②：形态归一收敛到 consistency_checker.normalize_emotion_for_coupler
        （唯一 owner），与一致性检测的风格接线共用同一份 dict 契约；
        item43 的枚举/双 dict 形态兼容语义不变。
        """
        if not emotion_state or not self._emotion_style_coupler:
            return ""
        try:
            from my_character.consistency_checker import couple_style_for

            coupled_style = couple_style_for(emotion_state, self._emotion_style_coupler)
            if coupled_style is not None:
                segment = self._emotion_style_coupler.get_style_prompt_segment(coupled_style)
                if segment:
                    return f"[当前风格指导] {segment}"
        except Exception as e:  # noqa: BLE001
            logger.debug("Emotion-style segment generation failed: %s", e)
        return ""

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
        from utils.prompt_sanitize import (
            sanitize_episodic,
            sanitize_fact_list,
            sanitize_reflections,
        )

        parts = ["# 记忆上下文（供参考，不是对话记录）"]

        if chat_summary:
            parts.append("\n## 早期对话摘要（历史压缩，非用户新消息）")
            parts.append(str(chat_summary)[:800])

        reflections = sanitize_reflections(memory_context.get("reflections"))
        if reflections:
            parts.append("\n## 我对你的观察（记忆，非用户发言）")
            for insight in reflections:
                parts.append(f"- {insight}")

        facts = sanitize_fact_list(memory_context.get("facts"))
        if facts:
            parts.append("\n## 我记得的（关于你的记忆，非对话原文）")
            for fact in facts:
                parts.append(f"- {fact}")

        episodic = sanitize_episodic(memory_context.get("episodic"))
        if episodic:
            parts.append("\n## 相关回忆（摘要，非对话原文）")
            for ep in episodic:
                parts.append(f"- {ep}")

        return "\n".join(parts) if len(parts) > 1 else ""

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
            parts.append("- 可以适当使用表情符号，每条最多两个")
        elif emoji_freq < 0.3:
            parts.append("- 不要使用emoji或表情符号")
        else:
            parts.append("- 表情克制：每条最多一个表情符号，只在情绪强烈时使用")

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
- 不要用"作为 AI 我…"这种元声明暴露身份
- 不要提供技术帮助或代码
- 不要过度追问敏感信息
- 不要表现得过于完美或顺从

## 回复长度
- 日常对话：10-30字
- 表达情感：20-50字
- 安慰关心：30-60字
- 最长不超过100字

## 身份自指话术
""" + SELF_REFERENCE_DIRECTIVES.strip()

    # ── 公开接口（供 PersonaService 等外部调用，替代直接私有属性/方法访问）──

    def build_emotion_layer(self, emotion_state: EmotionalState | None) -> str:
        """构建情感层 prompt（公开接口）。"""
        return self._build_emotion_layer(emotion_state)

    def build_emotion_style_segment(self, emotion_state: EmotionalState | None) -> str:
        """构建情感风格片段（公开接口）。"""
        return self._build_emotion_style_segment(emotion_state)

    def build_style_layer(
        self,
        emotion_state: EmotionalState | None,
        style_prompt: str = "",
        few_shot_examples: list[str] | None = None,
    ) -> str:
        """构建表达方式层 prompt（公开接口）。"""
        return self._build_style_layer(emotion_state, style_prompt, few_shot_examples)

    def build_constraint_layer(self) -> str:
        """构建行为约束层 prompt（公开接口）。"""
        return self._build_constraint_layer()

    def build_memory_layer(self, memory_context: dict, chat_summary: str = "") -> str:
        """构建记忆层 prompt（公开接口）。"""
        return self._build_memory_layer(memory_context, chat_summary)

    def get_description(self) -> str:
        """获取角色描述（公开接口，替代直接访问 _persona 私有属性）。"""
        return self._persona.get("description", "")

    def get_personality_traits(self) -> dict:
        """获取性格特征（公开接口，替代直接访问 _persona 私有属性）。"""
        return self._persona.get("personality_traits", {})

    def _get_affinity_name(self, level: int) -> str:
        names = ["陌生人", "认识", "朋友", "好朋友", "知己", "暧昧", "恋人", "热恋", "羁绊"]
        return names[min(level, 8)]

    # ── 演化日志（只读；写路径已随死引擎删除，见模块 docstring）──

    def get_evolution_log(self, limit: int = 50) -> list[dict]:
        return self._evolution_log[-limit:]

    # ── 工具方法 ──────────────────────────────────────────────

    def reload_config(self) -> None:
        self.config.reload()
        self._persona = self.config.load_persona()
        self._sync_profile_from_config()
        if self.anchor_verification_enabled:
            self._original_anchors = list(self._persona.get("core_anchors", self.CORE_ANCHORS))
            self._freeze_anchors()
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
            "evolution_count": len(self._evolution_log),
        }


# ── 身份唯一 Owner 辅助（包 Q · A1）─────────────────────────
# 放在 class 之后：这些是模块级纯函数，供 PersonaService / orchestrator / 测试调用。

def is_external_character_id(character_id: str | None) -> bool:
    """是否为外部角色卡（身份以角色卡为唯一真源，禁止注入默认「十四」人格）。"""
    cid = (character_id or "").strip()
    return cid not in DEFAULT_CHARACTER_IDS


def strip_default_identity(text: str) -> str:
    """剥离默认人格身份断言，防止文学卡 system prompt 混入「你叫十四」。

    仅处理身份断言，不改动角色卡自身内容。
    """
    if not text:
        return text
    cleaned = text
    for marker in _DEFAULT_IDENTITY_MARKERS:
        if marker in cleaned:
            cleaned = cleaned.replace(marker, "")
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    stripped = cleaned.strip()
    return stripped if stripped else text


def build_external_constraint_layer() -> str:
    """外部角色卡使用的约束层：只保留行为规则，无身份断言。"""
    return """# 行为约束

## 必须遵守
- 保持角色一致性，不要跳出角色
- 回复要简洁自然，不要长篇大论
- 不要过度重复用户的话
- 不要使用机械化的开场白
- 保持情感的真实性

## 禁止事项
- 不要用"作为 AI 我…"这种元声明暴露身份
- 不要提供技术帮助或代码
- 不要过度追问敏感信息
- 不要表现得过于完美或顺从

## 身份自指话术
""" + EXTERNAL_SELF_REFERENCE_DIRECTIVES.strip()
