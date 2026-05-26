"""T-19: 工具健康检测+指令处理器 单元测试"""
from unittest.mock import MagicMock, PropertyMock


class TestToolRegistryHealthCheck:
    def test_health_check_all_available(self):
        from tool_system.base_tool import BaseTool, ToolRegistry
        registry = ToolRegistry()
        tool = MagicMock(spec=BaseTool)
        tool.name = "test_tool"
        tool._plugin = MagicMock()
        registry.register(tool)
        result = registry.health_check_all()
        assert "test_tool" in result
        assert result["test_tool"]["available"] is True

    def test_health_check_plugin_not_loaded(self):
        from tool_system.base_tool import BaseTool, ToolRegistry
        registry = ToolRegistry()
        tool = MagicMock(spec=BaseTool)
        tool.name = "broken_tool"
        type(tool)._plugin = PropertyMock(return_value=None)
        registry.register(tool)
        result = registry.health_check_all()
        assert result["broken_tool"]["available"] is False
        assert "plugin not loaded" in result["broken_tool"]["error"]


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

