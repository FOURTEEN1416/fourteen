"""提醒到期投递任务 — 确定性调度执行层（三级意图管线的 L2）。

编排器在 L1 终审里通过 set_reminder 工具落库的提醒（带 session_key 归属），
由本任务每分钟轮询到期并**定向投递**到发起会话：

- 投递是确定性代码，LLM 不参与 tick 级决策（LLM 判断，代码执行）；
- 投递文案在投递前一刻由 LLM 按角色口吻生成（scallopbot 实践），
  生成失败/超时兜底用户原话（用户原话提醒保持确定性）；
- **豁免静默时段**：叫醒类提醒（如 06:00）恰恰落在 23-7 静默窗内，
  若复用主动消息的静默闸门，用户明确要求的提醒会被丢弃（2026-09-20
  「六点叫起床」事故的构成缺陷之一）；
- 投递成功 mark delivered，失败累计 3 次判 failed（不再投递但可查）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any

from proactive.ase_engine import sanitize_message

logger = logging.getLogger("reminder_delivery")

# pending_intents 过期清理的节流间隔（秒）——随轮询每分钟触发但不必每分钟全表扫
_INTENT_GC_INTERVAL_SECONDS = 300.0


class ReminderDeliveryTask:
    """可调用对象：APScheduler 线程每分钟调用一次（同步入口）。"""

    def __init__(
        self,
        structured_memory: Any,
        llm: Any = None,
        wechat_sender: Callable[[int, str, str], bool] | None = None,
        ws_sender: Callable[[str], bool] | None = None,
        character_name: str = "",
    ):
        """Args:
        structured_memory: StructuredMemory 实例（get_due_reminders 等）。
        llm: LLM gateway（投递文案生成；None 时直接用原文）。
        wechat_sender: ``(owner_id, peer_wxid, text) -> bool`` 定向投递，
            由 api 装配层闭包持有 connector registry 注入。
        ws_sender: ``(text) -> bool`` websocket 广播（web 控制台会话）。
        character_name: 当前角色名（文案 prompt 用；可选）。
        """
        self._sm = structured_memory
        self._llm = llm
        self._wechat_sender = wechat_sender
        self._ws_sender = ws_sender
        self._character_name = character_name
        self._last_intent_gc = 0.0

    def __call__(self) -> None:
        asyncio.run(self._run_once())

    async def _run_once(self) -> None:
        try:
            due = await asyncio.to_thread(self._sm.get_due_reminders)
        except Exception:  # noqa: BLE001
            logger.exception("轮询到期提醒失败")
            return
        for reminder in due:
            try:
                await self._deliver(reminder)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "提醒投递异常 id=%s session=%s", reminder.get("id"),
                    reminder.get("session_key"),
                )
                await asyncio.to_thread(
                    self._sm.mark_reminder_result, reminder.get("id"), False,
                )
        await self._maybe_gc_intents()

    async def _deliver(self, reminder: dict[str, Any]) -> None:
        text = await self._compose_text(reminder)
        session_key = str(reminder.get("session_key") or "")
        delivered = await self._send_to_session(session_key, text)
        if delivered:
            logger.info(
                "[reminder] 已投递 id=%s session=%s content=%.40s",
                reminder.get("id"), session_key, reminder.get("content", ""),
            )
        else:
            logger.warning(
                "[reminder] 投递失败 id=%s session=%s（fail_count 累计，3 次判死）",
                reminder.get("id"), session_key,
            )
        await asyncio.to_thread(self._sm.mark_reminder_result, reminder.get("id"), delivered)

    async def _send_to_session(self, session_key: str, text: str) -> bool:
        """按会话键定向投递：``owner:peer@im.wechat`` → 微信；其余 → websocket。"""
        if not session_key:
            return False
        if "@im.wechat" in session_key and ":" in session_key:
            owner_raw, peer = session_key.split(":", 1)
            if not self._wechat_sender:
                logger.warning("[reminder] 微信投递通道未注入，session=%s", session_key)
                return False
            try:
                owner_id = int(owner_raw)
            except ValueError:
                logger.warning("[reminder] session_key owner 非法: %s", session_key)
                return False
            try:
                return bool(await asyncio.to_thread(
                    self._wechat_sender, owner_id, peer, text,
                ))
            except Exception as e:  # noqa: BLE001
                logger.warning("[reminder] 微信投递异常 session=%s: %s", session_key, e)
                return False
        if self._ws_sender:
            try:
                result = self._ws_sender(text)
                if inspect.isawaitable(result):
                    result = await result
                return bool(result)
            except Exception as e:  # noqa: BLE001
                logger.warning("[reminder] websocket 投递异常: %s", e)
                return False
        return False

    async def _compose_text(self, reminder: dict[str, Any]) -> str:
        """投递文案：LLM 按口吻生成一句（投递前一刻生成）；失败兜底用户原话。"""
        content = str(reminder.get("content") or "").strip() or "提醒时间到了"
        if self._llm is None:
            return content
        who = f"（你是{self._character_name}）" if self._character_name else ""
        try:
            reply = await asyncio.wait_for(
                self._llm.chat(
                    query=(
                        f"设定的提醒到时间了。请用一句符合你口吻的中文提醒用户：{content}。"
                        "只输出这一句话本身，不要解释、不要动作神态、不要换行。"
                    ),
                    system_prompt=(
                        f"你是用户的亲密AI伴侣角色{who}，正在微信上主动发消息提醒用户。"
                        "输出口语化的一句话（不超过40字）。"
                    ),
                    temperature=0.7,
                    max_tokens=120,
                ),
                timeout=8.0,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[reminder] 文案生成失败，用原文兜底: %s", e)
            return content
        cleaned = sanitize_message(str(reply or "").strip())
        return cleaned or content

    async def _maybe_gc_intents(self) -> None:
        """节流清理过期澄清任务（失败不影响投递主流程）。

        ⚠️ 2026-09-20 修复：旧实现是**同步**函数并调用
        ``asyncio.run(self._sm.expire_stale_intents())``，有两处同时成立的错误：
        ① ``expire_stale_intents`` 是**同步**方法 —— ``asyncio.run`` 只接受协程对象，
           传入同步调用的返回值必抛 ``ValueError: a coroutine was expected``；
        ② 它由 ``_run_once()``（本身已在 ``asyncio.run`` 的事件循环里）**同步调用**，
           ``asyncio.run`` 在运行中的循环内必抛
           ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
        异常被 ``except Exception`` 吞掉（只在 debug 级留痕），因此
        **批量过期清理在生产中从未执行**：``pending_intents`` 的 active 行只有在
        「该会话被再次读取」时才由 ``get_active_pending_intent`` 惰性置为 expired，
        长期不活跃会话的行永久残留（表无界增长）。现改为 async + ``asyncio.to_thread``。
        """
        now = time.monotonic()
        if now - self._last_intent_gc < _INTENT_GC_INTERVAL_SECONDS:
            return
        self._last_intent_gc = now
        try:
            expired = await asyncio.to_thread(self._sm.expire_stale_intents)
            if expired:
                logger.info("[reminder] 过期澄清任务清理: %s 条", expired)
        except Exception:  # noqa: BLE001
            logger.debug("澄清任务过期清理失败", exc_info=True)
