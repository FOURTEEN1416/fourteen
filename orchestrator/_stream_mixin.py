"""OptimizedOrchestrator 分块响应：上游可流式，完整定稿后按传输确认记账。

SSE/WS 线协议保持不变；内部 token 事件的 ``_ack`` 回调只供适配器使用。
支持与不支持上游流式的网关共用上下文、发布、取消及后处理路径。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from utils.llm_bridge import current_llm, request_scoped_llm

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

    # 以下成员由宿主主类提供（mixin 协作契约）；显式声明消除 mypy attr-defined
    if TYPE_CHECKING:  # pragma: no cover
        def process_message(self, *args: Any, **kwargs: Any) -> Any: ...
        def _get_session_lock(self, session_id: str) -> Any: ...
        def _await_session_free(self, session_id: str, timeout: Any = ...) -> Any: ...
        def _prepare_context(self, *args: Any, **kwargs: Any) -> Any: ...
        def _after_process(self, *args: Any, **kwargs: Any) -> Any: ...
        def _finalize_reply(self, *args: Any, **kwargs: Any) -> str: ...
    # 后台 task 引用集合（避免被 GC 回收，asyncio.create_task 文档要求）。
    # 必须为实例变量，若为类变量会导致多实例共享同一集合引发 race condition。
    # 实际初始化在 OptimizedOrchestrator.__init__ 中完成。
    _background_tasks: set[Any]

    async def _async_consistency_check(
        self,
        reply: str,
        character_id: str,
        emotion_state: Any,
        session_id: str,
        chat_round: int = 0,
    ) -> None:
        """后台异步一致性检查（A3 统一策略）。

        - 不阻塞主回复流，避免触发第二次 LLM 调用导致响应时间翻倍
        - 生成后仅对**硬违规**（自称 AI 等）记日志；已推送文本不做静默改写
        - chat_round 由 _prepare_context 透传，禁止 stream 内二次查库
        """
        try:
            character_card = None
            persona_service = self.components.get("persona")
            card_loader = getattr(persona_service, "_load_character_card", None)
            from my_character.persona_engine import is_external_character_id
            if is_external_character_id(character_id) and callable(card_loader):
                character_card = card_loader(character_id)

            from my_character.consistency_checker import detect_hard_violation

            char_name = ""
            if isinstance(character_card, dict):
                char_name = str(character_card.get("name") or "")
            hard = detect_hard_violation(reply, char_name)
            if hard:
                # 已推送 → 不静默改写；下一轮 system 由 prompt 层约束
                logger.warning(
                    "硬违规(流式已推送不改写): type=%s session=%s char=%s reply=%r",
                    hard, session_id, character_id, (reply or "")[:80],
                )

            result = None
            if character_card:
                from my_character.consistency_checker import (
                    ConsistencyContext,
                    checker_for_card,
                    couple_style_for,
                )

                affinity = getattr(emotion_state, "affinity", 0) if emotion_state else 0
                result = checker_for_card(character_card).check(
                    reply,
                    ConsistencyContext(
                        emotion_state=emotion_state,
                        # 6b 项9②：风格维度接线（后台检测与主链同一 coupled_style 语义）
                        coupled_style=couple_style_for(
                            emotion_state,
                            getattr(persona_service, "style_coupler", None),
                        ),
                        chat_round=chat_round,
                        affinity=int(affinity or 0),
                    ),
                )
            elif persona_service and hasattr(persona_service, "check_consistency"):
                result = persona_service.check_consistency(reply, emotion_state, chat_round)

            if result is None:
                return

            if not result.overall_passed:
                # 6b 项9②：与修正触发同一旁路口径——persona 维度违规即硬违规，
                # 加权总分到不了 0.4 也应按严重记 WARNING（流式已推送不改写，仅观测）。
                persona_dim = result.dimensions.get("persona")
                hard_persona = persona_dim is not None and not persona_dim.passed
                if result.overall_score < 0.4 or hard_persona:
                    logger.warning(
                        "一致性严重违规(后台检测): score=%.2f session=%s char=%s",
                        result.overall_score, session_id, character_id,
                    )
                else:
                    logger.info(
                        "一致性轻度违规(后台检测): score=%.2f, 放行",
                        result.overall_score,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("后台一致性检查异常（不影响回复）: %s", exc)

    @request_scoped_llm
    async def process_message_stream(
        self,
        user_msg: str,
        session_id: str = "",
        message_type: str = "text",
        character_id: str = "default",
        emotion_engine: Any | None = None,
        user_llm_config: dict | None = None,
        user_id: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """SSE 聊天接口 — 上游流式生成，完整校验后分块发布。

        实现策略：
        1. 支持 chat_stream 的网关先生成完整草稿，主链定稿后按 8 字符切块。
        2. 不支持时调用 chat 生成草稿，共用同一发布与落库流程。
        3. 工具直复跳过模型；done 与历史只包含传输已确认的 token 前缀。
        4. 完整输出检查先于任何回复片段，因此首段显示晚于未经校验的真流式。
        5. 中断时只记用户输入与已确认前缀；服务端发送受理不等于终端已读。

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

        # 用户级 LLM gateway（API Key 隔离）：若提供 user_id + user_llm_config，
        # 本次流式请求使用用户专属 gateway，否则回退到全局共享 gateway。
        # 与 process_message 保持一致，避免流式响应绕过 API Key 隔离。
        request_llm = current_llm(self.components.get("llm"))

        # 检测 LLM 网关是否支持真流式
        use_true_stream = request_llm is not None and hasattr(request_llm, "chat_stream")

        # 两种上游共用发送、确认及落库流程；不得调用会提前记全文的普通入口。
        # _ack 只供服务端传输适配器调用，绝不序列化到 SSE/WS 协议中。
        accepted_end = 0
        reply = ""

        # ── 统一发布路径 ──
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
            # 2026-09-19：锁被占用时改为**有界排队**，不再直接吐「处理中, 请稍候...」——
            # 那是机器口吻的状态播报，且用户这一轮的输入会被整个丢弃。
            if not await self._await_session_free(session_id):
                reply = "等下，我还没回完上一条"
                yield {"type": "token", "content": reply}
                yield {
                    "type": "done",
                    "reply": reply,
                    "emotion": None,
                    "process_time": round(time.perf_counter() - stream_start, 3),
                }
                return

            lock = self._get_session_lock(session_id)
            async with lock:
                # ── 共享预处理（并行任务、prompt 组装、工具调用） ──
                ctx = await self._prepare_context(
                    user_msg_clean,
                    session_id,
                    character_id,
                    emotion_engine=emotion_engine,
                    user_id=user_id,  # P1-15：工具 _meta 调用归属（缺它 web 流式下 user_id=None）
                )
                emotion_state = ctx["emotion_state"]
                system_prompt = ctx["system_prompt"]
                chat_history = ctx["chat_history"]
                # A3：chat_round 由 prepare 透传，禁止 stream 内二次 get_chat_context
                chat_round = int(ctx.get("chat_round") or 0)

                # 上游仍可流式生成，但草稿先留在服务端；完整定稿后再下发。
                # 否则已发 token 无法撤回，清洗后只改 done/历史会制造三份事实。
                try:
                    full_reply = str(ctx.get("direct_reply") or "")
                    if not full_reply:
                        generation_args = dict(
                            query=user_msg_clean, system_prompt=system_prompt,
                            history=chat_history, temperature=0.85, max_tokens=2048,
                        )
                        upstream = None
                        try:
                            async with asyncio.timeout(30):
                                if use_true_stream:
                                    upstream = request_llm.chat_stream(**generation_args)
                                    async for token in upstream:
                                        if token:
                                            full_reply += token
                                else:
                                    full_reply = str(await request_llm.chat(**generation_args) or "")
                        except Exception as exc:
                            logger.warning("上游生成中断: %s", exc)
                            if not full_reply:
                                error_reply = "（生成回复时出现异常, 请稍后重试）"
                                yield {"type": "token", "content": error_reply}
                                yield {"type": "done", "reply": error_reply, "emotion": None,
                                       "process_time": round(time.perf_counter() - stream_start, 3)}
                                return
                        finally:
                            if upstream is not None:
                                await upstream.aclose()

                    reply = self._finalize_reply(full_reply, character_id)
                    for start in range(0, len(reply), 8):
                        end = min(start + 8, len(reply))

                        def acknowledge(end=end):
                            nonlocal accepted_end
                            accepted_end = max(accepted_end, end)

                        yield {"type": "token", "content": reply[start:end], "_ack": acknowledge}
                        # 普通生成器消费者继续迭代也表示前段已消费；中断时不走此行。
                        acknowledge()

                    bg_task = asyncio.create_task(
                        self._async_consistency_check(
                            reply=reply, character_id=character_id,
                            emotion_state=emotion_state, session_id=session_id,
                            chat_round=chat_round,
                        )
                    )
                    self._background_tasks.add(bg_task)
                    bg_task.add_done_callback(self._background_tasks.discard)
                finally:
                    # aclose/取消也保留用户行及已确认前缀；数据库轻写完成前不释放会话锁。
                    finish = asyncio.create_task(asyncio.to_thread(
                        self._after_process, user_msg_clean, reply[:accepted_end],
                        emotion_state, session_id, character_id,
                        turn_id=str(ctx.get("ax_turn_id") or ""),
                        reply_id=str(ctx.get("ax_reply_id") or ""),
                    ))
                    try:
                        await asyncio.shield(finish)
                    except asyncio.CancelledError:
                        await finish
                        raise

                process_time = round(time.perf_counter() - stream_start, 3)
                yield {
                    "type": "done",
                    "reply": reply[:accepted_end],
                    "emotion": emotion_state.to_dict() if emotion_state else None,
                    "process_time": process_time,
                }

        except Exception:
            logger.exception("流式处理异常")
            if accepted_end:
                # 已发内容不可撤回；后处理失败不能再追加一条错误台词并改写 done。
                yield {"type": "error", "error": "postprocess_failed", "reply": reply[:accepted_end]}
                return
            reply = "（处理消息时出现异常, 请稍后重试）"
            yield {"type": "token", "content": reply}
            yield {
                "type": "done",
                "reply": reply,
                "emotion": None,
                "process_time": round(time.perf_counter() - stream_start, 3),
            }
