from __future__ import annotations

import logging

from tool_system.base_tool import BaseTool, ToolResult

logger = logging.getLogger("reminder_tool")


class ReminderTool(BaseTool):
    name = "set_reminder"
    description = "设置提醒事项"
    permission_level = "friend"
    parameters_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "提醒内容",
            },
            "trigger_time": {
                "type": "string",
                "description": "触发时间，格式YYYY-MM-DD HH:MM",
            },
        },
        "required": ["content"],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def execute(self, content: str = "", trigger_time: str | None = None, **kwargs) -> ToolResult:
        if not content:
            return ToolResult(False, error="content is required")
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        try:
            reminder_id = self._sm.add_reminder(content, trigger_time)
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": trigger_time,
                "message": f"已设置提醒：{content}",
            })
        except Exception:
            logger.exception("设置提醒失败")
            return ToolResult(False, error="reminder_set_failed")


class CalendarQueryTool(BaseTool):
    name = "query_reminders"
    description = "查询待触发的提醒事项"
    permission_level = "friend"
    parameters_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def execute(self, **kwargs) -> ToolResult:
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        try:
            reminders = self._sm.get_pending_reminders()
            return ToolResult(True, data=reminders)
        except Exception:
            logger.exception("查询提醒失败")
            return ToolResult(False, error="reminder_query_failed")
