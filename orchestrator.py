from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from observability.logging_setup import new_trace_id
from observability.tracing import tracer
from safety.content_safety import ContentSafetyFilter
from safety.pii_anonymizer import PIIAnonymizer
from safety.prompt_injection import PromptInjectionDetector

logger = logging.getLogger("orchestrator")

# Session锁缓存配置：最大缓存数、锁过期时间（秒）
_MAX_SESSION_LOCKS = 1000
_SESSION_LOCK_TTL_SECONDS = 3600  # 1小时无使用后清理


class Orchestrator:
    def __init__(
        self,
        llm_gateway=None,
        emotion_engine=None,
        persona_engine=None,
        memory_pipeline=None,
        rag_engine=None,
        tool_dispatcher=None,
        multimodal_processor=None,
        ase_engine=None,
        safety_filter: ContentSafetyFilter | None = None,
        pii_anonymizer: PIIAnonymizer | None = None,
        injection_detector: PromptInjectionDetector | None = None,
        character_manager=None,
        character_service=None,
    ):
        self._llm = llm_gateway
        self._emotion = emotion_engine
        self._persona = persona_engine
        self._memory = memory_pipeline
        self._rag = rag_engine
        self._tools = tool_dispatcher
        self._multimodal = multimodal_processor
        self._ase = ase_engine
        self._safety = safety_filter or ContentSafetyFilter(enabled=False)
        self._pii = pii_anonymizer or PIIAnonymizer(enabled=False)
        self._injection = injection_detector or PromptInjectionDetector(enabled=False)
        self._character_manager = character_manager
        self._character_service = character_service
        self._last_chat_time_lock = threading.Lock()  # 轻量锁，仅保护 _last_chat_time 的读写
        # per-session 异步锁，避免全局锁导致所有用户串行处理
        # 使用带TTL的锁缓存，防止内存无限增长
        self._session_locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._session_locks_mutex = threading.Lock()
        self._last_chat_time = datetime.now(tz=timezone.utc)
        self._session_lock_access_time: dict[str, float] = {}

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取 per-session 异步锁，确保不同 session 可并行处理。

        注意: asyncio.Lock 必须在 async 上下文中创建以绑定正确的事件循环。
        采用延迟创建策略，首次在 async 上下文中调用时才实例化 Lock。

        内存优化：
        - 使用带TTL的锁缓存，防止session过多导致内存无限增长
        - 定期清理过期的session锁（超过1小时未访问）
        - 最大缓存数限制为1000个session
        """
        current_time = time.time()

        with self._session_locks_mutex:
            # 清理过期锁（每100次访问触发一次清理，避免频繁清理）
            if len(self._session_locks) >= _MAX_SESSION_LOCKS or \
               (len(self._session_locks) > 0 and hash(session_id) % 100 == 0):
                self._cleanup_expired_session_locks(current_time)

            # 检查是否已存在该session的锁
            if session_id in self._session_locks:
                lock, _ = self._session_locks[session_id]
                self._session_lock_access_time[session_id] = current_time
                return lock

            # 延迟创建：确保 Lock 绑定到当前运行的事件循环
            try:
                asyncio.get_running_loop()
                new_lock = asyncio.Lock()
            except RuntimeError:
                # 没有运行中的事件循环时，创建一个未绑定循环的 Lock
                # 后续在 async 上下文中使用时仍需注意事件循环一致性
                new_lock = asyncio.Lock()

            self._session_locks[session_id] = (new_lock, current_time)
            self._session_lock_access_time[session_id] = current_time
            return new_lock

    def _cleanup_expired_session_locks(self, current_time: float) -> None:
        """清理过期的session锁，防止内存无限增长。

        清理策略：
        1. 优先清理超过TTL（1小时）未访问的锁
        2. 如果仍然超过最大限制，清理最久未访问的锁
        """
        expired_sessions = []
        for sid, (_, created_time) in self._session_locks.items():
            last_access = self._session_lock_access_time.get(sid, created_time)
            if current_time - last_access > _SESSION_LOCK_TTL_SECONDS:
                expired_sessions.append(sid)

        for sid in expired_sessions:
            del self._session_locks[sid]
            if sid in self._session_lock_access_time:
                del self._session_lock_access_time[sid]

        # 如果仍然超过最大限制，清理最久未访问的
        if len(self._session_locks) >= _MAX_SESSION_LOCKS:
            sorted_sessions = sorted(
                self._session_lock_access_time.items(),
                key=lambda x: x[1]
            )
            sessions_to_remove = len(self._session_locks) - _MAX_SESSION_LOCKS + 100  # 预留100个空间
            for sid, _ in sorted_sessions[:sessions_to_remove]:
                if sid in self._session_locks:
                    del self._session_locks[sid]
                del self._session_lock_access_time[sid]

        if expired_sessions:
            logger.debug("清理 %d 个过期session锁，当前总数: %d", len(expired_sessions), len(self._session_locks))

    async def process_message(self, user_msg: str, session_id: str = "",
                        message_type: str = "text",
                        emotion_engine: Any | None = None) -> dict[str, Any]:
        """处理用户消息

        Args:
            user_msg: 用户消息内容
            session_id: 会话ID
            message_type: 消息类型
            emotion_engine: 可选的情感引擎，用于多用户隔离场景
        """
        trace_id = new_trace_id()
        tracer.start_trace(trace_id)

        try:
            lock = self._get_session_lock(session_id or "default")
            async with lock:
                with tracer.span("multimodal_preprocess"):
                    if self._multimodal and message_type != "text":
                        processed = self._multimodal.process(user_msg, message_type)
                        user_msg = processed.get("text", user_msg)

                with tracer.span("input_safety_check"):
                    safety_result = self._safety.check_input(user_msg)
                    if not safety_result.is_safe:
                        return {
                            "reply": self._safety.safe_alternative(safety_result.category),
                            "trace_id": trace_id,
                            "safety": safety_result.to_dict(),
                        }

                with tracer.span("pii_anonymize"):
                    user_msg, pii_detected = self._pii.anonymize(user_msg)

                with tracer.span("prompt_injection_check"):
                    is_injection, _, _ = self._injection.detect(user_msg)
                    if is_injection:
                        user_msg = self._injection.sanitize(user_msg)

                with tracer.span("emotion_analyze"):
                    emotion_state = None
                    # 优先使用传入的情感引擎（多用户隔离）
                    effective_emotion_engine = emotion_engine or self._emotion
                    if effective_emotion_engine:
                        recent = []
                        if self._memory:
                            recent_msgs = self._memory.working.get_recent(3)
                            recent = [m.get("content", "") for m in recent_msgs]
                        # 使用 run_in_executor 避免同步阻塞事件循环
                        loop = asyncio.get_running_loop()
                        emotion_state = await loop.run_in_executor(
                            None, effective_emotion_engine.analyze, user_msg, "\n".join(recent)
                        )

                with tracer.span("memory_retrieve"):
                    if self._memory and hasattr(self._memory, 'retrieve_context_async'):
                        ctx = await self._memory.retrieve_context_async(user_msg, session_id)
                    elif self._memory:
                        ctx = self._memory.retrieve_context(user_msg, session_id)
                    else:
                        ctx = {}

                    chat_history: list = []
                    chat_summary: str = ""
                    if self._memory and hasattr(self._memory, 'get_chat_context'):
                        chat_history, chat_summary = self._memory.get_chat_context(
                            session_id=session_id,
                        )

                with tracer.span("rag_retrieve"):
                    rag_context = ""
                    rag_results = {}
                    if self._rag and hasattr(self._rag, 'retrieve_async'):
                        rag_results = await self._rag.retrieve_async(user_msg)
                    elif self._rag:
                        rag_results = self._rag.retrieve(user_msg)
                    if rag_results.get("results"):
                        rag_context = "\n".join(
                            r.get("content", "") for r in rag_results["results"][:3]
                        )

                with tracer.span("prompt_assemble"):
                    system_prompt = ""
                    if self._persona:
                        emotion_dict = emotion_state.to_dict() if emotion_state else None
                        persona_overrides = None
                        if self._character_manager:
                            persona_overrides = self._character_manager.get_active_persona_config()
                        system_prompt = self._persona.build_system_prompt(
                            emotion_state=emotion_dict,
                            memory_context=ctx,
                            rag_context=rag_context,
                            chat_summary=chat_summary,
                            character_overrides=persona_overrides,
                        )

                with tracer.span("llm_inference"):
                    tools_schema = None
                    if self._tools:
                        affinity = emotion_state.affinity if emotion_state else 0
                        tools_schema = self._tools.registry.get_tools_by_permission(affinity)

                    if tools_schema and self._llm and hasattr(self._llm, 'chat_with_tools'):
                        llm_result = await self._llm.chat_with_tools(
                            query=user_msg,
                            system_prompt=system_prompt,
                            history=chat_history,
                            temperature=0.85,
                            max_tokens=2048,
                            tools=tools_schema,
                        )
                        reply = llm_result.get("content", "")
                        tool_calls = llm_result.get("tool_calls")

                        if tool_calls and self._tools:
                            with tracer.span("tool_call"):
                                for tc in tool_calls:
                                    fn = tc.get("function", {})
                                    tool_name = fn.get("name", "")
                                    try:
                                        arguments = json.loads(fn.get("arguments", "{}"))
                                    except json.JSONDecodeError:
                                        arguments = {}
                                    affinity = emotion_state.affinity if emotion_state else 0
                                    result = self._tools.dispatch(tool_name, arguments, affinity, trace_id)
                                    if result.success:
                                        tool_msg = f"[工具{tool_name}结果] {result.to_fc_result()}"
                                        reply = await self._llm.chat(
                                            query=f"基于工具结果回复用户：{tool_msg}\n原始问题：{user_msg}",
                                            system_prompt=system_prompt,
                                            history=chat_history,
                                            max_tokens=1024,
                                        )
                    elif self._llm:
                        reply = await self._llm.chat(
                            query=user_msg,
                            system_prompt=system_prompt,
                            history=chat_history,
                            temperature=0.85,
                            max_tokens=2048,
                        )
                    else:
                        reply = "（系统暂不可用）"

                # === 新增：一致性检查（复用 PersonaEngine.check_consistency） ===
                with tracer.span("consistency_check"):
                    try:
                        if self._persona and hasattr(self._persona, 'check_consistency'):
                            chat_round = 0
                            if self._memory and hasattr(self._memory, 'get_chat_context'):
                                history, _ = self._memory.get_chat_context(session_id=session_id)
                                chat_round = len(history) if history else 0
                            result = self._persona.check_consistency(
                                reply, emotion_state, chat_round
                            )
                            if not result.overall_passed:
                                if result.overall_score < 0.4 and result.correction_prompt:
                                    # 严重违规：用修正prompt重新生成
                                    corrected = await self._llm.chat_sync(
                                        query=f"{result.correction_prompt}\n\n"
                                              f"原始回复：{reply}\n\n"
                                              f"请根据以上修正建议重新生成一条符合角色设定的回复。"
                                              f"只输出修正后的回复。",
                                        max_tokens=512,
                                    )
                                    if corrected and len(corrected.strip()) > 0:
                                        reply = corrected.strip()
                                        logger.info("一致性严重违规已修正: score=%.2f", result.overall_score)
                                else:
                                    # 轻度违规：轻量修正
                                    logger.info("一致性轻度违规(score=%.2f)，放行原回复", result.overall_score)
                    except Exception as e:
                        logger.warning("一致性检查异常（已放行原回复）: %s", e)
                # === 检查结束 ===

                with tracer.span("output_safety_check"):
                    output_result = self._safety.check_output(reply)
                    if not output_result.is_safe:
                        reply = self._safety.safe_alternative(output_result.category)

                with tracer.span("memory_store"):
                    if self._memory:
                        emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
                        if hasattr(self._memory, 'after_chat_async'):
                            await self._memory.after_chat_async(user_msg, reply, emotion_tag, session_id=session_id)
                        else:
                            self._memory.after_chat(user_msg, reply, emotion_tag, session_id=session_id)

                with tracer.span("reflection"):
                    if self._ase:
                        self._ase.on_chat(user_msg, reply)

                with self._last_chat_time_lock:
                    self._last_chat_time = datetime.now(tz=timezone.utc)

            trace_result = tracer.end_trace()
            return {
                "reply": reply,
                "trace_id": trace_id,
                "emotion": emotion_state.to_dict() if emotion_state else None,
                "trace": trace_result,
            }

        except Exception as e:  # noqa: BLE001
            logger.error("Orchestrator error: %s", e)
            return {"reply": "（处理消息时出现异常，请稍后重试）", "trace_id": trace_id, "error": "internal_error"}
        finally:
            if trace_id in getattr(tracer, '_active_traces', {}):
                tracer.end_trace()

    async def process_message_stream(self, user_msg: str, session_id: str = "",
                                      message_type: str = "text") -> AsyncIterator[str]:
        if not self._llm or not hasattr(self._llm, 'chat_stream'):
            result = await self.process_message(user_msg, session_id, message_type)
            yield result.get("reply", "")
            return

        trace_id = new_trace_id()
        tracer.start_trace(trace_id)

        try:
            lock = self._get_session_lock(session_id or "default")
            async with lock:
                with tracer.span("multimodal_preprocess"):
                    if self._multimodal and message_type != "text":
                        processed = self._multimodal.process(user_msg, message_type)
                        user_msg = processed.get("text", user_msg)

                with tracer.span("input_safety_check"):
                    safety_result = self._safety.check_input(user_msg)
                    if not safety_result.is_safe:
                        yield self._safety.safe_alternative(safety_result.category)
                        return

                with tracer.span("pii_anonymize"):
                    user_msg, pii_detected = self._pii.anonymize(user_msg)

                with tracer.span("prompt_injection_check"):
                    is_injection, _, _ = self._injection.detect(user_msg)
                    if is_injection:
                        user_msg = self._injection.sanitize(user_msg)

                with tracer.span("emotion_analyze"):
                    emotion_state = None
                    if self._emotion:
                        recent = []
                        if self._memory:
                            recent_msgs = self._memory.working.get_recent(3)
                            recent = [m.get("content", "") for m in recent_msgs]
                        # 使用 run_in_executor 避免同步阻塞事件循环
                        loop = asyncio.get_running_loop()
                        emotion_state = await loop.run_in_executor(
                            None, self._emotion.analyze, user_msg, "\n".join(recent)
                        )

                with tracer.span("memory_retrieve"):
                    if self._memory and hasattr(self._memory, 'retrieve_context_async'):
                        ctx = await self._memory.retrieve_context_async(user_msg, session_id)
                    elif self._memory:
                        ctx = self._memory.retrieve_context(user_msg, session_id)
                    else:
                        ctx = {}

                with tracer.span("rag_retrieve"):
                    rag_context = ""
                    rag_results = {}
                    if self._rag and hasattr(self._rag, 'retrieve_async'):
                        rag_results = await self._rag.retrieve_async(user_msg)
                    elif self._rag:
                        rag_results = self._rag.retrieve(user_msg)
                    if rag_results.get("results"):
                        rag_context = "\n".join(
                            r.get("content", "") for r in rag_results["results"][:3]
                        )

                with tracer.span("prompt_assemble"):
                    system_prompt = ""
                    if self._persona:
                        emotion_dict = emotion_state.to_dict() if emotion_state else None
                        persona_overrides = None
                        if self._character_manager:
                            persona_overrides = self._character_manager.get_active_persona_config()
                        system_prompt = self._persona.build_system_prompt(
                            emotion_state=emotion_dict,
                            memory_context=ctx,
                            rag_context=rag_context,
                            character_overrides=persona_overrides,
                        )

                collected_tokens = []
                # 缓冲区安全检查：先攒够缓冲区再检查，安全后才 yield，杜绝不安全内容外泄
                BUFFER_CHECK_INTERVAL = 20  # noqa: N806
                buffer = []
                stream_unsafe = False
                partial_result = None  # 初始化 partial_result，避免后续引用时 NameError
                with tracer.span("llm_inference"):
                    async for token in self._llm.chat_stream(query=user_msg, system_prompt=system_prompt):
                        collected_tokens.append(token)
                        buffer.append(token)
                        if len(buffer) >= BUFFER_CHECK_INTERVAL:
                            partial_text = "".join(buffer)
                            partial_result = self._safety.check_output(partial_text)
                            if not partial_result.is_safe:
                                logger.warning(
                                    "Stream mid-buffer safety issue: %s",
                                    partial_result.category.value,
                                )
                                stream_unsafe = True
                                break
                            # 缓冲区安全，逐 token 输出
                            for t in buffer:
                                yield t
                            buffer = []

                    # 处理剩余的不足 BUFFER_CHECK_INTERVAL 的 token
                    if not stream_unsafe and buffer:
                        partial_text = "".join(buffer)
                        partial_result = self._safety.check_output(partial_text)
                        if not partial_result.is_safe:
                            logger.warning(
                                "Stream final buffer safety issue: %s",
                                partial_result.category.value,
                            )
                            stream_unsafe = True
                        else:
                            for t in buffer:
                                yield t
                            buffer = []

                if stream_unsafe:
                    # 检测到不安全内容，中断流并发送安全替代内容（此时未输出任何不安全token）
                    # partial_result 在 stream_unsafe=True 时必然已被赋值
                    if partial_result is not None:
                        yield self._safety.safe_alternative(partial_result.category)
                    else:
                        # 防御性编程：理论上不会到达这里
                        logger.error("stream_unsafe=True but partial_result is None")
                        yield "[内容安全过滤]"
                    # 不再执行后续的 memory_store 和 reflection
                    with self._last_chat_time_lock:
                        self._last_chat_time = datetime.now(tz=timezone.utc)
                    return

                # === 新增：一致性检查（复用 PersonaEngine.check_consistency） ===
                with tracer.span("consistency_check"):
                    try:
                        full_output = "".join(collected_tokens)
                        if self._persona and hasattr(self._persona, 'check_consistency'):
                            result = self._persona.check_consistency(
                                full_output, emotion_state, 0
                            )
                            if not result.overall_passed and result.overall_score < 0.4 and result.correction_prompt:
                                # 严重违规时尝试修正
                                corrected = await self._llm.chat_sync(
                                    query=f"{result.correction_prompt}\n\n"
                                          f"原始回复：{full_output}\n\n"
                                          f"请重新生成：",
                                    max_tokens=512,
                                )
                                if corrected and len(corrected.strip()) > 0:
                                    full_output = corrected.strip()
                    except Exception as e:
                        logger.warning("Stream一致性检查异常（已放行）: %s", e)
                # === 检查结束 ===

                with tracer.span("output_safety_check"):
                    output_result = self._safety.check_output(full_output)
                    if not output_result.is_safe:
                        logger.warning("Stream output safety issue: %s", output_result.category.value)
                        full_output = self._safety.safe_alternative(output_result.category)

                with tracer.span("memory_store"):
                    if self._memory:
                        emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
                        self._memory.after_chat(user_msg, full_output, emotion_tag, session_id=session_id)

                with tracer.span("reflection"):
                    if self._ase:
                        self._ase.on_chat(user_msg, full_output)

                with self._last_chat_time_lock:
                    self._last_chat_time = datetime.now(tz=timezone.utc)

        except Exception:
            logger.exception("Stream orchestrator error")
            yield json.dumps({"type": "stream_error", "error": "stream_error"})
        finally:
            if trace_id in getattr(tracer, '_active_traces', {}):
                tracer.end_trace()

    def check_proactive(self) -> dict[str, Any] | None:
        if self._ase:
            with self._last_chat_time_lock:
                hours_since_last = (datetime.now(tz=timezone.utc) - self._last_chat_time).total_seconds() / 3600.0
            return self._ase.tick(hours_since_last_chat=hours_since_last)  # type: ignore[no-any-return]
        return None

    def shutdown(self) -> None:
        components = [
            ("ase", self._ase),
            ("emotion", self._emotion),
            ("persona", self._persona),
            ("memory", self._memory),
            ("rag", self._rag),
            ("llm", self._llm),
        ]
        for name, comp in reversed(components):
            if comp and hasattr(comp, 'close'):
                try:
                    if asyncio.iscoroutinefunction(comp.close):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(comp.close())
                        except RuntimeError:
                            asyncio.run(comp.close())
                    else:
                        comp.close()
                    logger.info("Shutdown component: %s", name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to shutdown %s: %s", name, e)
            elif comp and hasattr(comp, 'shutdown'):
                try:
                    comp.shutdown()
                    logger.info("Shutdown component: %s", name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to shutdown %s: %s", name, e)
