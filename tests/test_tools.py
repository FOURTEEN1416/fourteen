"""单元测试: builtin 工具注册/权限/调度"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tools.base_tool import BaseTool, ToolResult, ToolRegistry, ToolDispatcher
from tools.builtin.calendar_tool import CalendarTool, CalculatorTool
from tools.builtin.weather_tool import WeatherTool
from tools.builtin.time_awareness_tool import TimeAwarenessTool


# ── Helper: 验证 OpenAI Function Calling Schema ──

def _assert_valid_fc_schema(schema: dict) -> None:
    """验证 schema 符合 OpenAI function calling 格式"""
    assert schema["type"] == "function"
    func = schema["function"]
    assert "name" in func
    assert isinstance(func["name"], str)
    assert "description" in func
    assert isinstance(func["description"], str)
    assert "parameters" in func
    params = func["parameters"]
    assert params["type"] == "object"
    assert "properties" in params


class TestCalendarToolSchema:
    def test_to_openai_fc_schema(self) -> None:
        tool = CalendarTool()
        schema = tool.to_openai_fc_schema()
        _assert_valid_fc_schema(schema)
        assert schema["function"]["name"] == "calendar"

    def test_execute_returns_data(self) -> None:
        tool = CalendarTool()
        result = tool.execute()
        assert result.success
        assert "date" in result.data
        assert "time" in result.data
        assert "weekday" in result.data


class TestCalculatorTool:
    def test_schema(self) -> None:
        tool = CalculatorTool()
        schema = tool.to_openai_fc_schema()
        _assert_valid_fc_schema(schema)
        assert schema["function"]["name"] == "calculator"

    @pytest.mark.parametrize("expr,expected", [
        ("2+3", 5),
        ("10-4", 6),
        ("3*7", 21),
        ("15/3", 5.0),
        ("2+3*4", 14),
        ("(2+3)*4", 20),
        ("10+3", 13),
        ("2**10", 1024),
        ("-5+3", -2),
        ("+7", 7),
    ])
    def test_safe_eval_valid(self, expr: str, expected: int | float) -> None:
        tool = CalculatorTool()
        result = tool.execute(expression=expr)
        assert result.success
        assert result.data["expression"] == expr
        assert result.data["result"] == expected

    def test_empty_expression(self) -> None:
        tool = CalculatorTool()
        result = tool.execute()
        assert not result.success

    def test_empty_string_expression(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(expression="")
        assert not result.success

    def test_rejects_dangerous_chars(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(expression="__import__('os')")
        assert not result.success

    def test_rejects_letters(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(expression="abc+2")
        assert not result.success

    def test_rejects_division_by_zero(self) -> None:
        tool = CalculatorTool()
        result = tool.execute(expression="1/0")
        assert not result.success


class TestWeatherToolSchema:
    def test_to_openai_fc_schema(self) -> None:
        tool = WeatherTool()
        schema = tool.to_openai_fc_schema()
        _assert_valid_fc_schema(schema)
        assert schema["function"]["name"] == "weather"
        assert "city" in schema["function"]["parameters"]["properties"]

    def test_health_check_returns_dict(self) -> None:
        tool = WeatherTool()
        status = tool.health_check()
        assert isinstance(status, dict)
        assert "available" in status


class TestTimeAwarenessToolSchema:
    def test_to_openai_fc_schema(self) -> None:
        tool = TimeAwarenessTool()
        schema = tool.to_openai_fc_schema()
        _assert_valid_fc_schema(schema)
        assert schema["function"]["name"] == "time_awareness"
        assert "action" in schema["function"]["parameters"]["properties"]


# ── 辅助工具 ──

class SimpleToolNoPermission(BaseTool):
    name = "simple_public"
    description = "A public tool"
    permission_level = "public"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(True, data={"done": True})


class FriendOnlyTool(BaseTool):
    name = "friend_tool"
    description = "Friend only"
    permission_level = "friend"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(True, data={"level": "friend"})


class IntimateOnlyTool(BaseTool):
    name = "intimate_tool"
    description = "Intimate only"
    permission_level = "intimate"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(True, data={"level": "intimate"})


class AdminTool(BaseTool):
    name = "admin_tool"
    description = "Admin only"
    permission_level = "admin"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(True, data={"level": "admin"})


class BrokenTool(BaseTool):
    name = "broken_tool"
    description = "A broken tool"

    def execute(self, **kwargs) -> ToolResult:
        raise RuntimeError("broken")

    def health_check(self) -> dict:
        raise RuntimeError("health_check_crashed")


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        tool = SimpleToolNoPermission()
        registry.register(tool)
        assert registry.get("simple_public") is tool
        assert registry.get("nonexistent") is None

    def test_unregister(self) -> None:
        registry = ToolRegistry()
        tool = SimpleToolNoPermission()
        registry.register(tool)
        registry.unregister("simple_public")
        assert registry.get("simple_public") is None

    def test_unregister_nonexistent(self) -> None:
        """反注册不存在的工具不应报错"""
        registry = ToolRegistry()
        registry.unregister("nothing")  # 不应抛异常

    def test_tool_names(self) -> None:
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        registry.register(FriendOnlyTool())
        assert set(registry.tool_names) == {"simple_public", "friend_tool"}

    def test_get_all_schemas(self) -> None:
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        registry.register(FriendOnlyTool())
        schemas = registry.get_all_schemas()
        assert len(schemas) == 2
        names = [s["function"]["name"] for s in schemas]
        assert "simple_public" in names
        assert "friend_tool" in names

    def test_get_tools_by_permission_public(self) -> None:
        """min_affinity=0 → 只返回 public 工具"""
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        registry.register(FriendOnlyTool())
        registry.register(IntimateOnlyTool())
        schemas = registry.get_tools_by_permission(min_affinity=0)
        names = [s["function"]["name"] for s in schemas]
        assert "simple_public" in names
        assert "friend_tool" not in names

    def test_get_tools_by_permission_friend(self) -> None:
        """min_affinity=2 → 返回 public + friend"""
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        registry.register(FriendOnlyTool())
        registry.register(IntimateOnlyTool())
        schemas = registry.get_tools_by_permission(min_affinity=2)
        names = [s["function"]["name"] for s in schemas]
        assert "simple_public" in names
        assert "friend_tool" in names
        assert "intimate_tool" not in names

    def test_health_check_all(self) -> None:
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        results = registry.health_check_all()
        assert "simple_public" in results
        assert results["simple_public"]["available"] is True

    def test_health_check_all_catches_exceptions(self) -> None:
        registry = ToolRegistry()
        registry.register(BrokenTool())
        results = registry.health_check_all()
        assert "broken_tool" in results
        assert results["broken_tool"]["available"] is False


class TestToolDispatcher:
    def test_dispatch_finds_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        dispatcher = ToolDispatcher(registry, rate_limit_per_minute=999)
        result = dispatcher.dispatch("simple_public", {}, affinity_level=0)
        assert result.success

    def test_dispatch_unknown_tool(self) -> None:
        dispatcher = ToolDispatcher(ToolRegistry())
        result = dispatcher.dispatch("nonexistent", {})
        assert not result.success

    def test_dispatch_permission_denied(self) -> None:
        registry = ToolRegistry()
        registry.register(IntimateOnlyTool())
        dispatcher = ToolDispatcher(registry)
        result = dispatcher.dispatch("intimate_tool", {}, affinity_level=0)
        assert not result.success

    def test_dispatch_permission_granted(self) -> None:
        registry = ToolRegistry()
        registry.register(IntimateOnlyTool())
        dispatcher = ToolDispatcher(registry)
        result = dispatcher.dispatch("intimate_tool", {}, affinity_level=6)
        assert result.success

    def test_dispatch_rate_limited(self) -> None:
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        dispatcher = ToolDispatcher(registry, rate_limit_per_minute=1)
        # 第一次应该成功
        result = dispatcher.dispatch("simple_public", {}, affinity_level=0)
        assert result.success
        # 第二次应该被限速
        result = dispatcher.dispatch("simple_public", {}, affinity_level=0)
        assert not result.success

    def test_dispatch_friend_allows_public_friend(self) -> None:
        """朋友阶段: public + friend 工具可访问"""
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        registry.register(FriendOnlyTool())
        registry.register(IntimateOnlyTool())
        dispatcher = ToolDispatcher(registry, rate_limit_per_minute=999)
        r1 = dispatcher.dispatch("simple_public", {}, affinity_level=2)
        r2 = dispatcher.dispatch("friend_tool", {}, affinity_level=2)
        r3 = dispatcher.dispatch("intimate_tool", {}, affinity_level=2)
        assert r1.success
        assert r2.success
        assert not r3.success

    def test_check_permission_public(self) -> None:
        """public 工具 affinity 0 即可访问"""
        registry = ToolRegistry()
        dispatcher = ToolDispatcher(registry)
        tool = SimpleToolNoPermission()
        assert dispatcher._check_permission(tool, 0) is True
        assert dispatcher._check_permission(tool, 10) is True

    def test_check_permission_admin(self) -> None:
        """admin 工具需 affinity 99"""
        registry = ToolRegistry()
        dispatcher = ToolDispatcher(registry)
        tool = AdminTool()
        assert dispatcher._check_permission(tool, 90) is False
        assert dispatcher._check_permission(tool, 99) is True
        assert dispatcher._check_permission(tool, 100) is True


class TestDispatcherEdgeCases:
    def test_empty_registry(self) -> None:
        """空注册表不应崩溃"""
        dispatcher = ToolDispatcher(ToolRegistry())
        result = dispatcher.dispatch("anything", {})
        assert not result.success

    def test_trace_id_noop(self) -> None:
        """trace_id 不应影响调度"""
        registry = ToolRegistry()
        registry.register(SimpleToolNoPermission())
        dispatcher = ToolDispatcher(registry, rate_limit_per_minute=999)
        result = dispatcher.dispatch("simple_public", {}, affinity_level=0, trace_id="test123")
        assert result.success


class TestToolResult:
    def test_to_dict(self) -> None:
        r = ToolResult(True, data={"a": 1})
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"]["a"] == 1

    def test_to_dict_error(self) -> None:
        r = ToolResult(False, error="test error")
        d = r.to_dict()
        assert d["success"] is False
        assert "test error" in d["error"]

    def test_to_fc_result_success(self) -> None:
        r = ToolResult(True, data={"msg": "ok"})
        s = r.to_fc_result()
        assert '"msg": "ok"' in s

    def test_to_fc_result_error(self) -> None:
        r = ToolResult(False, error="failed")
        s = r.to_fc_result()
        assert "failed" in s
