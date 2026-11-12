"""Speaker 统一出口核心（第 1 刀，2026-11-12）。

职责边界（本刀只做核心件，不接管主链/主动/手动投递）：

- ``build_system_prompt``：人设 → 能力 → 话题 → 调用方 base，段间双换行；
- ``express``：唯一拆条入口（复用 ``wechat_connector.split_reply_for_wechat``）；
- ``deliver``：调用方注入 sender，逐段发送并汇总回执，异常不抛；
- ``record``：chat_history + agent_core 影子账本双写，失败只 warning。

**禁止**：在 Speaker 内建 LLM 客户端、读全局凭证、开模块级缓存、复制
``utils.topic_state`` 的 SQL。所有依赖由 ``components`` 注入或调用方显式传入。
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from shisi.application import persona_context
from speaker.capabilities import CapabilityModel
from speaker.thread_state import ThreadState
from speaker.types import DeliveryResult, UtteranceIntent

logger = logging.getLogger("speaker")


class Speaker:
    """五条出站线共用的发声核心。

    Args:
        components: 可选依赖字典。当前识别两个键：
            ``memory``：具备 ``record_outbound_message(...)`` 的记忆服务
            （缺省 = 不写 chat_history，只写影子账本）；
            ``voice_available``：bool 或零参可调用对象，供能力探测。
    """

    def __init__(self, components: dict[str, Any] | None = None):
        self._components: dict[str, Any] = dict(components or {})

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------

    def _voice_available(self) -> bool:
        """读注入的语音可用性；异常/缺省按 False（宁可少声明能力）。"""
        value = self._components.get("voice_available", False)
        try:
            return bool(value() if callable(value) else value)
        except Exception:  # noqa: BLE001
            return False

    def build_system_prompt(
        self, intent: UtteranceIntent, *, base: str = ""
    ) -> str:
        """按固定顺序拼系统提示词：人设 → 能力 → 话题 → base。

        任一段取不到/抛错都跳过（fail-open），绝不让提示词拼装失败阻断投递；
        空段不产生多余空行。
        """
        segments: list[str] = []

        character_id = str(intent.character_id or "").strip()
        if character_id:
            try:
                persona = persona_context.render_persona_hint(
                    character_id, user_key=str(intent.session_key or "")
                ) or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("[speaker][step=persona] 加载失败 cid=%s: %s", character_id, e)
                persona = ""
            if persona.strip():
                segments.append(persona)

        try:
            capability = CapabilityModel.detect(
                voice_available=self._voice_available()
            ).prompt_block()
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][step=capability] 渲染失败: %s", e)
            capability = ""
        if capability.strip():
            segments.append(capability)

        try:
            topic = ThreadState(
                str(intent.session_key or ""), character_id
            ).text_block()
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][step=topic] 渲染失败: %s", e)
            topic = ""
        if topic.strip():
            segments.append(topic)

        if str(base or "").strip():
            segments.append(base)

        return "\n\n".join(segments)

    # ------------------------------------------------------------------
    # 拆条
    # ------------------------------------------------------------------

    def express(
        self, intent: UtteranceIntent, text: str, *, max_segments: int | None = None
    ) -> list[str]:
        """把正文拆成可发送段；``max_segments`` 缺省 1 表示不拆条。

        拆条实现唯一 owner 是 ``wechat_connector.split_reply_for_wechat``；
        ``hard_limit`` 跟随 ``SPEAKER_REPLY``（缺省开）。拆条异常时整段直发，
        不阻断投递。
        """
        raw = str(text or "")
        if not raw.strip():
            return []
        limit = 1 if max_segments is None else max(1, int(max_segments))
        try:
            from utils.speaker_switches import speaker_reply_enabled
            from wechat_direct.wechat_connector import split_reply_for_wechat

            segments = split_reply_for_wechat(
                raw, max_segments=limit, hard_limit=speaker_reply_enabled()
            )
            cleaned = [str(seg) for seg in segments if str(seg).strip()]
            return cleaned or [raw]
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][step=express] 拆条失败，整段直发: %s", e)
            return [raw]

    # ------------------------------------------------------------------
    # 投递
    # ------------------------------------------------------------------

    def deliver(
        self,
        intent: UtteranceIntent,
        text: str,
        *,
        sender: Callable[[str], str | bool],
        context_token: str = "",
        max_segments: int | None = None,
    ) -> DeliveryResult:
        """逐段调用 ``sender`` 并汇总回执；任何异常都转成 ``accepted=False``。

        ``max_segments`` 缺省 1（不拆条）；需要逐段投递的调用方显式传该会话
        拆条上限，``express`` 会按 ``SPEAKER_REPLY`` 的 hard_limit 语义拆分。
        逐段回执与已受理前缀因此是生产可走通的路径，不是测试专用分支。

        ``sender`` 契约：接收一段正文，返回**通道受理的正文**（str）或
        **是否受理**（bool，兼容既有微信/WS sender）；返回空串/False 视为
        该段未被受理。部分段已受理后失败时，返回值保留已受理前缀（与主链
        “已发前缀永不回滚”同语义）。

        异常语义：任何异常转成 ``accepted=False`` 返回，不向上抛；其中
        ``ChannelNotReadyError`` 的 reason 前缀固定为 ``channel_not_ready:``，
        需要「通道未就绪不计失败」的调用方按该前缀识别并跳过本轮。

        ``context_token`` 预留给后续通道适配器（本刀注入的 sender 是一参函数，
        不消费该字段）。
        """
        channel = str(intent.source or "")
        del context_token  # 预留位：当前 sender 契约无次参数，显式声明不消费
        try:
            segments = self.express(intent, text, max_segments=max_segments)
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][step=express] 异常: %s", e)
            segments = [str(text or "")] if str(text or "").strip() else []
        if not segments:
            return DeliveryResult(
                accepted=False, channel=channel, reason="empty_text"
            )

        sent: list[str] = []
        accepted_parts: list[str] = []
        for segment in segments:
            try:
                accepted = sender(segment)
            except Exception as e:  # noqa: BLE001
                reason = (
                    f"channel_not_ready:{e}"
                    if type(e).__name__ == "ChannelNotReadyError"
                    else f"send_failed:{e}"
                )
                logger.warning("[speaker][step=send] 发送异常: %s", e)
                return DeliveryResult(
                    accepted=False,
                    accepted_text="\n".join(accepted_parts),
                    segments=tuple(sent),
                    channel=channel,
                    reason=reason,
                )
            # sender 双契约（2026-11-12 复核修复）：既有发送器返回 bool
            # （True=通道受理，False=拒收），新发送器可返回「通道受理的正文」。
            # bool 必须按「本段原文」记账，不能 str(True) 把 "True" 写进历史。
            # 2026-11-12 复核再修：async sender 不得假成功（本方法同步），
            # 非空字符串只有等于整段才算完整受理（否则是部分前缀）。
            if inspect.isawaitable(accepted):
                if inspect.iscoroutine(accepted):
                    accepted.close()
                return DeliveryResult(
                    accepted=False,
                    accepted_text="\n".join(accepted_parts),
                    segments=tuple(sent),
                    channel=channel,
                    reason="async_sender_unsupported",
                )
            accepted_text = (
                (segment if accepted else "")
                if isinstance(accepted, bool)
                else str(accepted or "").strip()
            )
            if not accepted_text:
                return DeliveryResult(
                    accepted=False,
                    accepted_text="\n".join(accepted_parts),
                    segments=tuple(sent),
                    channel=channel,
                    reason="sender_returned_empty",
                )
            if not isinstance(accepted, bool) and accepted_text != segment:
                accepted_parts.append(accepted_text)
                return DeliveryResult(
                    accepted=False,
                    accepted_text="\n".join(accepted_parts),
                    segments=tuple(sent),
                    channel=channel,
                    reason="partial_segment",
                )
            sent.append(segment)
            accepted_parts.append(accepted_text)

        return DeliveryResult(
            accepted=True,
            accepted_text="\n".join(accepted_parts),
            segments=tuple(sent),
            channel=channel,
        )

    # ------------------------------------------------------------------
    # 记账
    # ------------------------------------------------------------------

    def record(
        self, intent: UtteranceIntent, text: str, result: DeliveryResult
    ) -> None:
        """投递成功后双写 chat_history 与影子账本；任一失败只 warning。

        正文优先取 ``result.accepted_text``（只记真正受理的内容），否则回落
        ``text``。``components["memory"]`` 缺省时跳过 chat_history——提醒线
        已由既有 ``record_outbound_message`` 写过，这里只补影子账本，避免双写。
        """
        if not getattr(result, "accepted", False) and not str(
            getattr(result, "accepted_text", "") or ""
        ).strip():
            return
        body = str(getattr(result, "accepted_text", "") or "").strip()
        if not body and getattr(result, "accepted", False):
            body = str(text or "").strip()
        if not body:
            return
        # 2026-11-12 复核修复：空 session_key 一律不写 chat_history——
        # record_outbound_message 在空 session_id 时会回落自身 session_id，
        # 属于跨会话串写风险；此时只保留 warning，不做任何归属写入。
        session_key = str(intent.session_key or "").strip()
        if not session_key:
            logger.warning("[speaker][step=history] 缺 session_key，跳过记账（防串会话）")
            return
        # 2026-11-12 复核修复：空 character_id 也不写——无角色归属的 assistant
        # 行会被按角色读取过滤，或在切角色后被新角色继承台词。
        character_id = str(intent.character_id or "").strip()
        if not character_id:
            logger.warning("[speaker][step=history] 缺 character_id，跳过记账（防无角色历史）")
            return

        memory = self._components.get("memory")
        recorder = getattr(memory, "record_outbound_message", None)
        if callable(recorder):
            try:
                wrote = recorder(
                    message=body,
                    session_id=session_key,
                    character_id=character_id,
                    channel=str(intent.source or ""),
                )
                if wrote is False:
                    logger.warning("[speaker][step=history] record_outbound_message 返回 False")
            except Exception as e:  # noqa: BLE001
                logger.warning("[speaker][step=history] 回写失败: %s", e)

        try:
            from agent_core import events

            events.record_event(
                "agent_msg",
                session_key=session_key,
                character_id=character_id,
                source=f"speaker/{intent.source}",
                payload={
                    # 2026-11-12 复核修复：部分受理（accepted=False 但有已受理
                    # 前缀）必须与完整送达可区分，否则账本按 phase 统计/承诺
                    # 回放会把「说了一半」误判成「已说完」。
                    "phase": "speaker_delivered" if getattr(result, "accepted", False) else "speaker_partial",
                    "accepted": bool(getattr(result, "accepted", False)),
                    "text_len": len(body),
                    "character_id": character_id,
                    "channel": str(intent.source or ""),
                    "turn_id": "",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[speaker][step=ledger] 事件写入失败: %s", e)
