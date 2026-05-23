from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Optional

from observability.logging_setup import new_trace_id
from observability.tracing import tracer
from safety.content_safety import ContentSafetyFilter
from safety.pii_anonymizer import PIIAnonymizer
from safety.prompt_injection import PromptInjectionDetector

logger = logging.getLogger("orchestrator")


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
        safety_filter: Optional[ContentSafetyFilter] = None,
        pii_anonymizer: Optional[PIIAnonymizer] = None,
        injection_detector: Optional[PromptInjectionDetector] = None,
        character_manager=None,
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
        self._state_lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._last_chat_time = datetime.now()

    async def process_message(self, user_msg: str, session_id: str = "",
                        message_type: str = "text") -> Dict[str, Any]:
        trace_id = new_trace_id()
        tracer.start_trace(trace_id)

        try:
            async with self._async_lock:
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
                    if self._emotion:
                        recent = []
                        if self._memory:
                            recent_msgs = self._memory.working.get_recent(3)
                            recent = [m.get("content", "") for m in recent_msgs]
                        emotion_state = self._emotion.analyze(user_msg, "\n".join(recent))

                with tracer.span("memory_retrieve"):
                    if self._memory and hasattr(self._memory, 'retrieve_context_async'):
                        ctx = await self._memory.retrieve_context_async(user_msg, session_id)
                    elif self._memory:
                        ctx = self._memory.retrieve_context(user_msg, session_id)
                    else:
                        ctx = {}

                with tracer.span("rag_retrieve"):
                    rag_context = ""
                    if self._rag:
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

                with tracer.span("llm_inference"):
                    tools_schema = None
                    if self._tools:
                        affinity = emotion_state.affinity if emotion_state else 0
                        tools_schema = self._tools.registry.get_tools_by_permission(affinity)

                    if tools_schema and self._llm and hasattr(self._llm, 'chat_with_tools'):
                        llm_result = self._llm.chat_with_tools(
                            query=user_msg,
                            system_prompt=system_prompt,
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
                                        reply = self._llm.chat(
                                            query=f"基于工具结果回复用户：{tool_msg}\n原始问题：{user_msg}",
                                            system_prompt=system_prompt,
                                            max_tokens=1024,
                                        )
                    elif self._llm:
                        reply = self._llm.chat(
                            query=user_msg,
                            system_prompt=system_prompt,
                            temperature=0.85,
                            max_tokens=2048,
                        )
                    else:
                        reply = "（系统暂不可用）"

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

                self._last_chat_time = datetime.now()

            trace_result = tracer.end_trace()
            return {
                "reply": reply,
                "trace_id": trace_id,
                "emotion": emotion_state.to_dict() if emotion_state else None,
                "trace": trace_result,
            }

        except Exception as e:
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
            async with self._async_lock:
                with self._state_lock:
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
                            emotion_state = self._emotion.analyze(user_msg, "\n".join(recent))

                    with tracer.span("memory_retrieve"):
                        ctx = self._memory.retrieve_context(user_msg, session_id) if self._memory else {}

                    with tracer.span("rag_retrieve"):
                        rag_context = ""
                        if self._rag:
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
                    with tracer.span("llm_inference"):
                        async for token in self._llm.chat_stream(query=user_msg, system_prompt=system_prompt):
                            collected_tokens.append(token)
                            yield token

                    with tracer.span("output_safety_check"):
                        full_output = "".join(collected_tokens)
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

                    self._last_chat_time = datetime.now()

        except Exception as e:
            logger.error("Stream orchestrator error: %s", e)
            yield json.dumps({"type": "stream_error", "error": str(e)[:200]})
        finally:
            if trace_id in getattr(tracer, '_active_traces', {}):
                tracer.end_trace()

    def check_proactive(self) -> Optional[Dict[str, Any]]:
        if self._ase:
            with self._state_lock:
                hours_since_last = (datetime.now() - self._last_chat_time).total_seconds() / 3600.0
            return self._ase.tick(hours_since_last_chat=hours_since_last)
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
                except Exception as e:
                    logger.warning("Failed to shutdown %s: %s", name, e)
            elif comp and hasattr(comp, 'shutdown'):
                try:
                    comp.shutdown()
                    logger.info("Shutdown component: %s", name)
                except Exception as e:
                    logger.warning("Failed to shutdown %s: %s", name, e)
