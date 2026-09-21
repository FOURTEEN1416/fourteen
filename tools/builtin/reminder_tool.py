from __future__ import annotations

import logging
from datetime import datetime, timedelta

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("reminder_tool")

# trigger_time 的合法格式与归一输出（到期轮询按字符串比较，必须与
# StructuredMemory._now_local() 的 "%Y-%m-%d %H:%M:%S" 同构）。
_TRIGGER_ACCEPT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")
_TRIGGER_STORE_FORMAT = "%Y-%m-%d %H:%M:%S"
# 过去时刻容忍窗：容忍几分钟内的舍入误差（"现在马上提醒"），超过即视为
# LLM 换算错误（算成了昨天/上礼拜）——照单全收会在下一拍立即投递并假成功。
_PAST_TOLERANCE = timedelta(minutes=5)


class TriggerTimeError(ValueError):
    """trigger_time 非法（格式不符 / 不是真实存在的日历时刻 / 早于容忍窗）。"""


def normalize_trigger_time(raw: str, now: datetime | None = None) -> str:
    """校验并归一 trigger_time；非法抛 :class:`TriggerTimeError`。

    2026-09-22 根治：旧实现把 LLM 给的任意字符串直接落库，到期判定是
    **字符串比较** —— 「明早六点」「6:00」等畸形值按 Unicode 字典序恒大于
    ``2026-…`` 前缀，**永不到期、永不投递、永不判死**，pending 却已 fulfilled
    （v1.30「说了会叫却没叫」修复后的残留通道）。现在格式/日历有效性/过去
    时刻三关全过才落库，失败信息回给终审模型重问或 ask_user。
    """
    text = str(raw or "").strip()
    if not text:
        raise TriggerTimeError("trigger_time_empty")
    parsed: datetime | None = None
    for fmt in _TRIGGER_ACCEPT_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise TriggerTimeError(
            f"trigger_time_bad_format:{text}（必须为北京时间 YYYY-MM-DD HH:MM）"
        )
    ref = now if now is not None else datetime.now()
    if parsed < ref - _PAST_TOLERANCE:
        raise TriggerTimeError(
            f"trigger_time_in_past:{text}（已过去，请按当前时间 {ref:%Y-%m-%d %H:%M} 重新换算）"
        )
    return parsed.strftime(_TRIGGER_STORE_FORMAT)


class ReminderTool(BaseTool):
    name = "set_reminder"
    description = "设置提醒事项（到点会主动发消息提醒用户）"
    # 2026-09-21 生产修复：托付提醒是用户显式请求，不得绑亲密度门槛。
    # 旧 permission_level=friend（affinity>=2）导致新用户（level 0）调用被拒、
    # 提醒未落库，pending 却已被标 fulfilled → 「说了会叫却没叫」。
    permission_level = "public"
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
            normalized = normalize_trigger_time(trigger_time)
        except TriggerTimeError as e:
            # 失败信息回给终审模型：格式错→重试换算；过去时刻→重新换算或 ask_user
            logger.info("[reminder] trigger_time 校验失败: %s", e)
            return ToolResult(False, error=str(e))
        try:
            reminder_id = self._sm.add_reminder(
                content, normalized, session_key=session_key, user_id=user_id,
            )
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": normalized,
                "deliver_to": session_key or "(无投递目标)",
                "message": f"已设置提醒：{content}，时间 {normalized}",
            })
        except Exception:
            logger.exception("设置提醒失败")
            return ToolResult(False, error="reminder_set_failed")


class CalendarQueryTool(BaseTool):
    name = "query_reminders"
    description = "查询待触发的提醒事项"
    # 与 set_reminder 同理：查询自己的提醒不应受亲密度门槛限制
    permission_level = "public"
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
