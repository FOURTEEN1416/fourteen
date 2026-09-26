from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

from tools.base_tool import BaseTool, ToolResult
from utils.local_time import now_local

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
    # 🔴 2026-09-22 二次根治：参照时刻必须与写入格式同一时区口径。
    # 落库串是**北京时间**（LLM 按 `utils.local_time.now_local()` 注入的当前
    # 时间换算），而旧实现用裸 `datetime.now()` 取宿主墙钟 —— UTC 容器下比
    # 北京慢 8 小时，「现在/几分钟内提醒我」会被判成「8 小时前」，
    # `trigger_time_in_past` 直接拒收：最常见的诉求在最需要它的部署形态下失效。
    ref = now if now is not None else now_local()
    if ref.tzinfo is not None:
        # 归一为 naive 本地墙钟，与 parsed（naive 北京时间）可直接比较
        ref = ref.replace(tzinfo=None)
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
        # 入站消息 id 随归属由编排器注入（_meta），LLM 参数不可决定
        message_id = str((meta or {}).get("message_id") or "")
        try:
            normalized = normalize_trigger_time(trigger_time)
        except TriggerTimeError as e:
            # 失败信息回给终审模型：格式错→重试换算；过去时刻→重新换算或 ask_user
            logger.info("[reminder] trigger_time 校验失败: %s", e)
            return ToolResult(False, error=str(e))
        from proactive.runtime_plane import effect_once

        # 幂等键含参数指纹：同一条消息托付两件事（六点和七点）各建一条，
        # 只有"同一条消息 + 同一件事"的重放才被吞掉。无 message_id（web/控制台）
        # 时键为空 → 不去重（宁可不 dedup，也不拿别的消息的键误吞本轮提醒）。
        args_hash = hashlib.sha1(
            f"{content}\n{normalized}".encode()
        ).hexdigest()[:16]
        dedup_key = (
            f"set_reminder:{session_key}:{message_id}:{args_hash}" if message_id else ""
        )

        def _create() -> int:
            return int(self._sm.add_reminder(
                content, normalized, session_key=session_key, user_id=user_id,
            ))

        try:
            first_executed, reminder_id = effect_once(dedup_key, _create)
            if not first_executed:
                logger.info(
                    "[reminder] 入站消息 %s 重放，复用已建提醒 %s", message_id, reminder_id,
                )
                if isinstance(reminder_id, str) and reminder_id.isdigit():
                    reminder_id = int(reminder_id)
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": normalized,
                "deliver_to": session_key or "(无投递目标)",
                "message": f"已设置提醒：{content}，时间 {normalized}",
                **({"deduplicated": True} if not first_executed else {}),
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
        "properties": {
            "include_finished": {
                "type": "boolean",
                "description": (
                    "是否一并返回已发/已取消/投递失败的提醒"
                    "（用户问「我让你提醒我的事都有哪些」以外的追问，如"
                    "「那条怎么没响」时才置 true）"
                ),
            },
        },
        "required": [],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def execute(self, include_finished: bool = False, **kwargs) -> ToolResult:
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        # 2026-09-22 防御收口：无 _meta（调用归属缺失）不得返回**全表**——
        # 旧实现会跨用户泄露全部提醒。生产路径恒注入 _meta，此为纵深防线。
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        try:
            if include_finished:
                # 待投影视图（active=1）看不见判死/取消件，「说了会叫却没叫」
                # 在用户眼里就是"提醒凭空消失"。全生命周期只在显式追问时开放。
                reminders = self._sm.list_reminders(session_key=session_key)
            else:
                reminders = self._sm.get_pending_reminders(session_key=session_key)
            return ToolResult(True, data=reminders)
        except Exception:
            logger.exception("查询提醒失败")
            return ToolResult(False, error="reminder_query_failed")


