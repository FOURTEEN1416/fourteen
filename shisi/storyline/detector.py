"""StorylineDetector — 自动检测人设文本中的剧情线模式。

扫描 character 的 personality / creator_notes / scenario 文本，
匹配时间、阶段、结局相关关键词，判断是否需要建议开启剧情线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from shisi.character.models import CharaCardV2

from .config import StorylineConfig


@dataclass
class DetectionResult:
    """自动检测结果"""
    has_storyline: bool = False
    confidence: float = 0.0
    matched_patterns: list[str] = field(default_factory=list)
    suggested_config: StorylineConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_storyline": self.has_storyline,
            "confidence": self.confidence,
            "matched_patterns": self.matched_patterns,
            "suggested": self.suggested_config.to_dict() if self.suggested_config else None,
        }


class StorylineDetector:
    """剧情线模式检测器。"""

    # 时间关键词
    _TIME_PATTERNS: list[tuple[str, str, float]] = [
        (r"第\s*[一二三四五六七八九十\d]+\s*天", "天数标识", 0.6),
        (r"\d+\s*天\s*(时间|期限|倒计时|旅程)", "天数期限", 0.7),
        (r"每\s*(句话|次对话|轮)\s*[+＋加增]\s*\d+\s*分钟", "对话推进时间", 0.9),
        (r"时间[系统线]", "时间系统", 0.8),
        (r"(总时长|上限|不超过)\s*\d+\s*天", "时长上限", 0.7),
    ]

    # 阶段关键词
    _STAGE_PATTERNS: list[tuple[str, str, float]] = [
        (r"(初识|初见|陌生|第一天)", "初始阶段", 0.4),
        (r"(熟悉|渐暖|靠近|第二天)", "熟悉阶段", 0.4),
        (r"(亲密|倾心|热恋|依赖)", "亲密阶段", 0.4),
        (r"(离别|告别|结局|终点|最后一天|第7天)", "离别结局", 0.5),
        (r"阶段.*(演变|变化|推进|发展)", "阶段演变", 0.7),
        (r"(性格|人格|态度).*(随时间|变化|演变|发展)", "时间人格演变", 0.8),
    ]

    # 行为规则关键词
    _BEHAVIOR_PATTERNS: list[tuple[str, str, float]] = [
        (r"禁止.*(亲密|牵手|拥抱|亲吻)", "亲密禁止规则", 0.8),
        (r"(必须|强制|不可|不能|严禁)", "强制规则", 0.5),
        (r"(OOC|越级|提前进入|越阶段)", "OOC禁止", 0.9),
        (r"(告别|离别).*(规则|流程|步骤)", "离别规则", 0.7),
        (r"空白.*(回复|内容|文字)", "空白回复规则", 0.9),
    ]

    # 结局关键词
    _ENDING_PATTERNS: list[tuple[str, str, float]] = [
        (r"(结局|结尾|终章|尾声)", "结局标识", 0.6),
        (r"(旁白|独白|叙事|叙述)", "旁白", 0.5),
        (r"(回忆|纪念|礼物|留[给到]).*", "回忆要素", 0.5),
        (r"(火车|离开|再见|永别)", "离别符号", 0.4),
    ]

    @classmethod
    def detect_from_card(cls, card: CharaCardV2) -> DetectionResult:
        """检测角色卡是否需要剧情线。"""
        text = cls._build_search_text(card)
        return cls._analyze(text)

    @classmethod
    def detect_from_text(cls, text: str) -> DetectionResult:
        """检测文本是否需要剧情线。"""
        return cls._analyze(text)

    @classmethod
    def _build_search_text(cls, card: CharaCardV2) -> str:
        """构建搜索文本。"""
        parts = [
            card.data.personality or "",
            card.data.creator_notes or "",
            card.data.scenario or "",
            card.data.description or "",
            card.data.system_prompt or "",
        ]
        return "\n".join(parts)

    @classmethod
    def _analyze(cls, text: str) -> DetectionResult:
        if not text.strip():
            return DetectionResult()

        matched: list[str] = []
        total_score = 0.0
        max_possible = 0

        # 扫描各类模式
        for patterns, category_weight in [
            (cls._TIME_PATTERNS, 0.35),
            (cls._STAGE_PATTERNS, 0.30),
            (cls._BEHAVIOR_PATTERNS, 0.20),
            (cls._ENDING_PATTERNS, 0.15),
        ]:
            for pattern, label, weight in patterns:
                max_possible += 1
                if re.search(pattern, text):
                    matched.append(label)
                    total_score += weight * category_weight

        if not matched:
            return DetectionResult()

        # 计算置信度
        confidence = min(1.0, total_score)

        # 生成建议配置
        suggested = None
        if confidence >= 0.3:
            suggested = cls._suggest_config(text, matched, confidence)

        return DetectionResult(
            has_storyline=confidence >= 0.3,
            confidence=round(confidence, 2),
            matched_patterns=matched,
            suggested_config=suggested,
        )

    @classmethod
    def _suggest_config(
        cls,
        text: str,
        matched_patterns: list[str],
        confidence: float,
    ) -> StorylineConfig:
        """根据检测结果生成建议的剧情线配置。"""
        # 尝试提取天数上限
        max_days = 7  # 默认
        day_match = re.search(r"(\d+)\s*天.*(?:上限|时长|总)", text)
        if not day_match:
            day_match = re.search(r"总.*?(\d+)\s*天", text)
        if day_match:
            max_days = int(day_match.group(1))

        # 尝试提取时间单位
        time_per_turn = 10  # 默认10分钟
        tpt_match = re.search(r"每.*?[+＋加增]\s*(\d+)\s*分钟", text)
        if tpt_match:
            time_per_turn = int(tpt_match.group(1))

        config = StorylineConfig(
            enabled=True,
            time_per_turn=time_per_turn,
            max_duration_minutes=max_days * 1440,
            auto_detected=True,
            detection_confidence=confidence,
        )

        # 如果有阶段类匹配，生成基础阶段
        if any("阶段" in p for p in matched_patterns) or any("演变" in p for p in matched_patterns):
            # 生成基于检测结果的阶段（先用默认 7 天模板，后续可微调）
            config = StorylineConfig.default_7day()
            config.auto_detected = True
            config.detection_confidence = confidence
            config.max_duration_minutes = max_days * 1440
            config.time_per_turn = time_per_turn

        return config
