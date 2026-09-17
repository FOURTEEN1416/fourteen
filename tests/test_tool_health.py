"""T-19: 工具健康检测+指令处理器 单元测试"""
from unittest.mock import MagicMock

from tools.base_tool import BaseTool, ToolResult


class FakeTool(BaseTool):
    name = "fake_tool"

    def __init__(self, plugin=None, sm=None):
        self._plugin = plugin
        self._sm = sm

    def execute(self, **kwargs):
        return ToolResult(True)


class TestToolRegistryHealthCheck:
    def test_health_check_all_available(self):
        from tools.base_tool import ToolRegistry
        registry = ToolRegistry()
        registry.register(FakeTool(plugin=object(), sm=object()))
        result = registry.health_check_all()
        assert "fake_tool" in result
        assert result["fake_tool"]["available"] is True
        assert result["fake_tool"]["error"] == ""

    def test_health_check_plugin_not_loaded(self):
        from tools.base_tool import ToolRegistry
        registry = ToolRegistry()
        registry.register(FakeTool(plugin=None))
        result = registry.health_check_all()
        assert "fake_tool" in result
        assert result["fake_tool"]["available"] is False
        assert "plugin not loaded" in result["fake_tool"]["error"]


# 2026-09-17：TestCommandHandlerSendSticker 随微信指令处理器删除而移除
# （shisi/wechat/command_handler.py 生产链路从未接线，用户裁决清洗）
