from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchestrator.optimized_orchestrator import OptimizedOrchestrator
from tools.base_tool import ToolResult


class _Registry:
    def get_tools_by_permission(self, affinity_level: int = 0):
        return [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "calculator",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


class _Tools:
    def __init__(self):
        self.registry = _Registry()
        self.calls: list[tuple[str, dict, int]] = []

    def dispatch(self, name: str, arguments: dict, affinity_level: int = 0):
        self.calls.append((name, arguments, affinity_level))
        return ToolResult(True, {"temperature": 21})


def test_normal_chat_does_not_pay_tool_intent_llm_call():
    orch = OptimizedOrchestrator()
    tools = _Tools()
    llm = SimpleNamespace(chat_with_tools=lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("ordinary chat must not invoke chat_with_tools")
    ))
    orch.components = {"tools": tools}

    result = asyncio.run(orch._run_tools_if_needed(llm, "你好呀", "system", []))

    assert result == ""
    assert tools.calls == []


def test_async_tool_intent_call_is_awaited_and_dispatches_filtered_schema():
    orch = OptimizedOrchestrator()
    tools = _Tools()
    seen: dict = {}

    async def chat_with_tools(**kwargs):
        seen.update(kwargs)
        return {
            "tool_calls": [
                {
                    "function": {
                        "name": "weather",
                        "arguments": '{"city": "北京"}',
                    }
                }
            ]
        }

    orch.components = {"tools": tools}
    result = asyncio.run(orch._run_tools_if_needed(
        SimpleNamespace(chat_with_tools=chat_with_tools),
        "北京今天天气怎么样",
        "system",
        [],
        affinity_level=2,
    ))

    assert [schema["function"]["name"] for schema in seen["tools"]] == ["weather"]
    assert tools.calls == [("weather", {"city": "北京"}, 2)]
    assert '"temperature": 21' in result


def test_disabled_tools_short_circuit():
    orch = OptimizedOrchestrator()
    orch.components = {"tools": None}
    result = asyncio.run(orch._run_tools_if_needed(
        SimpleNamespace(), "查一下天气", "system", []
    ))
    assert result == ""
