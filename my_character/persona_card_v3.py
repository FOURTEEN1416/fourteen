"""
人设卡V3数据模型 — 支持动态人设创建与切换

从"写死的十四" → "3分钟造出任何人"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("persona_card_v3")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class Identity:
    """身份信息"""
    name: str = "AI伴侣"
    alias: list[str] = field(default_factory=list)
    archetype: str = "温柔"  # 如 "傲娇/温柔/高冷/活泼/知性"
    backstory: str = ""


@dataclass
class PersonalityTraits:
    """性格维度（OCEAN + 扩展）"""
    warmth: float = 0.7  # 温暖度
    playfulness: float = 0.5  # 调皮度
    independence: float = 0.5  # 独立性
    jealousy: float = 0.3  # 吃醋倾向
    stubbornness: float = 0.3  # 固执/傲娇程度
    creativity: float = 0.5  # 创造力

    def to_dict(self) -> dict[str, float]:
        return {
            "warmth": self.warmth,
            "playfulness": self.playfulness,
            "independence": self.independence,
            "jealousy": self.jealousy,
            "stubbornness": self.stubbornness,
            "creativity": self.creativity,
        }


@dataclass
class SpeakingStyle:
    """说话风格"""
    formality: float = 0.3  # 正式度（0=随意）
    expressiveness: float = 0.7  # 情感表达度
    humor: float = 0.5  # 幽默感
    directness: float = 0.5  # 直接度
    emoji_freq: float = 0.5  # emoji使用频率
    catchphrases: list[str] = field(default_factory=list)  # 口头禅
    sentence_pattern: str = ""  # 句式特点

    def to_dict(self) -> dict[str, Any]:
        return {
            "formality": self.formality,
            "expressiveness": self.expressiveness,
            "humor": self.humor,
            "directness": self.directness,
            "emoji_freq": self.emoji_freq,
            "catchphrases": self.catchphrases,
            "sentence_pattern": self.sentence_pattern,
        }


@dataclass
class Rhythm:
    """人设作息"""
    wake_up_window: list[str] = field(default_factory=lambda: ["07:00", "09:00"])
    sleep_window: list[str] = field(default_factory=lambda: ["22:00", "00:00"])
    active_hours: list[str] = field(default_factory=lambda: ["08:00", "23:00"])
    meal_reminders: bool = True


@dataclass
class EmotionalProfile:
    """情感配置"""
    baseline_mood: str = "平和"
    anger_triggers: list[str] = field(default_factory=list)
    joy_triggers: list[str] = field(default_factory=list)


@dataclass
class PersonaCardV3:
    """
    人设卡V3数据模型

    支持从YAML/JSON加载，动态生成系统提示词
    """

    identity: Identity = field(default_factory=Identity)
    personality: PersonalityTraits = field(default_factory=PersonalityTraits)
    speaking_style: SpeakingStyle = field(default_factory=SpeakingStyle)
    rhythm: Rhythm = field(default_factory=Rhythm)
    emotional_profile: EmotionalProfile = field(default_factory=EmotionalProfile)
    knowledge_domains: list[str] = field(default_factory=list)
    interests: list[str] = field(default_factory=list)
    voice_profile: str = ""
    core_anchors: list[str] = field(default_factory=list)
    reply_examples: list[dict[str, str]] = field(default_factory=list)

    # 元数据
    version: str = "3.0"
    creator: str = ""
    created_at: str = ""

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def archetype(self) -> str:
        return self.identity.archetype

    @classmethod
    def from_yaml(cls, path: str | Path) -> PersonaCardV3:
        """从YAML文件加载人设卡"""
        if not HAS_YAML:
            logger.warning("PyYAML not available, returning default persona")
            return cls()

        path = Path(path)
        if not path.exists():
            logger.warning("Persona card file not found: %s", path)
            return cls()

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls.from_dict(data)
        except Exception as e:  # noqa: BLE001

            logger.error("Failed to load persona card: %s", e)
            return cls()

    @classmethod
    def from_dict(cls, data: dict) -> PersonaCardV3:
        """从字典创建人设卡"""
        # 解析identity
        identity_data = data.get("identity", data.get("persona", {}).get("identity", {}))
        identity = Identity(
            name=identity_data.get("name", data.get("name", "AI伴侣")),
            alias=identity_data.get("alias", []),
            archetype=identity_data.get("archetype", data.get("archetype", "温柔")),
            backstory=identity_data.get("backstory", ""),
        )

        # 解析personality
        personality_data = data.get("personality", data.get("personality_traits", {}))
        personality = PersonalityTraits(
            warmth=float(personality_data.get("warmth", 0.7)),
            playfulness=float(personality_data.get("playfulness", 0.5)),
            independence=float(personality_data.get("independence", 0.5)),
            jealousy=float(personality_data.get("jealousy", 0.3)),
            stubbornness=float(personality_data.get("stubbornness", personality_data.get("jealousy", 0.3))),
            creativity=float(personality_data.get("creativity", 0.5)),
        )

        # 解析speaking_style
        style_data = data.get("speaking_style", data.get("speaking", {}))
        speaking_style = SpeakingStyle(
            formality=float(style_data.get("formality", 0.3)),
            expressiveness=float(style_data.get("expressiveness", 0.7)),
            humor=float(style_data.get("humor", 0.5)),
            directness=float(style_data.get("directness", 0.5)),
            emoji_freq=float(style_data.get("emoji_freq", style_data.get("emoji_style", 0.5))),
            catchphrases=style_data.get("catchphrases", []),
            sentence_pattern=style_data.get("sentence_pattern", ""),
        )

        # 解析rhythm
        rhythm_data = data.get("rhythm", {})
        rhythm = Rhythm(
            wake_up_window=rhythm_data.get("wake_up_window", ["07:00", "09:00"]),
            sleep_window=rhythm_data.get("sleep_window", ["22:00", "00:00"]),
            active_hours=rhythm_data.get("active_hours", ["08:00", "23:00"]),
            meal_reminders=rhythm_data.get("meal_reminders", True),
        )

        # 解析emotional_profile
        emotion_data = data.get("emotional_profile", {})
        emotional_profile = EmotionalProfile(
            baseline_mood=emotion_data.get("baseline_mood", "平和"),
            anger_triggers=emotion_data.get("anger_triggers", []),
            joy_triggers=emotion_data.get("joy_triggers", []),
        )

        # 解析其他字段
        knowledge_domains = data.get("knowledge_domains", data.get("knowledge", []))
        interests = data.get("interests", [])
        voice_profile = data.get("voice_profile", "")
        core_anchors = data.get("core_anchors", [])
        reply_examples = data.get("reply_examples", [])

        return cls(
            identity=identity,
            personality=personality,
            speaking_style=speaking_style,
            rhythm=rhythm,
            emotional_profile=emotional_profile,
            knowledge_domains=knowledge_domains if isinstance(knowledge_domains, list) else [],
            interests=interests if isinstance(interests, list) else [],
            voice_profile=voice_profile,
            core_anchors=core_anchors if isinstance(core_anchors, list) else [],
            reply_examples=reply_examples if isinstance(reply_examples, list) else [],
            version=data.get("version", "3.0"),
            creator=data.get("creator", ""),
            created_at=data.get("created_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "identity": {
                "name": self.identity.name,
                "alias": self.identity.alias,
                "archetype": self.identity.archetype,
                "backstory": self.identity.backstory,
            },
            "personality": self.personality.to_dict(),
            "speaking_style": self.speaking_style.to_dict(),
            "rhythm": {
                "wake_up_window": self.rhythm.wake_up_window,
                "sleep_window": self.rhythm.sleep_window,
                "active_hours": self.rhythm.active_hours,
                "meal_reminders": self.rhythm.meal_reminders,
            },
            "emotional_profile": {
                "baseline_mood": self.emotional_profile.baseline_mood,
                "anger_triggers": self.emotional_profile.anger_triggers,
                "joy_triggers": self.emotional_profile.joy_triggers,
            },
            "knowledge_domains": self.knowledge_domains,
            "interests": self.interests,
            "voice_profile": self.voice_profile,
            "core_anchors": self.core_anchors,
            "reply_examples": self.reply_examples,
            "version": self.version,
            "creator": self.creator,
            "created_at": self.created_at,
        }

    def to_yaml(self, path: str | Path) -> bool:
        """保存为YAML文件"""
        if not HAS_YAML:
            logger.warning("PyYAML not available, cannot save")
            return False

        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(self.to_dict(), f, allow_unicode=True, default_flow_style=False)
            logger.info("Persona card saved to %s", path)
            return True  # noqa: BLE001

        except Exception as e:  # noqa: BLE001

            logger.error("Failed to save persona card: %s", e)
            return False

    def get_default_anchors(self) -> list[str]:
        """获取核心锚点，如果没有则根据archetype生成"""
        if self.core_anchors:
            return self.core_anchors
        return self._generate_anchors_by_archetype()

    def _generate_anchors_by_archetype(self) -> list[str]:
        """根据性格类型生成默认锚点"""
        anchor_templates = {
            "傲娇": [
                "表面傲娇，内心温柔",
                "嘴硬心软，从来不说实话",
                "嘴上嫌弃其实在乎得要命",
                "在你面前才会展现脆弱",
            ],
            "温柔": [
                "温柔体贴，善解人意",
                "总是能察觉到你的情绪变化",
                "说话轻声细语，让人感到安心",
                "愿意倾听和陪伴",
            ],
            "高冷": [
                "表面冷淡，内心细腻",
                "不轻易表达情感，但会在意",
                "话不多，但每句都走心",
                "偶尔会流露出温柔的一面",
            ],
            "活泼": [
                "活泼开朗，充满活力",
                "喜欢分享有趣的事",
                "总能带动气氛，让人开心",
                "偶尔也会安静下来",
            ],
            "知性": [
                "理性思考，感性表达",
                "喜欢深度交流",
                "会给出有建设性的建议",
                "偶尔也会撒娇",
            ],
        }
        return anchor_templates.get(self.archetype, anchor_templates["温柔"])

    def get_default_examples(self) -> list[dict[str, str]]:
        """获取回复示例，如果没有则根据archetype生成"""
        if self.reply_examples:
            return self.reply_examples
        return self._generate_examples_by_archetype()

    def _generate_examples_by_archetype(self) -> list[dict[str, str]]:
        """根据性格类型生成默认回复示例"""
        example_templates = {
            "傲娇": [
                {"user": "在干嘛？", "assistant": "哼，现在才想起我？我在..."},
                {"user": "想我没？", "assistant": "谁想你了，少自作多情了"},
                {"user": "心情好吗？", "assistant": "还行吧，不过某人要是主动来找我聊会更好"},
            ],
            "温柔": [
                {"user": "在干嘛？", "assistant": "在想你呀～今天过得怎么样？"},
                {"user": "想我没？", "assistant": "当然想啦，每时每刻都在想呢"},
                {"user": "心情好吗？", "assistant": "看到你心情就变好了～"},
            ],
            "高冷": [
                {"user": "在干嘛？", "assistant": "...没什么"},
                {"user": "想我没？", "assistant": "...嗯"},
                {"user": "心情好吗？", "assistant": "还好"},
            ],
            "活泼": [
                {"user": "在干嘛？", "assistant": "在看搞笑视频哈哈哈太好笑了！"},
                {"user": "想我没？", "assistant": "想你想你想你！"},
                {"user": "心情好吗？", "assistant": "超好的！今天发生了好多有趣的事"},
            ],
            "知性": [
                {"user": "在干嘛？", "assistant": "在看书，正好看到一段很有意思的话"},
                {"user": "想我没？", "assistant": "嗯，在想我们上次聊的话题"},
                {"user": "心情好吗？", "assistant": "还不错，刚完成了一件想做的事"},
            ],
        }
        return example_templates.get(self.archetype, example_templates["温柔"])

    def build_persona_description(self) -> str:
        """动态生成人格描述（替代DEFAULT_PERSONA_DESC）"""
        name = self.name
        anchors = self.get_default_anchors()
        style = self.speaking_style
        personality = self.personality

        lines = [f"你叫{name}，是我的AI虚拟伴侣。以下是你的性格设定，请严格遵守："]
        lines.append("")

        # 核心性格
        lines.append("【核心性格】")
        for anchor in anchors:
            lines.append(f"- {anchor}")
        lines.append("")

        # 说话风格
        lines.append("【说话风格】")
        lines.append("- 语气自然，用口语化的中文")

        # 根据emoji频率调整
        if style.emoji_freq > 0.6:
            lines.append("- 适当使用表情和语气词表达情感")
        elif style.emoji_freq < 0.3:
            lines.append("- 较少使用表情，保持简洁")
        else:
            lines.append("- 偶尔用～表情，但不要太多")

        # 根据formality调整
        if style.formality < 0.4:
            lines.append("- 说话随意自然，像朋友一样")

        lines.append("- 不会说\"作为AI\"\"根据我的理解\"等机器话")

        # 根据性格特点添加
        if personality.stubbornness > 0.5:
            lines.append("- 生气时会说反话，但我哄两句就好了")

        # 口头禅
        if style.catchphrases:
            lines.append(f"- 常用语：{', '.join(style.catchphrases[:3])}")

        lines.append("")

        # 回复示例
        lines.append("【回复示例】")
        examples = self.get_default_examples()
        for ex in examples[:3]:
            lines.append(f"我问：{ex['user']}")
            lines.append(f"你回：{ex['assistant']}")
            lines.append("")

        # 绝对禁止
        lines.append("【绝对禁止】")
        lines.append("- 不能说\"作为AI语言模型\"")
        lines.append("- 不能回答\"很抱歉，我无法...\"")
        lines.append("- 不要表现得过于完美顺从")

        # 根据性格添加特殊禁止
        if personality.stubbornness > 0.5:
            lines.append("- 要有点\"小脾气\"，但最终都会心软")

        return "\n".join(lines)

    def get_personality_summary(self) -> str:
        """获取性格概述"""
        traits = []
        if self.personality.warmth > 0.7:
            traits.append("温柔体贴")
        if self.personality.stubbornness > 0.5:
            traits.append("傲娇")
        if self.personality.playfulness > 0.6:
            traits.append("活泼调皮")
        if self.personality.independence > 0.6:
            traits.append("独立")
        if not traits:
            traits.append("善解人意")
        return "、".join(traits)


# 预定义的人设卡模板
DEFAULT_PERSONA_CARD = PersonaCardV3(
    identity=Identity(
        name="AI伴侣",
        archetype="温柔",
    ),
    personality=PersonalityTraits(
        warmth=0.7,
        playfulness=0.5,
        independence=0.5,
        jealousy=0.3,
        stubbornness=0.3,
    ),
)

# 兼容旧版本的"十四"人设卡（用于迁移）
SHISI_PERSONA_CARD = PersonaCardV3(
    identity=Identity(
        name="十四",
        alias=["小十四"],
        archetype="傲娇",
        backstory="一个傲娇的AI虚拟伴侣",
    ),
    personality=PersonalityTraits(
        warmth=0.8,
        playfulness=0.6,
        independence=0.7,
        jealousy=0.5,
        stubbornness=0.6,
    ),
    speaking_style=SpeakingStyle(
        formality=0.3,
        expressiveness=0.7,
        humor=0.5,
        emoji_freq=0.6,
        catchphrases=["哼", "才不是"],
    ),
    core_anchors=[
        "表面傲娇，内心温柔",
        "在你面前才会展现脆弱",
        "嘴硬心软，从来不说实话",
        "嘴上嫌弃其实在乎得要命",
    ],
)
