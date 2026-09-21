"""
动态锚点系统 — 扩展静态锚点为动态锚点

支持锚点优先级、条件激活、情感相关锚点权重调整。
（周期性强化注入回路批6b 项10 已删：should_reinforce/generate_reinforcement
零消费者，唯一使用方是已删除的 enhanced_prompt_engine 死路径。）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("dynamic_anchor")


@dataclass
class ActivationCondition:
    field: str
    operator: str
    value: Any

    def evaluate(self, context: dict[str, Any]) -> bool:
        actual = context.get(self.field)
        if actual is None:
            return True
        try:
            if self.operator == ">=":
                return actual >= self.value  # type: ignore[no-any-return]
            elif self.operator == "<=":
                return actual <= self.value  # type: ignore[no-any-return]
            elif self.operator == "==":
                return actual == self.value  # type: ignore[no-any-return]
            elif self.operator == "in":
                return actual in self.value
            elif self.operator == ">":
                return actual > self.value  # type: ignore[no-any-return]
            elif self.operator == "<":
                return actual < self.value  # type: ignore[no-any-return]
        except (TypeError, ValueError):
            return True
        return True


@dataclass
class DynamicAnchor:
    text: str
    priority: int = 5
    activation_conditions: list[ActivationCondition] = field(default_factory=list)
    emotion_weights: dict[str, float] = field(default_factory=dict)
    active: bool = True


@dataclass
class WeightedAnchor:
    anchor: str
    weight: float


@dataclass
class AnchorContext:
    affinity: int = 0
    energy: float = 1.0
    emotion_type: str = "平常"
    chat_round: int = 0
    time_of_day: str = "daytime"

    def to_condition_context(self) -> dict[str, Any]:
        return {
            "affinity": self.affinity,
            "energy": self.energy,
            "emotion_type": self.emotion_type,
            "time_of_day": self.time_of_day,
        }


@dataclass
class AnchorViolation:
    anchor: str
    weight: float
    score: float
    reason: str


@dataclass
class AnchorCheckResult:
    is_consistent: bool
    overall_score: float
    violations: list[AnchorViolation] = field(default_factory=list)


DEFAULT_DYNAMIC_ANCHORS = [
    DynamicAnchor(
        text="表面傲娇，内心温柔",
        priority=10,
        emotion_weights={"傲娇": 1.5, "温柔": 1.3},
    ),
    DynamicAnchor(
        text="嘴硬心软，从来不说实话",
        priority=9,
        emotion_weights={"傲娇": 1.5},
    ),
    DynamicAnchor(
        text="可以表达想念和喜欢",
        priority=7,
        activation_conditions=[ActivationCondition("affinity", ">=", 6)],
        emotion_weights={"撒娇": 1.5, "温柔": 1.3},
    ),
    DynamicAnchor(
        text="会撒娇、会用亲密称呼",
        priority=6,
        activation_conditions=[ActivationCondition("affinity", ">=", 5)],
        emotion_weights={"撒娇": 2.0},
    ),
]


class DynamicAnchorSystem:
    """动态锚点系统 — 扩展静态锚点"""

    def __init__(
        self,
        base_anchors: list[str] | None = None,
        dynamic_anchors: list[DynamicAnchor] | None = None,
    ):
        self._base_anchors = base_anchors or []
        self._dynamic_anchors: list[DynamicAnchor] = (
            list(DEFAULT_DYNAMIC_ANCHORS) if dynamic_anchors is None else list(dynamic_anchors)
        )

        for anchor in self._base_anchors:
            if not any(da.text == anchor for da in self._dynamic_anchors):
                self._dynamic_anchors.append(DynamicAnchor(text=anchor, priority=8))

        self._dynamic_anchors.sort(key=lambda a: a.priority, reverse=True)

    def register_dynamic_anchor(self, anchor: DynamicAnchor) -> None:
        if not any(da.text == anchor.text for da in self._dynamic_anchors):
            self._dynamic_anchors.append(anchor)
            self._dynamic_anchors.sort(key=lambda a: a.priority, reverse=True)
            logger.info("Registered dynamic anchor: %s (priority=%d)", anchor.text, anchor.priority)

    def get_active_anchors(self, context: AnchorContext) -> list[WeightedAnchor]:
        ctx_dict = context.to_condition_context()
        result = []

        for da in self._dynamic_anchors:
            if not da.active:
                continue

            conditions_met = all(
                cond.evaluate(ctx_dict) for cond in da.activation_conditions
            )
            if not conditions_met:
                continue

            weight = 1.0
            if da.emotion_weights and context.emotion_type in da.emotion_weights:
                weight = da.emotion_weights[context.emotion_type]

            result.append(WeightedAnchor(anchor=da.text, weight=weight))

        return result

    def check_consistency(self, response: str, context: AnchorContext) -> AnchorCheckResult:
        active = self.get_active_anchors(context)
        if not active:
            return AnchorCheckResult(is_consistent=True, overall_score=1.0)

        violations = []
        total_weight = sum(wa.weight for wa in active)
        violation_weight = 0.0

        CONTRADICT_PATTERNS = {  # noqa: N806
            "傲娇": ["坦率", "直说", "明说", "老实说", "我承认"],
            "温柔": ["冷漠", "不在乎", "无所谓"],
            "嘴硬心软": ["我不在乎", "我无所谓", "随便你"],
            "想念": ["不想你", "才没有想"],
        }

        for wa in active:
            for category, keywords in CONTRADICT_PATTERNS.items():
                if category in wa.anchor:
                    for kw in keywords:
                        if kw in response:
                            violations.append(AnchorViolation(
                                anchor=wa.anchor,
                                weight=wa.weight,
                                score=0.3,
                                reason=f"关键词'{kw}'与锚点'{wa.anchor}'矛盾",
                            ))
                            violation_weight += wa.weight
                            break

        overall_score = max(0.0, 1.0 - (violation_weight / total_weight if total_weight > 0 else 0))
        is_consistent = overall_score >= 0.7

        return AnchorCheckResult(
            is_consistent=is_consistent,
            overall_score=overall_score,
            violations=violations,
        )

    def get_all_anchors(self) -> list[str]:
        return [da.text for da in self._dynamic_anchors if da.active]

    def health_check(self) -> dict[str, Any]:
        return {
            "base_anchors_count": len(self._base_anchors),
            "dynamic_anchors_count": len(self._dynamic_anchors),
        }
