from __future__ import annotations

import ast
import logging
import operator
from datetime import datetime
from typing import Any

from tool_system.base import BaseTool, ToolResult

logger = logging.getLogger("calendar_tool")

# 安全的数学运算符映射
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_expr(expr: str) -> Any:
    """安全地计算数学表达式，不使用 eval()"""
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Num):  # Python 3.7 compat
        return node.n
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        # 零除检查：Div, FloorDiv, Mod 都需要检查
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("division by zero")
        return _SAFE_OPS[type(node.op)](left, right)
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


class CalendarTool(BaseTool):
    name = "get_current_time"
    description = "获取当前日期和时间"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, **kwargs) -> ToolResult:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return ToolResult(True, data={
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": weekdays[now.weekday()],
            "timestamp": now.isoformat(),
        })


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "计算数学表达式"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 '2+3*4'",
            },
        },
        "required": ["expression"],
    }

    def execute(self, expression: str = "", **kwargs) -> ToolResult:
        if not expression:
            return ToolResult(False, error="expression is required")
        try:
            allowed_chars = set("0123456789+-*/.() ")
            if not all(c in allowed_chars for c in expression.replace(" ", "")):
                return ToolResult(False, error="Expression contains disallowed characters")
            result = _safe_eval_expr(expression)
            return ToolResult(True, data={"expression": expression, "result": result})
        except Exception:
            logger.exception("日历计算失败")
            return ToolResult(False, error="calculation_error")
