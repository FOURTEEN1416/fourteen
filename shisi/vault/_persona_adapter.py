"""PersonaAdapter — 从 CharaCardV2 提取标准化 persona 特征。

提取维度：
- core_anchors: 核心锚点（性格关键词 + 身份描述）
- speaking_style: 说话风格（语气词、口癖、句式特征）
- background: 背景设定（世界观、身份、经历）
- relationship: 关系设定（与用户的关系、好感度基线）
- behavior_rules: 行为规则（该做/不该做、触发反应）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from shisi.character.models import CharaCardV2

logger = logging.getLogger("shisi.vault.persona_adapter")


@dataclass
class PersonaFeatures:
    """标准化 persona 特征。"""
    core_anchors: list[str] = field(default_factory=list)
    """性格关键词、身份标签、核心设定。"""

    speaking_style: list[str] = field(default_factory=list)
    """说话风格描述。"""

    background: list[str] = field(default_factory=list)
    """背景设定。"""

    relationship: list[str] = field(default_factory=list)
    """关系设定。"""

    behavior_rules: list[str] = field(default_factory=list)
    """行为规则。"""

    raw_personality: str = ""
    """原始 personality 全文（回退用）。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_anchors": self.core_anchors,
            "speaking_style": self.speaking_style,
            "background": self.background,
            "relationship": self.relationship,
            "behavior_rules": self.behavior_rules,
            "raw_personality": self.raw_personality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaFeatures:
        return cls(
            core_anchors=data.get("core_anchors", []),
            speaking_style=data.get("speaking_style", []),
            background=data.get("background", []),
            relationship=data.get("relationship", []),
            behavior_rules=data.get("behavior_rules", []),
            raw_personality=data.get("raw_personality", ""),
        )


class PersonaAdapter:
    """CharaCardV2 → PersonaFeatures 适配器。"""

    # 说话风格关键词
    _SPEAKING_KEYWORDS = [
        "语气", "口癖", "口头禅", "说话", "称呼", "自称",
        "语速", "语调", "尾音", "撒娇", "傲慢", "温柔",
        "冷淡", "热情", "严肃", "活泼", "慵懒",
    ]

    # 关系关键词
    _RELATIONSHIP_KEYWORDS = [
        "关系", "称呼我", "叫我", "对用户", "好感",
        "亲密度", "信任", "距离", "互动模式",
    ]

    # 行为规则关键词
    _RULE_KEYWORDS = [
        "规则", "不能", "不要", "必须", "禁止", "允许",
        "原则", "底线", "习惯", "日常", "反应",
    ]

    @classmethod
    def extract(cls, card: CharaCardV2) -> PersonaFeatures:
        """从 CharaCardV2 提取 persona 特征。"""
        data = card.data
        personality = data.personality or ""
        description = data.description or ""
        creator_notes = data.creator_notes or ""
        scenario = data.scenario or ""

        # 合并所有文本用于规则提取
        all_text = f"{personality}\n{description}\n{creator_notes}\n{scenario}"

        core_anchors = cls._extract_anchors(personality, description, scenario)
        speaking_style = cls._extract_by_keywords(all_text, cls._SPEAKING_KEYWORDS)
        background = cls._extract_background(scenario, description)
        relationship = cls._extract_by_keywords(all_text, cls._RELATIONSHIP_KEYWORDS)
        behavior_rules = cls._extract_by_keywords(all_text, cls._RULE_KEYWORDS)

        return PersonaFeatures(
            core_anchors=core_anchors,
            speaking_style=speaking_style,
            background=background,
            relationship=relationship,
            behavior_rules=behavior_rules,
            raw_personality=personality,
        )

    @classmethod
    def _extract_anchors(
        cls, personality: str, description: str, scenario: str
    ) -> list[str]:
        """提取核心锚点：取 personality 每行非空、非标点行，去掉过长行。"""
        anchors: list[str] = []
        for text in [personality, description]:
            for line in text.split("\n"):
                line = line.strip()
                # 跳过空行、纯标点行、过长行
                if not line or len(line) < 2:
                    continue
                if all(c in "，。！？、；：""''「」【】（）—…" for c in line):
                    continue
                if len(line) > 100:
                    # 长句截断到第一个句号
                    import re
                    match = re.split(r"[。！？!?]", line)
                    anchors.append(match[0].strip()[:80])
                else:
                    anchors.append(line)
        # 去重保留顺序
        seen = set()
        unique: list[str] = []
        for a in anchors:
            key = a[:20]
            if key not in seen:
                seen.add(key)
                unique.append(a)
        return unique[:20]  # 最多 20 个锚点

    @classmethod
    def _extract_by_keywords(cls, text: str, keywords: list[str]) -> list[str]:
        """按关键词提取相关句子。"""
        import re
        results: list[str] = []
        # 按句号/换行分割
        sentences = re.split(r"[。！？!?\n]", text)
        for sent in sentences:
            sent = sent.strip()
            if not sent or len(sent) < 4:
                continue
            for kw in keywords:
                if kw in sent and sent not in results:
                    results.append(sent)
                    break
        return results[:10]

    @classmethod
    def _extract_background(cls, scenario: str, description: str) -> list[str]:
        """提取背景设定。"""
        results: list[str] = []
        for text in [scenario, description]:
            if text.strip():
                results.append(text.strip()[:200])  # 截断过长
        return results
