"""
角色卡数据模型 - 直接复用SillyTavern V1/V2/V3格式

@deprecated 此模块已弃用，请使用 shisi.core.models.character_aggregate.CharacterAggregate
EmotionStyleMap → shisi.core.models.persona_profile.PersonaProfile.from_emotion_style_map()

设计原则:
  - 数据结构与SillyTavern完全兼容
  - 使用dataclass而非pydantic（避免额外依赖，与项目现有风格一致）
  - 所有字段带默认值，向前兼容
  - 提供to_persona_config()方法直接对接PersonaEngine
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("character_card.models")


class CardVersion(Enum):
    """角色卡版本"""
    V1 = "v1"
    V2 = "v2"
    V3 = "v3"


# ── SillyTavern完全兼容的数据结构 ──


@dataclass
class WorldInfoEntry:
    """角色书条目 - SillyTavern WorldInfo完全兼容"""
    id: int = 0
    keys: list[str] = field(default_factory=list)
    content: str = ""
    secondary_keys: list[str] = field(default_factory=list)
    comment: str = ""
    constant: bool = False
    selective: bool = True
    insertion_order: int = 100
    enabled: bool = True
    position: str = "0"
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldInfoBook:
    """角色书 - 场景/知识库"""
    name: str = ""
    entries: list[WorldInfoEntry] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class CharacterExtensions:
    """SillyTavern扩展字段"""
    talkativeness: float = 0.5
    fav: bool = False
    world: str = ""
    depth_prompt: dict | None = None
    regex_scripts: list[dict] = field(default_factory=list)


@dataclass
class CharacterData:
    """角色数据 - V2/V3核心数据节"""
    name: str = ""
    description: str = ""
    character_version: str = "1.0"
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    system_prompt: str = ""
    post_history_instructions: str = ""
    creator_notes: str = ""
    tags: list[str] = field(default_factory=list)
    creator: str = "unknown"
    character_book: WorldInfoBook | None = None
    extensions: CharacterExtensions = field(default_factory=lambda: CharacterExtensions())


@dataclass
class CharacterCard:
    """角色卡主类 - 兼容SillyTavern V1/V2/V3"""
    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    data: CharacterData = field(default_factory=lambda: CharacterData())

    @property
    def version(self) -> CardVersion:
        if self.spec == "chara_card_v2":
            return CardVersion.V2
        elif self.spec == "chara_card_v3":
            return CardVersion.V3
        return CardVersion.V1

    def to_persona_config(self) -> dict[str, Any]:
        """
        转换为PersonaEngine兼容配置

        这是核心集成点 - 对接十四的my_character/模块
        """
        return {
            "name": self.data.name,
            "description": self.data.description,
            "personality": self.data.personality,
            "scenario": self.data.scenario,
            "greeting": self.data.first_mes,
            "examples": self.data.mes_example,
            "system_prompt": self.data.system_prompt,
            "tags": self.data.tags,
            "creator_notes": self.data.creator_notes,
            # 情感倾向（从tags/description中推断）
            "warmth": self._infer_warmth(),
            "playfulness": self._infer_playfulness(),
        }

    def _infer_warmth(self) -> float:
        """从角色描述推断温暖度（0-1）"""
        text = (self.data.description + self.data.personality).lower()
        warm_words = ["温柔", "温暖", "善良", "可爱", "暖心", "甜美", "gentle", "warm", "sweet", "kind"]
        cold_words = ["冷淡", "高冷", "冷漠", "傲娇", "冰冷", "cold", "distant", "tsundere"]
        score = 0.5
        for w in warm_words:
            if w in text:
                score += 0.1
        for w in cold_words:
            if w in text:
                score -= 0.1
        return max(0.0, min(1.0, score))

    def _infer_playfulness(self) -> float:
        """从角色描述推断调皮度（0-1）"""
        text = (self.data.description + self.data.personality).lower()
        playful_words = ["调皮", "活泼", "搞怪", "幽默", "俏皮", "playful", "cheerful", "funny"]
        serious_words = ["认真", "严肃", "稳重", "成熟", "serious", "mature", "stern"]
        score = 0.5
        for w in playful_words:
            if w in text:
                score += 0.1
        for w in serious_words:
            if w in text:
                score -= 0.1
        return max(0.0, min(1.0, score))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（兼容SillyTavern格式）"""
        result = {
            "spec": self.spec,
            "spec_version": self.spec_version,
            "data": {
                "name": self.data.name,
                "description": self.data.description,
                "character_version": self.data.character_version,
                "personality": self.data.personality,
                "scenario": self.data.scenario,
                "first_mes": self.data.first_mes,
                "mes_example": self.data.mes_example,
                "alternate_greetings": self.data.alternate_greetings,
                "system_prompt": self.data.system_prompt,
                "post_history_instructions": self.data.post_history_instructions,
                "creator_notes": self.data.creator_notes,
                "tags": self.data.tags,
                "creator": self.data.creator,
                "extensions": {
                    "talkativeness": self.data.extensions.talkativeness,
                    "fav": self.data.extensions.fav,
                    "world": self.data.extensions.world,
                },
            },
        }
        # 可选字段
        if self.data.character_book:
            book_data: dict[str, Any] = {
                "name": self.data.character_book.name,
                "entries": [
                    {
                        "id": e.id,
                        "keys": list(e.keys),
                        "content": e.content,
                        "secondary_keys": list(e.secondary_keys),
                        "comment": e.comment,
                        "constant": e.constant,
                        "selective": e.selective,
                        "insertion_order": e.insertion_order,
                        "enabled": e.enabled,
                        "position": e.position,
                        "extensions": e.extensions,
                    }
                    for e in self.data.character_book.entries
                ],
                "extensions": self.data.character_book.extensions,
            }
            result["data"]["character_book"] = book_data  # type: ignore[index]
        if self.data.extensions.depth_prompt:
            result["data"]["extensions"]["depth_prompt"] = self.data.extensions.depth_prompt  # type: ignore[index]
        return result

    def to_json(self, indent: int = 2) -> str:
        """序列化为JSON字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class EmotionStyleMap:
    """
    情感-风格映射表

    用于将SillyTavern角色卡的情感倾向映射到十四的情感系统
    """
    warmth: float = 0.5
    playfulness: float = 0.5
    independence: float = 0.5
    jealousy: float = 0.3
    stubbornness: float = 0.4

    @classmethod
    def from_character_card(cls, card: CharacterCard) -> EmotionStyleMap:
        config = card.to_persona_config()
        text = (card.data.description + card.data.personality + card.data.creator_notes).lower()
        # 推断独立性
        indep_words = ["独立", "自主", "有自己的想法", "独立自主", "independent", "self-reliant"]
        dep_words = ["依赖", "粘人", "黏人", "离不开", "dependent", "clingy"]
        independence = 0.5
        for w in indep_words:
            if w in text:
                independence += 0.1
        for w in dep_words:
            if w in text:
                independence -= 0.1
        # 推断嫉妒倾向
        jealous_words = ["吃醋", "占有欲", "嫉妒", "小气", "jealous", "possessive", "jealousy"]
        jealousy = 0.3
        for w in jealous_words:
            if w in text:
                jealousy += 0.1
        # 推断固执程度
        stubborn_words = ["固执", "倔强", "犟", "坚持己见", "不认输", "stubborn", "headstrong"]
        flexible_words = ["随和", "好说话", "顺从", "柔和", "flexible", "agreeable"]
        stubbornness = 0.4
        for w in stubborn_words:
            if w in text:
                stubbornness += 0.1
        for w in flexible_words:
            if w in text:
                stubbornness -= 0.1

        return cls(
            warmth=config.get("warmth", 0.5),
            playfulness=config.get("playfulness", 0.5),
            independence=max(0.0, min(1.0, independence)),
            jealousy=max(0.0, min(1.0, jealousy)),
            stubbornness=max(0.0, min(1.0, stubbornness)),
        )
