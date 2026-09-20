from __future__ import annotations

import logging

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("reminder_tool")


class ReminderTool(BaseTool):
    name = "set_reminder"
    description = "设置提醒事项（到点会主动发消息提醒用户）"
    permission_level = "friend"
    # 编排器终审调度本工具时注入调用归属（_meta），提醒按会话投递
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "提醒内容",
            },
            "trigger_time": {
                "type": "string",
                "description": (
                    "触发时间，北京时间，格式 YYYY-MM-DD HH:MM（24 小时制）。"
                    "相对表达（明早六点/半小时后）必须先按当前时间换算成该格式"
                ),
            },
        },
        "required": ["content", "trigger_time"],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def execute(self, content: str = "", trigger_time: str | None = None,
                **kwargs) -> ToolResult:
        if not content:
            return ToolResult(False, error="content is required")
        if not trigger_time:
            return ToolResult(False, error="trigger_time_required")
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        # 归属由服务端注入（_meta），LLM 参数里的同名键不可信
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        user_id = (meta or {}).get("user_id")
        try:
            reminder_id = self._sm.add_reminder(
                content, trigger_time, session_key=session_key, user_id=user_id,
            )
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": trigger_time,
                "deliver_to": session_key or "(无投递目标)",
                "message": f"已设置提醒：{content}，时间 {trigger_time}",
            })
        except Exception:
            logger.exception("设置提醒失败")
            return ToolResult(False, error="reminder_set_failed")


class CalendarQueryTool(BaseTool):
    name = "query_reminders"
    description = "查询待触发的提醒事项"
    permission_level = "friend"
    wants_call_context = True
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
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        try:
            reminders = self._sm.get_pending_reminders(session_key=session_key)
            return ToolResult(True, data=reminders)
        except Exception:
            logger.exception("查询提醒失败")
            return ToolResult(False, error="reminder_query_failed")
