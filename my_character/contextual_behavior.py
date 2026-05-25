"""
情境化行为 — 根据情境因素生成行为修饰

根据时间、天气、节日、用户状态等情境因素
动态调整人设表现，生成情境化的行为修饰指令。
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("contextual_behavior")

# AST 安全求值支持
_AST_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
    ast.Not: lambda a: not a,
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
}


def _safe_eval_ast(node: ast.AST, ctx: dict[str, Any]) -> Any:
    """使用 AST 节点遍历安全求值，替代 eval()"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        raise NameError(f"Undefined variable: {node.id}")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _AST_OPS:
        return _AST_OPS[type(node.op)](_safe_eval_ast(node.operand, ctx))  # type: ignore[operator]
    if isinstance(node, ast.BinOp) and type(node.op) in _AST_OPS:
        return _AST_OPS[type(node.op)](  # type: ignore[operator]
            _safe_eval_ast(node.left, ctx), _safe_eval_ast(node.right, ctx)
        )
    if isinstance(node, ast.Compare):
        left = _safe_eval_ast(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators, strict=False):
            if type(op) in _AST_OPS:
                left = _AST_OPS[type(op)](left, _safe_eval_ast(comp, ctx))  # type: ignore[operator]
            else:
                raise ValueError(f"Unsupported comparison: {ast.dump(op)}")
        return left
    if isinstance(node, ast.BoolOp):
        # 短路求值：and/or 应该逐个求值，遇到确定结果立即停止
        if isinstance(node.op, ast.And):
            result = _safe_eval_ast(node.values[0], ctx)
            for val_node in node.values[1:]:
                if not result:  # 短路：遇到 False 立即停止
                    return result
                result = result and _safe_eval_ast(val_node, ctx)
            return result
        elif isinstance(node.op, ast.Or):
            result = _safe_eval_ast(node.values[0], ctx)
            for val_node in node.values[1:]:
                if result:  # 短路：遇到 True 立即停止
                    return result
                result = result or _safe_eval_ast(val_node, ctx)
            return result
        else:
            raise ValueError(f"Unsupported boolean operator: {ast.dump(node.op)}")
    raise ValueError(f"Unsupported AST node: {ast.dump(node)}")


@dataclass
class BehaviorRule:
    condition: str
    modifiers: list[str]
    priority: int = 5

    _ALLOWED_NAMES = frozenset({
        "time_period", "energy", "affinity", "is_weekend", "is_holiday",
        "True", "False", "None",
    })

    def evaluate(self, context_vars: dict[str, Any]) -> bool:
        import ast

        try:
            tree = ast.parse(self.condition, mode="eval")
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id not in self._ALLOWED_NAMES:
                    logger.warning("BehaviorRule condition blocked unsafe name: %s", node.id)
                    return False
                # 阻止所有函数调用和属性访问
                if isinstance(node, (ast.Call, ast.Attribute)):
                    logger.warning("BehaviorRule condition blocked unsafe operation: %s", ast.dump(node))
                    return False
            # 使用 AST 安全求值替代 eval
            return _safe_eval_ast(tree.body, context_vars)  # type: ignore[no-any-return]  # noqa: BLE001
        except Exception:  # noqa: BLE001
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

    def __init__(self, rules: list[BehaviorRule] | None = None):
        self._rules: list[BehaviorRule] = rules or list(DEFAULT_BEHAVIOR_RULES)
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

    def _extract_context_vars(self, context: Any) -> dict[str, Any]:
        from my_character.enhanced_prompt_engine import PromptContext

        try:
            from my_character.persona_utils import extract_context_vars as _extract
        except ImportError:
            _extract = None  # type: ignore[assignment]

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
