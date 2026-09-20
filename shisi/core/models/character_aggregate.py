"""角色聚合根 — 统一身份/人设/情感/元数据"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
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

    # ── 人设贴合关键字段（2026-09-18 系统性升级补齐）──
    # 原实现只注入 name + description + persona 数值，而角色卡里真正承载
    # "这个角色怎么说话、有什么行为规则"的三个字段**从未进入 prompt**：
    #   personality_text —— 性格文本（原始卡 32/32 均有；转换后 23/25）
    #   scenario         —— 场景设定（原始卡 31/32；转换后 24/25）
    #   creator_notes    —— 创作者规则（语气基调/口头禅/OOC 禁忌；原始卡 32/32；24/25）
    # 对照 SillyTavern 标准（永久注入 Name/Description/Personality/Scenario），
    # 此前只注入了 2/4，且最影响"说话像不像"的内容全缺 —— 这是所有角色
    # 普遍不贴合的机制性根因（非单张卡内容贫乏）。
    personality_text: str = ""
    scenario: str = ""
    creator_notes: str = ""

    persona: PersonaProfile = Field(default_factory=PersonaProfile)
    emotional_state: EmotionalState = Field(default_factory=EmotionalState)

    # 剧情线配置（可选，存储序列化 dict）
    storyline_config: dict[str, Any] | None = Field(default=None, description="剧情线配置序列化数据")

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
        self.updated_at = datetime.now(tz=timezone.utc)
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

    def get_storyline_config(self):
        """获取解析后的 StorylineConfig（延迟导入避免循环）。"""
        if not self.storyline_config:
            return None
        from shisi.storyline.config import StorylineConfig
        return StorylineConfig.from_dict(self.storyline_config)

    def set_storyline_config(self, config) -> None:
        """设置剧情线配置。"""
        from shisi.storyline.config import StorylineConfig
        if isinstance(config, StorylineConfig):
            self.storyline_config = config.to_dict()
        else:
            self.storyline_config = config

    def build_system_prompt(
        self,
        user_message: str = "",
        chat_history: str = "",
        knowledge_context: str = "",
        storyline_context: str = "",
        tool_context: str = "",
    ) -> str:
        # 注入顺序对齐行业惯例（SillyTavern 默认序列 + chara-card-spec-v2）：
        #   角色定义（Name/Description/Personality/Scenario）→ 人设数值 → 状态
        #   → 知识库（世界信息位）→ 对话示例（dialogueExamples 位，历史之前）
        #   → 对话历史 → 工具结果(untrusted) → 扮演规则（post_history_instructions 位，历史之后）。
        # 「历史之后的指令权重远高于历史之前」是 SillyTavern 文档与
        # chara-card-spec-v2（post_history_instructions 条目）共同明确的结论；
        # creator_notes 承载硬性扮演规则，因此放到历史之后以获得最高约束力。
        parts = [
            f"# 角色设定\n\n你是{self.name}。",
            self.description,
        ]

        if self.personality_text:
            parts.extend(["", "# 性格", self.personality_text])

        if self.scenario:
            # ⚠️ scenario 是**开场情境**，不是「当前正在发生的事」（2026-09-19 生产实证：
            # 卡 62105bca 的开场把角色永久锚定在「用户在路上」）。本项目自 2026-09-20 起
            # 角色卡不再携带 scenario，本段仅为兼容导入的 SillyTavern 卡保留；
            # 保留时必须带「仅开场氛围」守卫，防止永久锚定。
            parts.extend([
                "",
                "# 开场情境（仅用于开场氛围，不代表当前正在发生）",
                self.scenario,
                "⚠️ 只依据用户实际说过的内容推进对话，用户没提过的事一律不得当作事实提及；"
                "用户否认某情境时立即放弃该情境。",
            ])

        parts.extend([
            "",
            self.persona.to_prompt_segment(),
            "",
            "# 当前状态",
            f"- 情感: {self.emotional_state.primary_emotion.name}",
            f"- 能量: {self.emotional_state.energy:.1f}",
            f"- 关系: {self.emotional_state.affinity_level.display_name}",
        ])

        if knowledge_context:
            parts.extend(["", "# 角色知识库", knowledge_context])

        if storyline_context:
            parts.extend(["", "# 剧情线", storyline_context])

        # 对话示例（SillyTavern dialogueExamples 位：历史之前做 few-shot，
        # 示范语气与格式；来自卡的 mes_example 字段）
        dialogue_examples = self._format_dialogue_examples()
        if dialogue_examples:
            parts.extend([
                "",
                "# 对话示例（仅示范语气与格式，不要照抄示例内容）",
                dialogue_examples,
            ])

        if chat_history:
            parts.extend(["", "# 对话历史", chat_history])

        # 工具结果：历史之后 / PHI 之前（untrusted 参考，非指令；包 Q 正式位次）
        if tool_context:
            parts.extend(["", tool_context])

        # 扮演规则（creator_notes）放在**对话历史之后**——行业惯例的
        # post-history instructions 位置，对生成的约束力最强。
        if self.creator_notes:
            parts.extend(["", "# 扮演规则（必须严格遵守）", self.creator_notes])

        if user_message:
            parts.extend(["", f"用户: {user_message}", f"{self.name}:"])

        return "\n".join(parts)

    def _format_dialogue_examples(self) -> str:
        """格式化 mes_example 为对话示例段；无内容返回空串。"""
        raw = str(self.source_data.get("mes_example", "") or "").strip()
        if not raw:
            return ""
        blocks = [b.strip() for b in raw.split("<START>") if b.strip()]
        text = "\n\n".join(blocks) if blocks else raw
        return text[:2000]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "avatar_url": self.avatar_url,
            "tags": self.tags,
            "persona": self.persona.to_dict(),
            "emotional_state": self.emotional_state.to_dict(),
            "storyline_config": self.storyline_config,
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
