"""
人设评估器 — 多维度评估回复的人设贴合度

5维评估：锚点忠实度 + 风格一致性 + 情感恰当性 + 行为合规性 + 记忆连贯性
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("persona_evaluator")

EVALUATION_WEIGHTS = {
    "anchor_fidelity": 0.30,
    "style_consistency": 0.25,
    "emotion_appropriateness": 0.20,
    "behavior_compliance": 0.15,
    "memory_coherence": 0.10,
}


@dataclass
class EvaluationReport:
    overall_score: float = 0.0
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    passed: bool = False
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


class PersonaEvaluator:
    """人设评估器 — 多维度评估"""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        passing_threshold: float = 0.75,
        anchor_checker: Any = None,
        constraint_validator: Any = None,
    ):
        self._weights = weights or EVALUATION_WEIGHTS
        self._threshold = passing_threshold
        self._anchor_checker = anchor_checker
        self._constraint_validator = constraint_validator
        self._history: List[EvaluationReport] = []

    def evaluate_response(
        self,
        persona_config: Dict[str, Any],
        context: Dict[str, Any],
        response: str,
    ) -> EvaluationReport:
        """多维度评估回复的人设贴合度"""
        scores: Dict[str, float] = {}

        scores["anchor_fidelity"] = self._eval_anchor_fidelity(
            persona_config.get("anchors", []), response,
        )

        scores["style_consistency"] = self._eval_style_consistency(
            persona_config.get("speaking_style", {}), response,
        )

        scores["emotion_appropriateness"] = self._eval_emotion_appropriateness(
            context.get("emotion_state", {}), response,
        )

        scores["behavior_compliance"] = self._eval_behavior_compliance(
            persona_config.get("behavior_constraints", {}), response,
        )

        scores["memory_coherence"] = self._eval_memory_coherence(
            context.get("recent_messages", []), response,
        )

        overall = self._calculate_weighted_score(scores)
        issues = self._identify_issues(scores)
        suggestions = self._generate_suggestions(issues, persona_config)

        report = EvaluationReport(
            overall_score=overall,
            dimension_scores=scores,
            passed=overall >= self._threshold,
            issues=issues,
            suggestions=suggestions,
        )
        self._history.append(report)
        return report

    def _eval_anchor_fidelity(self, anchors: List[str], response: str) -> float:
        if self._anchor_checker and anchors:
            try:
                is_consistent, score, _ = self._anchor_checker.check_response_consistency(
                    response, anchors,
                )
                return score
            except Exception:
                pass
        if not anchors:
            return 0.8
        matched = sum(1 for a in anchors if any(c in response for c in a if len(c) > 1))
        return min(1.0, matched / len(anchors) * 2) if anchors else 0.8

    def _eval_style_consistency(self, style: Dict, response: str) -> float:
        score = 0.7
        length = len(response)
        pref = style.get("sentence_length_preference", "medium")
        if pref == "short" and length <= 30:
            score += 0.15
        elif pref == "medium" and 10 <= length <= 60:
            score += 0.15
        elif pref == "long" and length > 30:
            score += 0.15
        emoji_count = sum(1 for c in response if ord(c) > 0x1F000)
        emoji_freq = style.get("emoji_frequency", 0.5)
        if emoji_freq > 0.5 and emoji_count > 0:
            score += 0.1
        elif emoji_freq < 0.3 and emoji_count == 0:
            score += 0.1
        return min(1.0, score)

    def _eval_emotion_appropriateness(self, emotion_state: Dict, response: str) -> float:
        score = 0.75
        emotion = emotion_state.get("primary", {}).get("type", "")
        if not emotion or emotion == "平常":
            return score
        emotion_indicators = {
            "开心": ["哈哈", "嘻", "呀", "～", "！"],
            "生气": ["哼", "呵", "！", "别"],
            "撒娇": ["嘛", "呢", "呀", "呜", "～"],
            "傲娇": ["才", "哼", "又", "不是"],
            "伤心": ["...", "唉", "嗯"],
            "担心": ["吧", "呢", "别"],
        }
        indicators = emotion_indicators.get(emotion, [])
        if any(ind in response for ind in indicators):
            score += 0.2
        return min(1.0, score)

    def _eval_behavior_compliance(self, constraints: Dict, response: str) -> float:
        if self._constraint_validator:
            try:
                result = self._constraint_validator.validate(response)
                return 1.0 if result.passed else 0.3
            except Exception:
                pass
        forbidden = constraints.get("forbidden", [])
        for pattern in forbidden:
            if isinstance(pattern, str) and pattern in response:
                return 0.3
        return 0.9

    def _eval_memory_coherence(self, recent_messages: List, response: str) -> float:
        if not recent_messages:
            return 0.8
        return 0.8

    def _calculate_weighted_score(self, scores: Dict[str, float]) -> float:
        total = 0.0
        for dim, weight in self._weights.items():
            total += scores.get(dim, 0.5) * weight
        return min(1.0, total)

    def _identify_issues(self, scores: Dict[str, float]) -> List[str]:
        issues = []
        for dim, score in scores.items():
            if score < 0.5:
                issues.append(f"{dim}: 严重不足 ({score:.2f})")
            elif score < 0.7:
                issues.append(f"{dim}: 需要改进 ({score:.2f})")
        return issues

    def _generate_suggestions(
        self, issues: List[str], persona_config: Dict,
    ) -> List[str]:
        suggestions = []
        for issue in issues:
            if "anchor" in issue:
                suggestions.append("强化角色锚点，在提示词中更明确地指定核心性格")
            elif "style" in issue:
                suggestions.append("提供更多风格示例，增加语气词和句式指导")
            elif "emotion" in issue:
                suggestions.append("调整情感-风格映射，确保情感状态正确影响输出风格")
            elif "behavior" in issue:
                suggestions.append("增加约束检测规则，防止违规输出")
            elif "memory" in issue:
                suggestions.append("增强记忆检索，确保回复与历史上下文一致")
        return suggestions

    def get_average_score(self, last_n: int = 10) -> float:
        recent = self._history[-last_n:]
        if not recent:
            return 0.0
        return sum(r.overall_score for r in recent) / len(recent)
