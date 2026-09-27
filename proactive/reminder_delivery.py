"""提醒到期投递任务 — 确定性调度执行层（三级意图管线的 L2）。

编排器在 L1 终审里通过 set_reminder 工具落库的提醒（带 session_key 归属），
由本任务每分钟轮询到期并**定向投递**到发起会话：

- 投递是确定性代码，LLM 不参与 tick 级决策（LLM 判断，代码执行）；
- 投递文案在投递前一刻由 LLM 按角色口吻生成（scallopbot 实践），
  生成失败/超时明确引用用户设定的任务，不把用户原话冒充角色自述；
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
from utils import character_resolver
from utils import session_key as session_key_mod

logger = logging.getLogger("reminder_delivery")

# pending_intents 过期清理的节流间隔（秒）——随轮询每分钟触发但不必每分钟全表扫
_INTENT_GC_INTERVAL_SECONDS = 300.0

# 控制面受理回执等待窗（秒）：连接器 outbox 消费者挂在 5 秒 tick 上，
# 正常受理 <10s；超时**按未送达处理**（fail_count 继续累计，不假 delivered）。
PLANE_RECEIPT_TIMEOUT = 30.0


class ChannelNotReadyError(Exception):
    """通道实例缺席 / 账号未登录 —— 「现在没法发」不等于「发送失败」。

    计入 fail_count 会让提醒在三次轮询（约 3 分钟）后被判死：用户只是重启后
    慢了几分钟扫码、或跑 `--no-api` 没有 websocket 服务，之后就永远收不到
    叫醒（v1.30「说了会叫却没叫」同族）。此类轮次**跳过记账**、下分钟再试；
    真实发送失败（连接器返回 False）仍然照计——3 次判死的裁决不变。

    ⚠️ 口径只覆盖**本机直发**（`plane=None`，main 单进程入口）。控制面路径的
    「30s 内无宿主受理」仍按未送达计一次失败（`test_reminder_unconfirmed_receipt
    _is_not_success` 钉住）：那是 DECISION_LEDGER:115 的判死语义，且未受理的行仍
    留在 outbox 可被后到的宿主投递，改成跳过会让提醒永活并每分钟堆一行——
    两条拓扑对「通道一直缺席」的容忍度不一致，属未裁决项，不自决。
    """


class ReminderDeliveryTask:
    """可调用对象：APScheduler 线程每分钟调用一次（同步入口）。"""

    # 类级默认：未注入控制面时走进程内直发。__init__ 里同名覆盖。
    _plane: Any | None = None

    def __init__(
        self,
        structured_memory: Any,
        llm: Any = None,
        wechat_sender: Callable[[int, str, str], bool] | None = None,
        ws_sender: Callable[[str, str], bool] | None = None,
        memory: Any | None = None,
        character_id_resolver: Callable[[str], str] | None = None,
        llm_resolver: Callable | None = None,
        plane: Any | None = None,
    ):
        """Args:
        structured_memory: StructuredMemory 实例（get_due_reminders 等）。
        llm: LLM gateway（投递文案生成；None 时引用用户任务）。
        wechat_sender: ``(owner_id, peer_wxid, text) -> bool`` 定向投递，
            由 api 装配层闭包持有 connector registry 注入。
        ws_sender: ``(session_key, text) -> bool`` websocket **定向**投递
            （2026-09-22 起按会话键定向，旧 ``(text)`` 广播签名废弃——
            广播会把 A 的提醒推给所有打开控制台的人）。
        memory: MemoryService（API 受理后回写历史；缺省则不回写）。
        character_id_resolver: ``(session_key) -> 角色 id``，生成前只解析一次；
            展示名从该 id 取得，不再独立解析第二份身份。
        plane: ``proactive.runtime_plane`` 模块（或同接口对象）。注入后投递走
            **控制面 outbox**：入队保留 owner/peer/character 快照，由真正持有
            通道资源的宿主（连接器线程 / WS 持有者）CAS 认领并回写受理回执。
            旧实现假定"投递与通道宿主同进程"——scheduler 在 worker A、微信
            连接器在 worker B 时 A 的本地 registry 为空，提醒被误判失败并
            3 次判死（缺陷 A/C/E）。None 时保留原直发注入（开发/测试）。
        """
        self._sm = structured_memory
        self._llm = llm
        self._llm_resolver = llm_resolver
        self._wechat_sender = wechat_sender
        self._ws_sender = ws_sender
        self._memory = memory
        self._character_id_resolver = character_id_resolver
        self._plane = plane
        # 节流基准锚在「已过一整个间隔」而非 0：Linux 上 time.monotonic() 以**开机**为起点，
        # 新启动的宿主（如 CI runner，开机 <300s）会让首 tick 误判为「刚清理过」而跳过批量 GC。
        self._last_intent_gc = time.monotonic() - _INTENT_GC_INTERVAL_SECONDS

    def __call__(self) -> None:
        from utils.async_utils import run_on_shared_loop

        run_on_shared_loop(self._run_once())

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
        session_key = str(reminder.get("session_key") or "").strip()
        # D13 同意门禁（四通道同源）：账号未同意/已撤回时提醒不再外发。
        if session_key:
            from api.consent import outbound_allowed_for_session

            if not await outbound_allowed_for_session(session_key):
                logger.info(
                    "[reminder] 账号未同意协议，停发提醒 id=%s session=%s",
                    reminder.get("id"), session_key,
                )
                return
        character_id = self._resolve_character_id(session_key)
        text = await self._compose_text(reminder, character_id=character_id)
        try:
            if self._plane is not None:
                # 块3：投递经控制面 outbox —— 通道宿主可能在别的 worker（scheduler
                # 与连接器/WS 各自选主），本地直发在跨 worker 拓扑下必然误判失败。
                delivered = await self._send_via_plane(session_key, text, character_id)
            else:
                delivered = await self._send_to_session(session_key, text)
        except ChannelNotReadyError as e:
            # 「通道没就绪」跳过本轮：不记 delivered、也不吃失败配额。
            logger.warning(
                "[reminder] 通道未就绪，本轮不投递也不计失败 id=%s session=%s: %s",
                reminder.get("id"), session_key, e,
            )
            return
        if delivered:
            # 自问自答根治：她主动说的话必须进历史，否则下一轮她自己不记得提醒过
            # 🔴 2026-09-22：必须带 character_id（出站行此前无归属 → 切角色继承台词）
            recorder = getattr(self._memory, "record_outbound_message", None)
            if recorder is not None and session_key:
                try:
                    await asyncio.to_thread(
                        recorder,
                        message=text,
                        session_id=session_key,
                        character_id=character_id,
                        channel="reminder",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("[reminder] 回写历史失败 session=%s: %s", session_key, e)
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
        """按会话键定向投递：微信会话 → 微信通道；其余 → websocket。

        ⚠️ 2026-09-21 重扫修复（微信提醒恒不送达的机制级根因）：
        旧判据是 ``"@im.wechat" in session_key``，而**生产微信键由
        `wechat_connector._session_key` 构造为 ``N:wxid``（无后缀）** ——
        条件永假 → 微信分支**不可达**，到期提醒恒走 websocket 广播：
        微信用户收不到（未开 web 控制台），开着控制台时还可能因 ws 广播
        返回 True 而把提醒记成「已投递」。判据现由唯一真源
        `utils.session_key.is_wechat_key` 提供（三种方言口径见其 docstring）。
        """
        sk = str(session_key or "").strip()
        if not sk:
            return False
        if session_key_mod.is_wechat_key(sk):
            parsed = session_key_mod.parse(sk)
            if parsed.owner is None or not parsed.peer:
                logger.warning("[reminder] 微信会话键无法解析为 owner:peer: %s", sk)
                return False
            if not self._wechat_sender:
                logger.warning("[reminder] 微信投递通道未注入，session=%s", sk)
                return False
            try:
                return bool(await asyncio.to_thread(
                    self._wechat_sender, parsed.owner, parsed.peer, text,
                ))
            except ChannelNotReadyError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("[reminder] 微信投递异常 session=%s: %s", sk, e)
                return False
        if self._ws_sender:
            try:
                result = self._ws_sender(session_key, text)
                if inspect.isawaitable(result):
                    result = await result
                return bool(result)
            except ChannelNotReadyError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("websocket 定向投递异常 session=%s: %s", session_key, e)
                return False
        return False

    async def _send_via_plane(
        self, sk: str, text: str, character_id: str = "",
        receipt_timeout: float = PLANE_RECEIPT_TIMEOUT,
    ) -> bool:
        """经控制面 outbox 投递：入队保留归属快照，等资源宿主的受理回执。

        微信键 → ``channel=wechat`` 且**不点名 slot**：该 owner 任一在线连接器
        宿主 CAS 认领并真实发送，天然杜绝"遍历本地 registry 逐 slot 直发"；
        其余键 → ``channel=websocket``，由持有该连接的 WS worker 消费。
        旧实现只在**同进程** registry 里找通道，scheduler 与连接器分属不同
        worker 时本地必空 → 提醒被误判失败并 3 次判死（缺陷 A/C/E）。
        """
        rp = self._plane
        parsed = session_key_mod.parse(sk)
        is_wechat = session_key_mod.is_wechat_key(sk)
        if is_wechat and (parsed.owner is None or not parsed.peer):
            logger.warning("[reminder] 微信会话键无法解析为 owner:peer: %s", sk)
            return False
        channel = "wechat" if is_wechat else "websocket"
        try:
            cid = await asyncio.to_thread(
                rp.enqueue_send,
                kind="reminder", channel=channel, session_key=sk,
                owner_id=parsed.owner, peer=parsed.peer if is_wechat else "",
                character_id=character_id, message=text,
            )
            row = await asyncio.to_thread(rp.wait_for_receipt, cid, receipt_timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning("[reminder] 控制面投递异常 session=%s: %s", sk, e)
            return False
        status = str((row or {}).get("status") or "")
        reason = str((row or {}).get("fail_reason") or "")
        if status == "accepted":
            return True
        if status == "failed":
            logger.warning(
                "[reminder] 宿主回执投递失败 session=%s reason=%s", sk, reason,
            )
            return False
        logger.warning(
            "[reminder] %ss 内未获受理回执，按**未送达**处理 session=%s",
            receipt_timeout, sk,
        )
        return False

    def _resolve_character_id(self, session_key: str) -> str:
        """生成与落库共享同一角色快照；注入解析失败不冒认其他角色。"""
        if self._character_id_resolver is not None:
            return str(self._character_id_resolver(session_key) or "").strip()
        try:
            from api.deps import deps

            gf = getattr(deps, "gf", None)
        except Exception:  # noqa: BLE001
            gf = None
        return character_resolver.resolve_character_id(session_key, gf)

    async def _compose_text(self, reminder: dict[str, Any], *, character_id: str) -> str:
        """按固定身份生成；降级时明确引用用户设定的任务，不冒充角色自述。"""
        content = str(reminder.get("content") or "").strip()
        fallback = f"你设定的提醒时间到了：「{content}」。" if content else "你设定的提醒时间到了。"
        llm = self._llm
        if self._llm_resolver is not None:
            try:
                llm = await self._llm_resolver(str(reminder.get("session_key") or ""))
            except Exception:
                logger.warning("提醒模型凭证不可用，使用无模型引用文案")
                return fallback
        if llm is None:
            return fallback
        resolved_name = character_resolver.display_name(character_id) if character_id else ""
        who = f"（你是{resolved_name}）" if resolved_name else ""
        try:
            reply = await asyncio.wait_for(
                llm.chat(
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
            logger.warning("[reminder] 文案生成失败，引用用户任务兜底: %s", e)
            return fallback
        cleaned = sanitize_message(str(reply or "").strip())
        return cleaned or fallback

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
