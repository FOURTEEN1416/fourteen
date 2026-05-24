"""
认知扭曲检测 (Cognitive Distortions)

参考文献:
  - Beck, A.T. (1976). Cognitive Therapy and the Emotional Disorders.
  - Burns, D.D. (1980). Feeling Good: The New Mood Therapy.
  - Yurica & DiTomasso (2005). Cognitive Distortions Scale.

10种常见认知扭曲 (Burns, 1980):
  1. All-or-Nothing (全或无思维)
  2. Overgeneralization (过度概括)
  3. Mental Filter (心理过滤)
  4. Disqualifying the Positive (否定正面)
  5. Jumping to Conclusions (妄下结论)
     a. Mind Reading (读心术)
     b. Fortune Telling (算命)
  6. Magnification/Minimization (夸大/缩小)
  7. Emotional Reasoning (情绪推理)
  8. Should Statements (应该陈述)
  9. Labeling (贴标签)
  10. Personalization (个人化)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CognitiveDistortionHit:
    """单次认知扭曲匹配"""
    type: str = ""
    subtype: str = ""
    matched_text: str = ""
    confidence: float = 0.0


@dataclass
class CognitiveDistortionResult:
    """认知扭曲检测结果"""
    hits: list[CognitiveDistortionHit] = field(default_factory=list)
    total_count: int = 0
    dominant_pattern: str = "none"
    severity: str = "none"  # none / mild / moderate / frequent

    def to_dict(self) -> dict:
        by_type: dict[str, int] = {}
        for h in self.hits:
            by_type[h.type] = by_type.get(h.type, 0) + 1
        return {
            "total_count": self.total_count,
            "dominant_pattern": self.dominant_pattern,
            "severity": self.severity,
            "by_type": by_type,
            "recent": [{"type": h.type, "subtype": h.subtype,
                         "matched": h.matched_text[:50]} for h in self.hits[-10:]],
        }


# 10种认知扭曲的匹配模式
_DISTORTION_PATTERNS: dict[str, list[tuple]] = {
    "all_or_nothing": [
        (re.compile(r"(从来.{0,3}(不|没|都|总是|一直))"), 0.7, "全或无"),
        (re.compile(r"(永远.{0,3}(不|都|就|会))"), 0.6, "全或无"),
        (re.compile(r"(完全.{0,3}(没有|不行|失败|没用|不能))"), 0.7, "全或无"),
        (re.compile(r"(一点.{0,3}(都不|也没|都不行))"), 0.5, "全或无"),
        (re.compile(r"(所有人.{0,3}(都|都不))"), 0.5, "全或无"),
    ],
    "overgeneralization": [
        (re.compile(r"(每次.{0,3}(都|总是))"), 0.6, "过度概括"),
        (re.compile(r"(总是.{0,3}(这样|如此|一样))"), 0.7, "过度概括"),
        (re.compile(r"(一辈子.{0,3}(都|就))"), 0.5, "过度概括"),
        (re.compile(r"(什么都.{0,3}(做不好|不行|不会))"), 0.6, "过度概括"),
    ],
    "mental_filter": [
        (re.compile(r"(就.{0,5}(只看|只记得|只注意|只关注).{0,5}(不好|糟糕|失败|缺点))"), 0.6, "心理过滤"),
        (re.compile(r"(虽然.{0,10}但是.{0,10}(还是|依然).{0,5}(不好|不够|不行))"), 0.5, "心理过滤"),
    ],
    "disqualifying_positive": [
        (re.compile(r"(那.{0,3}(不算|不是|不叫).{0,3}(成功|好|厉害|优秀))"), 0.7, "否定正面"),
        (re.compile(r"(就.{0,3}(运气|碰巧|侥幸).{0,3}(而已|罢了))"), 0.7, "否定正面"),
        (re.compile(r"(这次.{0,3}(不算|运气).{0,3}(下次|以后))"), 0.5, "否定正面"),
    ],
    "jumping_to_conclusions": [
        (re.compile(r"(肯定.{0,3}(觉得|认为|想).{0,3}我.{0,5}(不好|不行|傻|笨|讨厌))"), 0.7, "读心术"),
        (re.compile(r"(他.{0,3}(一定|肯定|绝对).{0,3}(觉得|认为|看不上))"), 0.6, "读心术"),
        (re.compile(r"(到时候.{0,3}(一定|肯定|绝对|会).{0,3}(失败|不行|搞砸|出问题))"), 0.7, "算命"),
        (re.compile(r"(万一.{0,3}(怎么办|怎么样|不行))"), 0.5, "算命"),
    ],
    "magnification": [
        (re.compile(r"(完蛋|毁了|完了|彻底.{0,3}(失败|不行|没救))"), 0.6, "灾难化"),
        (re.compile(r"(太.{0,3}(可怕|恐怖|糟糕|严重))"), 0.5, "灾难化"),
        (re.compile(r"(天.{0,3}(都.{0,3}(塌了|要塌)))"), 0.4, "灾难化"),
    ],
    "emotional_reasoning": [
        (re.compile(r"(因为.{0,3}(害怕|担心|焦虑).{0,3}所以.{0,3}(一定|肯定))"), 0.7, "情绪推理"),
        (re.compile(r"(我.{0,3}(觉得|感觉).{0,3}(不好|不对|不对劲).{0,3}所以.{0,3}(就是))"), 0.6, "情绪推理"),
        (re.compile(r"(因为.{0,3}(难受|不开心).{0,3}所以.{0,3}(是.{0,3}(我的.{0,3}(错|问题))))"), 0.5, "情绪推理"),
    ],
    "should_statements": [
        (re.compile(r"(应该.{0,3}(要|做|能|更))"), 0.5, "应该陈述"),
        (re.compile(r"(必须.{0,3}(要|得|做到))"), 0.5, "应该陈述"),
        (re.compile(r"(不得不.{0,3}(这样|那么|如此))"), 0.4, "应该陈述"),
    ],
    "labeling": [
        (re.compile(r"(我.{0,3}(就是.{0,3}(个|一个).{0,3}(废物|笨蛋|傻瓜|loser|失败者|垃圾)))"), 0.8, "贴标签"),
        (re.compile(r"(我.{0,3}(太.{0,3}(笨|蠢|傻|差))"), 0.6, "贴标签"),
    ],
    "personalization": [
        (re.compile(r"(都是.{0,3}(因为|由于).{0,3}我)"), 0.6, "个人化"),
        (re.compile(r"(都怪我)"), 0.7, "个人化"),
        (re.compile(r"(如果.{0,5}(不是|没有).{0,3}我.{0,5}(就不会|就不会这样))"), 0.7, "个人化"),
    ],
}


class CognitiveDistortionDetector:
    """认知扭曲检测器

    基于 Burns(1980) 的 10 种认知扭曲框架。
    使用正则匹配 + 可选的 LLM 深度分析。
    """

    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway
        self.total_detections = 0

    def detect(self, text: str) -> CognitiveDistortionResult:
        """检测文本中的认知扭曲"""
        result = CognitiveDistortionResult()

        for distortion_type, patterns in _DISTORTION_PATTERNS.items():
            for pattern, confidence, subtype in patterns:
                match = pattern.search(text)
                if match:
                    hit = CognitiveDistortionHit(
                        type=distortion_type,
                        subtype=subtype,
                        matched_text=match.group(0),
                        confidence=confidence,
                    )
                    result.hits.append(hit)

        result.total_count = len(result.hits)

        # 找主导模式
        if result.hits:
            type_counts: dict[str, int] = {}
            for h in result.hits:
                type_counts[h.type] = type_counts.get(h.type, 0) + 1
            result.dominant_pattern = max(type_counts, key=lambda k: type_counts[k])

        # 严重度
        if result.total_count >= 5:
            result.severity = "frequent"
        elif result.total_count >= 3:
            result.severity = "moderate"
        elif result.total_count >= 1:
            result.severity = "mild"
        else:
            result.severity = "none"

        self.total_detections += result.total_count
        return result

    def to_prompt_enhancement(self, result: CognitiveDistortionResult) -> str:
        """生成用于LLM提示词的认知扭曲缓解建议"""
        if result.severity == "none":
            return ""

        type_cn = {
            "all_or_nothing": "全或无思维",
            "overgeneralization": "过度概括",
            "mental_filter": "心理过滤",
            "disqualifying_positive": "否定正面",
            "jumping_to_conclusions": "妄下结论",
            "magnification": "灾难化",
            "emotional_reasoning": "情绪推理",
            "should_statements": "应该陈述",
            "labeling": "贴标签",
            "personalization": "个人化",
        }

        lines = ["[认知扭曲提示]"]
        lines.append(f"- 用户表现出{type_cn.get(result.dominant_pattern, result.dominant_pattern)}的思维模式")
        lines.append("- 回应时避免强化这些认知扭曲，温和地提供替代视角")
        return "\n".join(lines)
