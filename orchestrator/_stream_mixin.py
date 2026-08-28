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
    ) -> None:
        """后台异步一致性检查（B2 优化）。

        - 不阻塞主回复流，避免触发第二次 LLM 调用导致响应时间翻倍
        - 严重违规只记录日志，不影响已推送的回复
        - 可在此触发下一轮的修正提示（当前仅日志，避免过度复杂）
        """
        try:

            character_card = None
            persona_service = self.components.get("persona")
            card_loader = getattr(persona_service, "_load_character_card", None)
            if character_id not in ("default", "demo") and callable(card_loader):
                character_card = card_loader(character_id)

            result = None
            if character_card:
                from my_character.consistency_checker import (
                    ConsistencyContext,
                    PersonaConsistencyChecker,
                )
                from my_character.dynamic_anchor import DynamicAnchorSystem

                anchors = character_card.get("core_anchors") or []
                checker = PersonaConsistencyChecker(
                    dynamic_anchors=DynamicAnchorSystem(
                        base_anchors=[str(a) for a in anchors],
                        dynamic_anchors=[],
                    ),
                )
                affinity = getattr(emotion_state, "affinity", 0) if emotion_state else 0
                chat_round = 0
                mem = self.components.get("memory")
                if mem and hasattr(mem, "get_chat_context"):
                    try:
                        history, _ = mem.get_chat_context(session_id=session_id)
                        chat_round = len(history) if history else 0
                    except Exception:  # noqa: BLE001
                        pass
                result = checker.check(
                    reply,
                    ConsistencyContext(
                        emotion_state=emotion_state,
                        chat_round=chat_round,
                        affinity=int(affinity or 0),
                    ),
                )
            elif persona_service and hasattr(persona_service, "check_consistency"):
                chat_round = 0
                mem = self.components.get("memory")
                if mem and hasattr(mem, "get_chat_context"):
                    try:
                        history, _ = mem.get_chat_context(session_id=session_id)
                        chat_round = len(history) if history else 0
                    except Exception:  # noqa: BLE001
                        pass
                result = persona_service.check_consistency(reply, emotion_state, chat_round)

            if result is None:
                return

            if not result.overall_passed:
                if result.overall_score < 0.4:
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

        # 用户级 LLM gateway（API Key 隔离）：若提供 user_id + user_llm_config，
        # 本次流式请求使用用户专属 gateway，否则回退到全局共享 gateway。
        # 与 process_message 保持一致，避免流式响应绕过 API Key 隔离。
        request_llm = self.components.get("llm")
        if user_llm_config and user_id is not None:
            try:
                from llm_provider import get_user_llm
                request_llm = get_user_llm(user_id, user_llm_config)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to get user-level LLM gateway for stream, falling back to global: %s", e)

        # 检测 LLM 网关是否支持真流式
        use_true_stream = request_llm is not None and hasattr(request_llm, "chat_stream")

        if not use_true_stream:
            # ── 降级：伪流式（跑完整 process_message 后按块 yield） ──
            try:
                result = await self.process_message(
                    user_msg,
                    session_id,
                    message_type,
                    character_id,
                    emotion_engine=emotion_engine,
                    user_llm_config=user_llm_config,
                    user_id=user_id,
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

                # 9. 真流式：LLM token 实时推送，输出安全检查改为流式抽检
                # 输入安全检查已在前面完成；输出安全检查用"流式窗口抽检 +
                # 最终全量校验"双层保护，不再阻塞 token 推送。
                full_reply = ""
                safety = self.components["safety"]
                unsafe_detected = False
                # 捕获流式抽检发现的不安全类别，供 safe_alternative 使用
                unsafe_category: Any = None

                try:
                    async for token in request_llm.chat_stream(
                        query=user_msg_clean,
                        system_prompt=system_prompt,
                        history=chat_history,
                        temperature=0.85,
                        max_tokens=2048,
                    ):
                        if not token:
                            continue
                        full_reply += token

                        # 流式抽检：每 40 字符做一次快速输出安全检查
                        # 发现不安全内容立即停止推送，避免泄露后续 token
                        if len(full_reply) % 40 < len(token):
                            quick_check = safety.check_output(full_reply[-60:])
                            if not quick_check.is_safe:
                                unsafe_detected = True
                                unsafe_category = quick_check.category
                                logger.warning(
                                    "流式抽检发现不安全内容，停止推送: category=%s",
                                    quick_check.category,
                                )
                                break

                        yield {"type": "token", "content": token}
                except Exception as e:
                    logger.warning("真流式调用失败: %s", e)
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

                # 流式抽检发现不安全内容 → 用安全替代语替换
                if unsafe_detected:
                    # safe_alternative 需要 SafetyCategory 枚举，不能用字符串
                    # 之前 bug: safe_alternative("unsafe_output") 永远走 default 分支
                    reply = safety.safe_alternative(unsafe_category)
                    yield {"type": "token", "content": reply}
                    yield {
                        "type": "done",
                        "reply": reply,
                        "emotion": emotion_state.to_dict() if emotion_state else None,
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

                # 10. 最终全量输出安全校验（兜底）
                reply = full_reply
                output_result = safety.check_output(reply)
                if not output_result.is_safe:
                    # 极端情况：流式抽检漏过，最终校验拦截
                    reply = safety.safe_alternative(output_result.category)
                    yield {"type": "token", "content": "\n[内容已过滤]"}

                # 11. 一致性检查异步化（B2 优化）
                # 不阻塞当前回复流；严重违规记录到后台，下一轮自动修正
                # 避免触发第二次 LLM 调用导致响应时间翻倍
                # 保留 task 引用避免被 GC 回收（Python 官方文档要求）
                bg_task = asyncio.create_task(
                    self._async_consistency_check(
                        reply=reply,
                        character_id=character_id,
                        emotion_state=emotion_state,
                        session_id=session_id,
                    )
                )
                self._background_tasks.add(bg_task)
                bg_task.add_done_callback(self._background_tasks.discard)

                # ── 共享后处理（after_chat → ASE → 好感度同步）──
                await asyncio.to_thread(
                    self._after_process,
                    user_msg_clean,
                    reply,
                    emotion_state,
                    session_id,
                    character_id,
                )

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
