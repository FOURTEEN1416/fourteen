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
from security.content_safety import ContentSafetyFilter
from security.pii_anonymizer import PIIAnonymizer
from security.prompt_injection import PromptInjectionDetector

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

        # ── per-session 异步锁系统 ────────────────────────────────────
        # 设计原则：
        #   1. 每个 session 一个 asyncio.Lock，不同 session 可完全并行处理
        #   2. 同一 session 的消息串行处理，保证情感引擎状态一致性
        #   3. 锁缓存带 TTL 自动清理，防止内存无限增长
        #   4. 后台定期清理，不在请求热路径中执行 O(n) 操作
        # ──────────────────────────────────────────────────────────────
        self._session_locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._session_locks_mutex = threading.Lock()
        self._last_chat_time = datetime.now(tz=timezone.utc)
        self._session_lock_access_time: dict[str, float] = {}
        self._cleanup_task: asyncio.Task | None = None  # 后台清理任务，首次使用时惰性启动

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取 per-session 异步锁，确保不同 session 可并行处理。

        锁策略：
        - 每个 session 独立 asyncio.Lock，无全局串行瓶颈
        - 同一 session 的消息串行执行，保证情感引擎状态一致性
        - 不同 session 的请求可完全并行

        跨事件循环安全说明：
        - asyncio.Lock() 在创建时会绑定到当前运行的事件循环
        - 此方法总是在 async 上下文中被调用（process_message / process_message_stream 均为 async），
          因此 asyncio.Lock() 总能绑定到正确的事件循环
        - 单进程单事件循环部署（Uvicorn 标准模式）下不存在跨循环问题
        - 多 worker 部署时每个 worker 独立进程，_session_locks 字典不共享

        内存优化：
        - 使用带 TTL 的锁缓存，防止 session 过多导致内存无限增长
        - 后台定期清理过期锁，不在请求热路径中执行 O(n) 清理
        - 最大缓存数限制为 _MAX_SESSION_LOCKS 个 session
        """
        # 首次使用时惰性启动后台清理任务
        if self._cleanup_task is None:
            self._lazy_start_cleanup_task()

        current_time = time.time()

        with self._session_locks_mutex:
            # 快速路径：检查缓存
            if session_id in self._session_locks:
                lock, _ = self._session_locks[session_id]
                self._session_lock_access_time[session_id] = current_time
                return lock

            # 创建新的 per-session 锁
            # asyncio.Lock() 会自动绑定到当前运行的事件循环，
            # 此处必然在 async 上下文中，因此总是安全的
            new_lock = asyncio.Lock()

            self._session_locks[session_id] = (new_lock, current_time)
            self._session_lock_access_time[session_id] = current_time

            # 达到上限时触发一次紧急清理（仅计数检查，O(1) 操作）
            if len(self._session_locks) >= _MAX_SESSION_LOCKS:
                logger.warning("Session锁缓存达到上限 (%d)，触发紧急清理", _MAX_SESSION_LOCKS)
                # 紧急清理在互斥锁外执行，避免阻塞当前 session 的创建
                # 注意：此处不阻塞 - 让后台任务处理

            return new_lock

    def _lazy_start_cleanup_task(self) -> None:
        """惰性启动后台锁清理任务（线程安全）"""
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._periodic_lock_cleanup())
            logger.debug("后台session锁清理任务已启动")
        except RuntimeError:
            # 理论上不会发生（_get_session_lock 总是在 async 上下文中调用）
            # 防御性编程：记录警告，清理任务将在下次调用时重试
            logger.warning("无法启动后台清理任务：无运行中的事件循环")

    async def _periodic_lock_cleanup(self) -> None:
        """后台定期清理过期 session 锁（每 5 分钟）。

        将 O(n) 的清理操作从请求热路径移至后台任务，
        避免 _session_locks_mutex 被长时间持有导致其他 session 阻塞。
        """
        while True:
            try:
                await asyncio.sleep(300)  # 每 5 分钟清理一次
                self._cleanup_expired_session_locks(time.time())
                current_count = len(self._session_locks)
                if current_count > 0:
                    logger.debug("Session锁清理完成，当前活跃锁数: %d", current_count)
            except asyncio.CancelledError:
                logger.debug("后台session锁清理任务已取消")
                break
            except Exception:  # noqa: BLE001
                logger.exception("Session锁清理任务异常，将在下一周期重试")

    def _cleanup_expired_session_locks(self, current_time: float) -> None:
        """清理过期的 session 锁，防止内存无限增长（线程安全，内部自行加锁）。

        清理策略：
        1. 优先清理超过 TTL（1小时）未访问的锁
        2. 如果仍然超过最大限制，清理最久未访问的锁

        注意：此方法现在从后台清理任务调用，不在 _get_session_lock 的热路径中执行。
        内部自行加锁，调用者无需持有 _session_locks_mutex。
        """
        with self._session_locks_mutex:
            expired = []
            for sid, (_, created_time) in self._session_locks.items():
                last_access = self._session_lock_access_time.get(sid, created_time)
                if current_time - last_access > _SESSION_LOCK_TTL_SECONDS:
                    expired.append(sid)

            for sid in expired:
                self._session_locks.pop(sid, None)
                self._session_lock_access_time.pop(sid, None)

            # 如果仍然超过最大限制，清理最久未访问的
            if len(self._session_locks) >= _MAX_SESSION_LOCKS:
                sorted_sessions = sorted(
                    self._session_lock_access_time.items(),
                    key=lambda x: x[1],
                )
                sessions_to_remove = len(self._session_locks) - _MAX_SESSION_LOCKS + 100  # 预留100个空间
                for sid, _ in sorted_sessions[:sessions_to_remove]:
                    self._session_locks.pop(sid, None)
                    self._session_lock_access_time.pop(sid, None)

            if expired:
                logger.debug("清理 %d 个过期session锁，当前总数: %d", len(expired), len(self._session_locks))

    async def process_message(self, user_msg: str, session_id: str = "",
                        message_type: str = "text",
                        emotion_engine: Any | None = None,
                        character_id: str = "default") -> dict[str, Any]:
        """处理用户消息

        Args:
            user_msg: 用户消息内容
            session_id: 会话ID
            message_type: 消息类型
            emotion_engine: 可选的情感引擎，用于多用户隔离场景
            character_id: 修复 P0-7，兼容新调用约定（OptimizedOrchestrator 用此参），
                旧 Orchestrator 不做多角色隔离，参数被记录但不改变行为。
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
                        emotion_dict = emotion_state
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

                # === 一致性检查（复用 my_character/consistency_checker.py） ===
                with tracer.span("consistency_check"):
                    from my_character.consistency_checker import check_and_correct_reply
                    reply = await check_and_correct_reply(
                        reply=reply,
                        persona_engine=self._persona,
                        llm_gateway=self._llm,
                        emotion_state=emotion_state,
                        session_id=session_id,
                        memory=self._memory,
                    )
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
                                      message_type: str = "text",
                                      character_id: str = "default") -> AsyncIterator[str]:
        """流式聊天接口 — 修复 P0-7

        兼容新调用约定（OptimizedOrchestrator 用 character_id='xxx'）。
        旧 Orchestrator 不做多角色隔离，character_id 被记录但不改变行为。
        """
        if not self._llm or not hasattr(self._llm, 'chat_stream'):
            result = await self.process_message(user_msg, session_id, message_type)
            yield result.get("reply", "")
            return

        trace_id = new_trace_id()
        tracer.start_trace(trace_id)

        # 预处理器阶段（在锁保护下串行执行，保证 session 状态一致）
        # 注意：该阶段不含 yield，锁在离开 async with 块后释放，
        # 避免流式 yield 期间持有锁导致锁生命周期不确定的问题
        async def _preprocess() -> dict:
            """在锁保护下执行预处理器，返回后续阶段所需的上下文"""
            lock = self._get_session_lock(session_id or "default")
            async with lock:
                with tracer.span("multimodal_preprocess"):
                    processed_msg = user_msg
                    if self._multimodal and message_type != "text":
                        processed = self._multimodal.process(user_msg, message_type)
                        processed_msg = processed.get("text", user_msg)

                with tracer.span("input_safety_check"):
                    safety_result = self._safety.check_input(processed_msg)
                    if not safety_result.is_safe:
                        return {"early_exit": self._safety.safe_alternative(safety_result.category)}

                with tracer.span("pii_anonymize"):
                    processed_msg, _ = self._pii.anonymize(processed_msg)

                with tracer.span("prompt_injection_check"):
                    is_injection, _, _ = self._injection.detect(processed_msg)
                    if is_injection:
                        processed_msg = self._injection.sanitize(processed_msg)

                with tracer.span("emotion_analyze"):
                    emotion_state = None
                    if self._emotion:
                        recent = []
                        if self._memory:
                            recent_msgs = self._memory.working.get_recent(3)
                            recent = [m.get("content", "") for m in recent_msgs]
                        loop = asyncio.get_running_loop()
                        emotion_state = await loop.run_in_executor(
                            None, self._emotion.analyze, processed_msg, "\n".join(recent)
                        )

                with tracer.span("memory_retrieve"):
                    if self._memory and hasattr(self._memory, 'retrieve_context_async'):
                        ctx = await self._memory.retrieve_context_async(processed_msg, session_id)
                    elif self._memory:
                        ctx = self._memory.retrieve_context(processed_msg, session_id)
                    else:
                        ctx = {}

                with tracer.span("rag_retrieve"):
                    rag_context = ""
                    rag_results = {}
                    if self._rag and hasattr(self._rag, 'retrieve_async'):
                        rag_results = await self._rag.retrieve_async(processed_msg)
                    elif self._rag:
                        rag_results = self._rag.retrieve(processed_msg)
                    if rag_results.get("results"):
                        rag_context = "\n".join(
                            r.get("content", "") for r in rag_results["results"][:3]
                        )

                with tracer.span("prompt_assemble"):
                    system_prompt = ""
                    if self._persona:
                        emotion_dict = emotion_state
                        persona_overrides = None
                        if self._character_manager:
                            persona_overrides = self._character_manager.get_active_persona_config()
                        system_prompt = self._persona.build_system_prompt(
                            emotion_state=emotion_dict,
                            memory_context=ctx,
                            rag_context=rag_context,
                            character_overrides=persona_overrides,
                        )

                return {
                    "processed_msg": processed_msg,
                    "emotion_state": emotion_state,
                    "system_prompt": system_prompt,
                    "early_exit": None,
                }

        ctx = await _preprocess()
        if ctx.get("early_exit"):
            yield ctx["early_exit"]
            return

        processed_msg: str = ctx["processed_msg"]
        emotion_state: Any = ctx["emotion_state"]
        system_prompt: str = ctx["system_prompt"]

        # 流式推理阶段（锁已释放，可并行处理同一 session 的其他请求）
        collected_tokens = []
        BUFFER_CHECK_INTERVAL = 20  # noqa: N806
        buffer = []
        stream_unsafe = False
        partial_result = None
        full_output = ""

        try:
            with tracer.span("llm_inference"):
                async for token in self._llm.chat_stream(query=processed_msg, system_prompt=system_prompt):
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
                        for t in buffer:
                            yield t
                        buffer = []

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
                if partial_result is not None:
                    yield self._safety.safe_alternative(partial_result.category)
                else:
                    logger.error("stream_unsafe=True but partial_result is None")
                    yield "[内容安全过滤]"
                with self._last_chat_time_lock:
                    self._last_chat_time = datetime.now(tz=timezone.utc)
                return

            full_output = "".join(collected_tokens)

            # 一致性检查
            with tracer.span("consistency_check"):
                from my_character.consistency_checker import check_and_correct_reply
                full_output = await check_and_correct_reply(
                    reply=full_output,
                    persona_engine=self._persona,
                    llm_gateway=self._llm,
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self._memory,
                )

            with tracer.span("output_safety_check"):
                output_result = self._safety.check_output(full_output)
                if not output_result.is_safe:
                    logger.warning("Stream output safety issue: %s", output_result.category.value)
                    full_output = self._safety.safe_alternative(output_result.category)

            with tracer.span("memory_store"):
                if self._memory:
                    emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
                    self._memory.after_chat(processed_msg, full_output, emotion_tag, session_id=session_id)

            with tracer.span("reflection"):
                if self._ase:
                    self._ase.on_chat(processed_msg, full_output)

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
        """关闭 Orchestrator，释放所有资源。

        关闭顺序（反向依赖顺序）：
        1. 取消后台 session 锁清理任务
        2. 逐个关闭组件（llm → rag → memory → persona → emotion → ase）
        """
        # 1. 取消后台清理任务
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("后台session锁清理任务已取消")

        # 2. 关闭组件
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