class ReminderManageTool(BaseTool):
    """提醒生命周期：本人取消 / 改期 / 恢复。

    补齐前只有"设"和"查待发"两条路 —— 用户说「那个提醒取消吧」无工具可调，
    只能等它到点发一条废话；说错了时间只能再设一条，旧的还在（同一天被叫两次）；
    3 次投递失败判死后既查不到也不能恢复（「还是叫我一声」无路可走）。
    """

    name = "manage_reminder"
    description = (
        "管理已设置的提醒：取消（cancel）、改期（reschedule）、"
        "恢复一条失败或已取消的提醒（restore）"
    )
    # 与 set_reminder 同纪律：用户对自己托付的提醒的处置权不绑亲密度。
    permission_level = "public"
    wants_call_context = True
    _ACTIONS = ("cancel", "reschedule", "restore")
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["cancel", "reschedule", "restore"],
                "description": "cancel=取消 / reschedule=改期 / restore=恢复",
            },
            "reminder_id": {
                "type": "integer",
                "description": "提醒编号（来自 query_reminders 返回的 id）",
            },
            "trigger_time": {
                "type": "string",
                "description": (
                    "新的触发时间，北京时间，格式 YYYY-MM-DD HH:MM（24 小时制）。"
                    "仅 action=reschedule 必填，相对表达须先按当前时间换算"
                ),
            },
        },
        "required": ["action", "reminder_id"],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def execute(self, action: str = "", reminder_id: int | None = None,
                trigger_time: str | None = None, **kwargs) -> ToolResult:
        if action not in self._ACTIONS:
            return ToolResult(
                False, error=f"unknown_action:{action}（可选 {'/'.join(self._ACTIONS)}）"
            )
        if reminder_id is None:
            return ToolResult(False, error="reminder_id is required")
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        # 与 set_reminder/query_reminders 同纪律：归属只能由服务端注入，
        # 缺失即拒——否则等于允许跨会话按 id 处置他人提醒。
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        try:
            rid = int(reminder_id)
        except (TypeError, ValueError):
            return ToolResult(False, error=f"reminder_id_invalid:{reminder_id}")

        try:
            if action == "cancel":
                if not self._sm.cancel_reminder(rid, session_key=session_key):
                    return self._not_owned(rid)
                row = self._sm.get_reminder(rid)
                return ToolResult(True, data={
                    "reminder_id": rid,
                    "status": (row or {}).get("status", "cancelled"),
                    "message": f"已取消提醒 {rid}",
                })

            if action == "reschedule":
                if not trigger_time:
                    return ToolResult(False, error="trigger_time_required")
                try:
                    normalized = normalize_trigger_time(trigger_time)
                except TriggerTimeError as e:
                    logger.info("[reminder] 改期 trigger_time 校验失败: %s", e)
                    return ToolResult(False, error=str(e))
                row = self._sm.reschedule_reminder(
                    rid, session_key=session_key, trigger_time=normalized
                )
                if row is None:
                    return self._not_owned(rid)
                return ToolResult(True, data={
                    "reminder_id": rid,
                    "status": row.get("status"),
                    "trigger_time": row.get("trigger_time"),
                    "message": f"提醒 {rid} 已改到 {normalized}",
                })

            # restore
            if not self._sm.restore_reminder(rid, session_key=session_key):
                row = self._sm.get_reminder(rid)
                if row and row.get("status") == "delivered":
                    return ToolResult(
                        False, error=f"reminder_already_delivered:{rid}"
                    )
                return self._not_owned(rid)
            return ToolResult(True, data={
                "reminder_id": rid,
                "status": "pending",
                "message": f"提醒 {rid} 已重新排队",
            })
        except Exception:
            logger.exception("提醒生命周期操作失败 action=%s id=%s", action, rid)
            return ToolResult(False, error="reminder_manage_failed")

    @staticmethod
    def _not_owned(reminder_id: int) -> ToolResult:
        return ToolResult(
            False,
            error=(
                f"reminder_not_found_or_not_yours:{reminder_id}"
                "（先用 query_reminders 的 include_finished 确认编号）"
            ),
        )
