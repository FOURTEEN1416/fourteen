"""OptimizedOrchestrator 流式处理 — Mixin。

将原 ``process_message_stream`` 方法（209 行）从主文件迁出，便于独立阅读
真流式 / 伪流式两条路径。方法签名、事件协议、内部调用链均与原实现一致；
不引入新的抽象层或兼容垫片。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("orchestrator.optimized")


class _StreamPipelineMixin:
    """``OptimizedOrchestrator.process_message_stream`` 的隔离载体。

    依赖主类提供的 ``self._initialized`` / ``self.components`` /
    ``self._get_session_lock`` / ``self._prepare_context`` /
    ``self._after_process`` / ``self.process_message``。
    """

    # NOTE: 类型注解使用 Any 避免与主类循环依赖；运行期为 OptimizedOrchestrator 实例。
    _initialized: bool
    components: dict[str, Any]

    async def process_message_stream(
        self,
        user_msg: str,
        session_id: str = "",
        message_type: str = "text",
        character_id: str = "default",
        emotion_engine: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE 流式聊天接口 — 真流式接入

        实现策略：
        1. 如果 LLM 网关支持 chat_stream，使用真流式（边生成边 yield）
        2. 如果不支持，降级为伪流式（跑完 process_message 后按 8 字符切块 yield）
        3. 安全检查和 PII 脱敏在流式开始前完成
        4. 最后统一返回一条 done 事件，包含 reply / emotion / process_time 等字段

        Args:
            user_msg: 用户消息
            session_id: 会话 ID
            message_type: 消息类型（text/voice/image）
            character_id: 角色 ID（用于多角色隔离）

        Yields:
            事件字典：{"type": "token", "content": "..."} 或
            {"type": "done", "reply": "...", "emotion": ..., "process_time": ...}
        """
        stream_start = time.perf_counter()

        if not self._initialized:
            reply = "系统初始化中, 请稍候..."
            yield {"type": "token", "content": reply}
            yield {"type": "done", "reply": reply, "emotion": None, "process_time": 0.0}
            return

        # 检测 LLM 网关是否支持真流式
        llm = self.components.get("llm")
        use_true_stream = llm is not None and hasattr(llm, "chat_stream")

        if not use_true_stream:
            # ── 降级：伪流式（跑完整 process_message 后按块 yield） ──
            try:
                result = await self.process_message(
                    user_msg,
                    session_id,
                    message_type,
                    character_id,
                    emotion_engine=emotion_engine,
                )
                reply = result.get("reply", "")
                emotion = result.get("emotion")
                process_time = result.get(
                    "process_time", round(time.perf_counter() - stream_start, 3)
                )
            except Exception:
                logger.exception("流式处理异常")
                reply = "（处理消息时出现异常, 请稍后重试）"
                emotion = None
                process_time = round(time.perf_counter() - stream_start, 3)

            if reply:
                chunk_size = 8
                for i in range(0, len(reply), chunk_size):
                    yield {"type": "token", "content": reply[i:i + chunk_size]}
            yield {"type": "done", "reply": reply, "emotion": emotion, "process_time": process_time}
            return

        # ── 真流式路径 ──
        try:
            # 1. 输入安全检查（必须在流式开始前完成）
            safety_result = self.components["safety"].check_input(user_msg)
            if not safety_result.is_safe:
                reply = self.components["safety"].safe_alternative(safety_result.category)
                yield {"type": "token", "content": reply}
                yield {
                    "type": "done",
                    "reply": reply,
                    "emotion": None,
                    "process_time": round(time.perf_counter() - stream_start, 3),
                }
                return

            # 2. PII 脱敏（必须在流式开始前完成）
            user_msg_clean, _ = self.components["pii"].anonymize(user_msg)

            # 3. 注入检测 + 消毒
            is_injection, _, _ = self.components["injection"].detect(user_msg_clean)
            if is_injection:
                user_msg_clean = self.components["injection"].sanitize(user_msg_clean)

            # 4. 会话锁（防止同 session 并发处理）
            lock = self._get_session_lock(session_id)
            if lock.locked():
                reply = "处理中, 请稍候..."
                yield {"type": "token", "content": reply}
                yield {
                    "type": "done",
                    "reply": reply,
                    "emotion": None,
                    "process_time": round(time.perf_counter() - stream_start, 3),
                }
                return

            async with lock:
                # ── 共享预处理（并行任务、prompt 组装、工具调用） ──
                ctx = await self._prepare_context(
                    user_msg_clean,
                    session_id,
                    character_id,
                    emotion_engine=emotion_engine,
                )
                emotion_state = ctx["emotion_state"]
                system_prompt = ctx["system_prompt"]
                chat_history = ctx["chat_history"]

                # 9. 先完整缓冲 LLM 输出。未经一致性和输出安全校验的内容
                # 不能发送给客户端，否则后续事件也无法撤回泄露内容。
                full_reply = ""
                try:
                    async for token in llm.chat_stream(
                        query=user_msg_clean,
                        system_prompt=system_prompt,
                        history=chat_history,
                        temperature=0.85,
                        max_tokens=2048,
                    ):
                        if token:
                            full_reply += token
                except Exception as e:
                    logger.warning("真流式调用失败: %s", e)
                    # 流式中途失败：如果已有部分输出，补充提示后继续后处理
                    # 如果完全没有输出，降级为返回错误提示
                    if not full_reply:
                        reply = "（生成回复时出现异常, 请稍后重试）"
                        yield {"type": "token", "content": reply}
                        yield {
                            "type": "done",
                            "reply": reply,
                            "emotion": None,
                            "process_time": round(time.perf_counter() - stream_start, 3),
                        }
                        return

                if not full_reply:
                    yield {
                        "type": "done",
                        "reply": "",
                        "emotion": None,
                        "process_time": round(time.perf_counter() - stream_start, 3),
                    }
                    return

                # 10. 完整回复校验。
                reply = full_reply

                from my_character.consistency_checker import check_and_correct_reply

                character_card = None
                persona_service = self.components.get("persona")
                card_loader = getattr(persona_service, "_load_character_card", None)
                if character_id not in ("default", "demo") and callable(card_loader):
                    character_card = card_loader(character_id)
                reply = await check_and_correct_reply(
                    reply=reply,
                    persona_engine=getattr(persona_service, "engine", None),
                    llm_gateway=self.components.get("llm"),
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self.components.get("memory"),
                    character_card=character_card,
                )

                output_result = self.components["safety"].check_output(reply)
                if not output_result.is_safe:
                    reply = self.components["safety"].safe_alternative(output_result.category)

                # ── 共享后处理（after_chat → ASE → 好感度同步）──
                await asyncio.to_thread(
                    self._after_process,
                    user_msg_clean,
                    reply,
                    emotion_state,
                    session_id,
                    character_id,
                )

                # 校验通过后才对外发送最终文本。
                chunk_size = 8
                for i in range(0, len(reply), chunk_size):
                    yield {"type": "token", "content": reply[i:i + chunk_size]}

                process_time = round(time.perf_counter() - stream_start, 3)
                yield {
                    "type": "done",
                    "reply": reply,
                    "emotion": emotion_state.to_dict() if emotion_state else None,
                    "process_time": process_time,
                }

        except Exception:
            logger.exception("流式处理异常")
            reply = "（处理消息时出现异常, 请稍后重试）"
            yield {"type": "token", "content": reply}
            yield {
                "type": "done",
                "reply": reply,
                "emotion": None,
                "process_time": round(time.perf_counter() - stream_start, 3),
            }
