"""
情境化行为 — 根据情境因素生成行为修饰

根据时间、天气、节日、用户状态等情境因素
动态调整人设表现，生成情境化的行为修饰指令。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("contextual_behavior")


@dataclass
class BehaviorRule:
    condition: str
    modifiers: List[str]
    priority: int = 5

    _ALLOWED_NAMES = frozenset({
        "time_period", "energy", "affinity", "is_weekend", "is_holiday",
        "True", "False", "None",
    })

    def evaluate(self, context_vars: Dict[str, Any]) -> bool:
        import ast
        try:
            tree = ast.parse(self.condition, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id not in self._ALLOWED_NAMES:
                    logger.warning("BehaviorRule condition blocked unsafe name: %s", node.id)
                    return False
            return bool(eval(self.condition, {"__builtins__": {}}, context_vars))
        except Exception:
            return False


DEFAULT_BEHAVIOR_RULES = [
    BehaviorRule(
        condition="time_period == 'late_night'",
        modifiers=["语气更温柔慵懒", "回复简短", "可能表示困了"],
        priority=8,
    ),
    BehaviorRule(
        condition="time_period == 'night'",
        modifiers=["语气柔和", "回复偏简短"],
        priority=7,
    ),
    BehaviorRule(
        condition="time_period == 'morning'",
        modifiers=["可能带点起床气", "语气偏傲娇"],
        priority=6,
    ),
    BehaviorRule(
        condition="energy < 0.3",
        modifiers=["语气疲惫", "回复简短", "可以求安慰"],
        priority=7,
    ),
    BehaviorRule(
        condition="energy > 0.8",
        modifiers=["语气活泼", "可能主动找话题"],
        priority=5,
    ),
    BehaviorRule(
        condition="affinity >= 7",
        modifiers=["主动关心", "表达想念", "语气亲密"],
        priority=6,
    ),
    BehaviorRule(
        condition="affinity <= 2",
        modifiers=["保持距离感", "语气礼貌但疏离"],
        priority=6,
    ),
    BehaviorRule(
        condition="is_weekend == True",
        modifiers=["更放松调皮", "可能撒娇"],
        priority=4,
    ),
    BehaviorRule(
        condition="is_holiday == True",
        modifiers=["特殊节日问候", "更温暖"],
        priority=8,
    ),
]


class ContextualBehavior:
    """情境化行为 — 根据情境因素生成行为修饰"""

    def __init__(self, rules: Optional[List[BehaviorRule]] = None):
        self._rules: List[BehaviorRule] = rules or list(DEFAULT_BEHAVIOR_RULES)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def generate_behavior_prompt(self, context: Any) -> str:
        """根据当前情境生成行为修饰指令

        Args:
            context: PromptContext实例或dict，提供emotion_state/time_context等

        Returns:
            情境化行为修饰文本，无匹配规则时返回空串
        """
        context_vars = self._extract_context_vars(context)
        active_modifiers = []

        for rule in self._rules:
            if rule.evaluate(context_vars):
                active_modifiers.extend(rule.modifiers)

        if not active_modifiers:
            return ""

        unique_modifiers = list(dict.fromkeys(active_modifiers))
        return "# 情境化行为\n当前情境下：\n" + "\n".join(
            f"- {m}" for m in unique_modifiers[:8]
        )

    def _extract_context_vars(self, context: Any) -> Dict[str, Any]:
        from my_character.enhanced_prompt_engine import PromptContext, TimeContext

        try:
            from my_character.persona_utils import extract_context_vars as _extract
        except ImportError:
            _extract = None

        if isinstance(context, PromptContext):
            time_ctx = context.time_context
            if _extract is not None:
                return _extract(emotion_state=context.emotion_state, time_context=time_ctx)
            vars_ = {
                "time_period": "afternoon",
                "energy": 1.0,
                "affinity": 0,
                "is_weekend": False,
                "is_holiday": False,
            }
            if time_ctx:
                vars_["time_period"] = time_ctx.period
                vars_["is_weekend"] = time_ctx.is_weekend
                vars_["is_holiday"] = time_ctx.is_holiday
            if context.emotion_state:
                if hasattr(context.emotion_state, "energy"):
                    vars_["energy"] = context.emotion_state.energy
                if hasattr(context.emotion_state, "affinity"):
                    vars_["affinity"] = context.emotion_state.affinity
            return vars_
        elif isinstance(context, dict):
            if _extract is not None:
                return _extract(emotion_state=None, time_context=None)
            return {
                "time_period": "afternoon",
                "energy": 1.0,
                "affinity": 0,
                "is_weekend": False,
                "is_holiday": False,
            }
        return {
            "time_period": "afternoon",
            "energy": 1.0,
            "affinity": 0,
            "is_weekend": False,
            "is_holiday": False,
        }

    def add_rule(self, rule: BehaviorRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def health_check(self) -> dict:
        return {"rules_count": len(self._rules)}
