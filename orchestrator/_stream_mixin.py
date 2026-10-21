"""OptimizedOrchestrator 分块响应：真流式按句边下发，未发余量交给唯一终裁。

SSE/WS 线协议保持不变；内部 token 事件的 ``_ack`` 回调只供适配器使用。
支持与不支持上游流式的网关共用上下文、发布、取消及后处理路径。

**2026-10-21 R4 真流式**（用户裁决）：旧的「上游攒稿 → 完整定稿 → 8 字符切块」
把首字节延迟固定在整轮生成耗时上。现在（``chat.stream_mode: true``，默认）：

1. ``_prepare_context`` 返回前不下发任何 token（工具闸/RAG/记忆严格先行）；
2. 逐 token 喂 ``utils.stream_guard.IncrementalReplyGuard``，**完整句**过增量
   守卫后立即作为单个 token 事件下发（适配器仍按 8 字符切传输块，``_ack`` 记账
   不变）；不完整尾巴永不下发；
3. 上游结束（或中断）后，未发余量送 ``self._finalize_reply`` 做**唯一全文终裁**
   （错误哨兵/仅剩角色名/发言标签/时间前缀/安全闸等只能全文判定的规则）；
   终裁为空走 ``get_fallback_line`` 兜底。

守卫取舍（设计复核 2026-10-21，逐条登记）：增量守卫只判定未发余量；需要全文
位置的规则延迟到终裁（已发前缀不再复核=降级项）；「命中即截断其后」的无由头
反问实现为**终止上游 + 丢弃全部未发**；退化形态（仅剩角色名）与错误哨兵整串
在流首扣住，流末由终裁判死。已发内容永不回滚（``accepted_end`` 前缀语义不变）。

``chat.stream_mode: buffered`` 时为旧路径（攒稿 → 定稿 → 8 字符切块），回滚只需
改配置。

**背压（设计复核 2026-10-21 条目「新增风险未列：背压延长持锁」）**：``yield`` 在
``async with lock`` 内（``_stream_mixin.py`` 会话锁块），慢客户端会让生成与传输
交织在同一临界区内。这与旧实现**同量级**（旧实现也在锁内逐块 yield，只是把等待
搬到末尾），但真流式下等待被摊到整轮：锁持有 ≈ 生成 + 传输。若慢客户端把持锁
拖过 ``_await_session_free`` 的排队窗口，下一轮会拿到「等下，我还没回完上一条」。
不做额外改动（加队列=引入兜底层，违反 §1.2）；故障形态由排队超时兜底且可观测。
为不把客户端慢误判成模型慢，真流式的超时预算**只给「等下一个上游 token」计时**
（``llm_budget_left`` 递降——只在 ``__anext__`` 前后取时刻差扣减），传输/消费耗时
不计入 LLM 预算。

**供应商静默截断（设计复核条目「降级语义·不闭合」）**：``openai_compatible_provider``
在已下发内容后遇超时/异常会 ``return``（不抛），网关因此看到「正常结束的流」。
编排层无法据此判定截断，故只记 ``stream_tail_without_terminator``（尾段无句末标点）
作为旁证日志；不重试、不改写已发内容。

**§1.2 豁免声明（设计复核条目「与项目铁律冲突」）**：``buffered`` 旧路径与
``true`` 新路径在 ``process_message_stream`` 内共存，按 AGENTS §1.2
「不引入兜底层、兼容 shim、重复 owner」原属违规形态。此处按用户裁决的**回滚
要求**显式豁免：两条路径共享同一个 ``_finalize_reply`` 终裁真源与同一组正则
（``utils.prompt_sanitize``），不存在第二个守卫 owner；``buffered`` 只保留旧的
**发布时机**，不复制规则。回滚=改配置，无需回滚代码。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from utils.llm_bridge import current_llm, request_scoped_llm

logger = logging.getLogger("orchestrator.optimized")

#: 传输分块上限（HTML/HTTP 缓冲友好；``accepted_end`` 算术按此粒度记账）。
_TRANSPORT_CHUNK_CHARS = 8


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

    def _build_guard_context(
        self,
        session_id: str,
        character_id: str,
        user_msg: str = "",
        reply_mode: str | None = None,
    ) -> dict[str, Any]:
        """首 token 前一次性提升增量守卫所需的全部上下文（含唯一一次称呼 I/O）。

        ``resolve_address`` 内部读画像投影与否认账本（一次 I/O）；逐 token 解析既
        浪费又会让各 token 的判定面不一致。失败一律 fail-open（缺上下文=不剥，
        与 ``terminal_reply_guard`` 的缺省语义一致）。
        """
        from utils.address_terms import LEGAL_FALLBACK, resolve_address
        from utils.reply_mode import read_reply_mode
        from utils.stream_guard import build_safety_check

        character_name = ""
        try:
            from utils.character_resolver import display_name

            character_name = str(display_name(str(character_id or "")) or "")
        except Exception as exc:  # noqa: BLE001
            logger.debug("流式守卫角色名解析失败（忽略）: %s", exc)

        legal = LEGAL_FALLBACK
        forbidden: frozenset[str] = frozenset()
        try:
            legal, forbidden = resolve_address(str(session_id or ""), str(character_id or ""))
        except Exception as exc:  # noqa: BLE001
            logger.debug("流式守卫称呼解析失败（忽略）: %s", exc)

        return {
            "character_name": character_name,
            # 2026-10-21 R7：优先用本轮已解析的模式（`ctx` 透传），不再二次热读
            "reply_mode": (
                reply_mode if reply_mode in ("immersive", "novel") else read_reply_mode(session_id or None)
            ),
            "legal": legal,
            "forbidden": forbidden,
            "safety_check": build_safety_check((self.components or {}).get("safety")),
            "user_msg": user_msg,
        }

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
        """SSE 聊天接口 — 按句边真流式下发，全文终裁只处理未发余量。

        实现策略：
        1. ``chat.stream_mode: true``（默认）：上游 token 喂增量守卫，完整句
           立即下发；未发余量流末交 ``_finalize_reply`` 终裁。
        2. ``buffered``：旧行为（完整定稿后按 8 字符切块），用于一键回滚。
        3. 支持与不支持上游流式的网关共用上下文、发布、取消及后处理路径。
        4. 工具直复（``direct_reply``）跳过模型，文本已定稿 → 整段走同一下发管线。
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
                # W2（2026-09-27）：锁后即建立 turn 上下文——准备阶段取消/异常的
                # 统一收尾也要有稳定 turn id，不得依赖 prepare 的产物。
                pre_turn_id = uuid.uuid4().hex[:12]
                # 轮次 LLM 预算总账（2026-10-21 FIX-3）：与非流式主链同一真源。
                # 流式这跳旧实现写死 30s，与主链 60s 各自为政 —— 同一句 prompt
                # 走 SSE 还是走普通 HTTP 拿到的是两套预算。
                from orchestrator.optimized_orchestrator import (
                    MAIN_CHAIN_LLM_TIMEOUT_SECONDS,
                    TOTAL_TURN_LLM_BUDGET,
                    remaining_turn_llm_budget,
                )

                llm_deadline = time.monotonic() + TOTAL_TURN_LLM_BUDGET
                ctx: dict[str, Any] = {}
                emotion_state = None

                async def _emit(text: str) -> AsyncIterator[dict[str, Any]]:
                    """把一段**已过硬守卫**的正文按传输粒度下发并记账。

                    ``reply`` 随下发增长（断开时 ``finally`` 的 ``reply[:accepted_end]``
                    必须与已发字符对齐）；``accepted_end`` 是**累计字符位置**，因此
                    ``_ack`` 的回调值要带上前面已发长度——逐句下发时光按片段内偏移
                    记账会让前缀被后一句覆盖。
                    """
                    nonlocal reply, accepted_end, emitted_chars
                    if not text:
                        return
                    reply += text
                    for start in range(0, len(text), _TRANSPORT_CHUNK_CHARS):
                        piece = text[start:start + _TRANSPORT_CHUNK_CHARS]
                        end = emitted_chars + start + len(piece)

                        def acknowledge(end=end):
                            nonlocal accepted_end
                            accepted_end = max(accepted_end, end)

                        yield {"type": "token", "content": piece, "_ack": acknowledge}
                        # 普通生成器消费者继续迭代也表示前段已消费；中断时不走此行。
                        acknowledge()
                    emitted_chars += len(text)

                emitted_chars = 0
                try:
                    # ── 共享预处理（并行任务、prompt 组装、工具调用） ──
                    ctx = await self._prepare_context(
                        user_msg_clean,
                        session_id,
                        character_id,
                        emotion_engine=emotion_engine,
                        user_id=user_id,  # P1-15：工具 _meta 调用归属（缺它 web 流式下 user_id=None）
                        turn_id=pre_turn_id,
                        llm_deadline=llm_deadline,
                    )
                    emotion_state = ctx["emotion_state"]
                    system_prompt = ctx["system_prompt"]
                    chat_history = ctx["chat_history"]
                    # A3：chat_round 由 prepare 透传，禁止 stream 内二次 get_chat_context
                    chat_round = int(ctx.get("chat_round") or 0)

                    # 2026-10-21 R4：守卫所需上下文在**首 token 前**一次性提升
                    # （称呼解析有一次 I/O：画像投影 + 否认账本；逐 token 解析会
                    # 把 I/O 放进流式热路径且各 token 判定面不一致）。
                    from utils.stream_guard import IncrementalReplyGuard, read_stream_mode

                    config_manager = (self.components or {}).get("config")
                    stream_mode = read_stream_mode(config_manager)
                    true_stream = use_true_stream and stream_mode != "buffered"
                    # 2026-10-21 R7：本轮模式由 `_prepare_context` 单次解析，透传给
                    # 守卫与终裁（旧实现守卫/终裁各自热读，流式下可能拿到两个模式）。
                    turn_reply_mode = ctx.get("reply_mode")
                    guard_ctx = self._build_guard_context(
                        session_id, character_id, user_msg_clean, reply_mode=turn_reply_mode,
                    )

                    # 上游仍可流式生成。真流式下按句边下发；buffered 下攒稿后下发。
                    # 已发 token 无法撤回 —— 因此 buffered 模式必须完整定稿后再发。
                    direct_reply = str(ctx.get("direct_reply") or "")
                    full_reply = direct_reply
                    guard: Any = None
                    truncated = False
                    generation_interrupted = False
                    if not full_reply:
                        generation_args = dict(
                            query=user_msg_clean, system_prompt=system_prompt,
                            history=chat_history, temperature=0.85, max_tokens=2048,
                        )
                        upstream = None
                        # 2026-10-21 FIX-3：预算走总账（旧实现写死 30s）——
                        # 与非流式主链同为 min(单跳额度, 轮次剩余)。
                        main_hop_budget = min(
                            MAIN_CHAIN_LLM_TIMEOUT_SECONDS,
                            remaining_turn_llm_budget(llm_deadline),
                        )
                        try:
                            if true_stream:
                                # ⚠️ 真流式下**传输时间不得计入 LLM 预算**：token 要在
                                # 锁内 yield 给适配器，慢客户端会让「等消费者」的时间
                                # 落在 asyncio.timeout 里，把健康的生成误杀成超时。
                                # 故额度只在「等下一个上游 token」时被扣减（递降量），
                                # 而不是一次性钉死一个绝对墙钟截止点 —— 后者的等待
                                # 时间会被传输/消费的墙钟一起吃掉（2026-10-21 复核实测：
                                # 每块消费 50ms + 单跳预算 50ms → 第 2 句之前即
                                # TimeoutError + aclose 上游，用户只收到第一句）。
                                guard = IncrementalReplyGuard(**guard_ctx)
                                llm_budget_left = float(main_hop_budget)
                                upstream = request_llm.chat_stream(**generation_args)
                                streamer = upstream.__aiter__()
                                while True:
                                    if llm_budget_left <= 0:
                                        raise TimeoutError("流式生成超出轮次预算")
                                    wait_start = time.monotonic()
                                    async with asyncio.timeout(llm_budget_left):
                                        try:
                                            token = await streamer.__anext__()
                                        except StopAsyncIteration:
                                            break
                                    # 只扣「等这个 token」的耗时；随后的 _emit/yield
                                    # （传输与消费）不参与 LLM 预算。
                                    llm_budget_left -= time.monotonic() - wait_start
                                    if not token:
                                        continue
                                    for piece in guard.feed(token):
                                        async for event in _emit(piece):
                                            yield event
                                    if guard.stop_requested:
                                        # 截断类规则命中：不再消费上游（其后文语义上
                                        # 属于同一甩锅反问），丢弃全部未发。
                                        truncated = True
                                        break
                            elif use_true_stream:
                                async with asyncio.timeout(main_hop_budget):
                                    upstream = request_llm.chat_stream(**generation_args)
                                    async for token in upstream:
                                        if token:
                                            full_reply += token
                            else:
                                async with asyncio.timeout(main_hop_budget):
                                    full_reply = str(
                                        await request_llm.chat(**generation_args) or ""
                                    )
                        except Exception as exc:
                            logger.warning("上游生成中断: %s", exc)
                            generation_interrupted = True
                            # 「首 token 前失败」包含真流式下 guard 尚未收到任何内容
                            # （``has_received()`` 为假）的情形 —— 此时按既有错误
                            # 路径回执，而不是走终裁/兜底。
                            if not full_reply and (guard is None or not guard.has_received()):
                                error_reply = "（生成回复时出现异常, 请稍后重试）"
                                # 单帧下发且不进 ``reply``/``_ack``：错误哨兵不得
                                # 作为「角色说过的话」落库（只留用户行）。
                                yield {"type": "token", "content": error_reply}
                                yield {"type": "done", "reply": error_reply, "emotion": None,
                                       "process_time": round(time.perf_counter() - stream_start, 3)}
                                return
                        finally:
                            if upstream is not None:
                                await upstream.aclose()

                    # 2026-10-21 R4：唯一全文终裁入口。
                    # 真流式：``guard.finish()`` = 仍未下发的余量（已发前缀**不重判**）；
                    # buffered/直复：``full_reply`` = 完整草稿（旧行为，逐字不变）。
                    # 单一调用点也是既有 AST 契约（``test_time_word_recalibration``
                    # 钉死流式 _finalize_reply 调用点唯一、实参对齐非流式主链）。
                    remainder = guard.finish() if guard is not None else full_reply
                    if guard is not None and guard.safety_replacement and not remainder:
                        remainder = ""
                    if guard is not None and not generation_interrupted and remainder:
                        # 供应商静默截断可观测性（设计复核 2026-10-21 条目「降级语义
                        # 不闭合」）：provider 内层超时/异常时**吞掉并正常结束**，
                        # 编排层看不到截断信号。真流式下已发内容不回滚，但「尾段没有
                        # 句末标点」是唯一可得的旁证——只记结构化事件，不改行为。
                        from utils.prompt_sanitize import SENTENCE_TERMINATORS

                        if remainder[-1] not in SENTENCE_TERMINATORS:
                            logger.info(
                                "stream_tail_without_terminator session=%s chars=%d",
                                session_id, len(remainder),
                            )
                    # 唯一终裁调用点（既有 AST 契约）：buffered 模式恒调；
                    # 真流式只在「有余量」或「一个字都没发出去」时调 —— 后者用于
                    # 空流/全被剥离的 empty_reply 兜底；已成功发完则绝不补发兜底句。
                    should_finalize = (
                        guard is None
                        or bool(remainder)
                        or (not reply and not guard.safety_replacement)
                    )
                    final_text = (
                        self._finalize_reply(
                            remainder,
                            character_id,
                            user_msg=user_msg_clean,
                            session_id=session_id,
                            last_turn_age_seconds=ctx.get("last_turn_age_seconds"),
                            reply_mode=turn_reply_mode,
                        )
                        if should_finalize
                        else ""
                    )
                    if guard is not None and not final_text and not reply and guard.safety_replacement:
                        # 输出安全闸命中且**未发任何内容**：整句换成拦截文案
                        # （批式语义）；已有前缀时不追加文案，只终止后续。
                        final_text = guard.safety_replacement
                    if final_text:
                        async for event in _emit(final_text):
                            yield event
                    if guard is not None and not reply:
                        # 守卫+终裁全空（退化/全被剥离/空流）：角色化兜底，与批式一致。
                        from utils.fallback_lines import get_fallback_line

                        # 2026-10-21 R7：兜底句跟随本轮模式（不热读）
                        fallback = get_fallback_line(
                            character_id, "empty_reply",
                            turn_reply_mode or ctx.get("reply_mode"),
                        )
                        async for event in _emit(fallback):
                            yield event
                    if truncated:
                        logger.info(
                            "stream_truncated_by_guard session=%s emitted=%d",
                            session_id, len(reply),
                        )
                    if generation_interrupted and reply:
                        # 已发后中断：不重试、不换供应商重生成（重放会半句+整句
                        # 重复）。只记结构化事件；用户看到的内容就是已确认前缀。
                        logger.warning(
                            "stream_truncated_degraded session=%s chars=%d",
                            session_id, len(reply),
                        )

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
                    # 统一收尾：覆盖准备、生成、发布全程（W2，2026-09-27）。
                    # aclose/取消也保留用户行及已确认前缀；数据库轻写完成前不释放会话锁。
                    if ctx:
                        # 准备已完成（含生成中断/发布中断）：全量 after_process 落
                        # 用户行 + 真正被传输确认的前缀，行为与重构前一致。
                        finish = asyncio.create_task(asyncio.to_thread(
                            self._after_process, user_msg_clean, reply[:accepted_end],
                            emotion_state, session_id, character_id,
                            turn_id=str(ctx.get("ax_turn_id") or ""),
                            reply_id=str(ctx.get("ax_reply_id") or ""),
                            memory_fact_sources=ctx.get("memory_fact_sources"),
                            knowledge_snippets=ctx.get("knowledge_snippets"),
                        ))
                        try:
                            await asyncio.shield(finish)
                        except asyncio.CancelledError:
                            await finish
                            raise
                    elif user_msg_clean:
                        # 准备阶段取消/异常（ctx 未产出）：这一轮已过安全清洗，
                        # 用户行必须留下，否则用户的话凭空消失、下一轮「不记得说过」。
                        # 不跑全量 after_process——记忆管线不该消化一个没有回复的半轮。
                        memory = self.components.get("memory")
                        writer = (
                            getattr(memory, "write_chat_history_sync", None)
                            if memory is not None else None
                        )
                        if writer is not None:
                            try:
                                writer(
                                    user_msg=user_msg_clean,
                                    reply=reply[:accepted_end],
                                    session_id=session_id,
                                    character_id=str(character_id or ""),
                                    turn_id=pre_turn_id,
                                )
                            except Exception as e:  # noqa: BLE001
                                logger.warning(
                                    "准备阶段收尾轻写失败 session=%s: %s", session_id, e,
                                )

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
