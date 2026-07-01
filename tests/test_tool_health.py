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


class TestCommandHandlerSendSticker:
    def test_send_sticker_with_recommend(self):
        from shisi.wechat.command_handler import WeChatCommandHandler
        from shisi.wechat.command_parser import Command
        sticker_mgr = MagicMock()
        sticker_mgr.recommend.return_value = [
            {"sticker_id": "happy_01", "category": "开心", "file_path": "/tmp/happy.png"},
        ]
        handler = WeChatCommandHandler(sticker_manager=sticker_mgr)
        cmd = Command(action="send_sticker", raw="发表情", params={})
        result = handler._handle_send_sticker(cmd, "")
        assert "happy_01" in result
        assert "表情推荐" in result

    def test_send_sticker_no_match(self):
        from shisi.wechat.command_handler import WeChatCommandHandler
        from shisi.wechat.command_parser import Command
        sticker_mgr = MagicMock()
        sticker_mgr.recommend.return_value = []
        handler = WeChatCommandHandler(sticker_manager=sticker_mgr)
        cmd = Command(action="send_sticker", raw="发表情", params={})
        result = handler._handle_send_sticker(cmd, "")
        assert "没有匹配" in result

