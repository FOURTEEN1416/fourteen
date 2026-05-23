from __future__ import annotations

from datetime import datetime

from tool_system.base import BaseTool, ToolResult


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

    SAFE_BUILTINS = {
        "abs": abs, "round": round, "min": min, "max": max,
        "pow": pow, "int": int, "float": float,
    }

    def execute(self, expression: str = "", **kwargs) -> ToolResult:
        if not expression:
            return ToolResult(False, error="expression is required")
        try:
            allowed_chars = set("0123456789+-*/.() ")
            if not all(c in allowed_chars for c in expression.replace(" ", "")):
                return ToolResult(False, error="Expression contains disallowed characters")
            result = eval(expression, {"__builtins__": {}}, self.SAFE_BUILTINS)
            return ToolResult(True, data={"expression": expression, "result": result})
        except Exception as e:
            return ToolResult(False, error=f"Calculation error: {e}")
