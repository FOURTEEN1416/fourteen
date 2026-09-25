"""优化版对话编排器 — 核心对话处理与组件编排

模块结构：
- 本文件：``OptimizedOrchestrator`` 主类，负责 ``__init__`` / 会话锁 / 上下文准备 /
  ``process_message`` / 健康检查等核心流程。
- ``orchestrator._init_mixin._InitPhasesMixin``：``initialize`` 阶段化拆分
  （``initialize`` 直接调用 10 个 ``_init_*``，``_init_memory_and_rag`` 再级联
  ``_init_ase_and_scheduler`` / ``_init_tools`` / ``_init_rag``，共 13 个阶段方法）。
- ``orchestrator._stream_mixin._StreamPipelineMixin``：``process_message_stream`` SSE 流式。

Mixin 通过 ``self.components`` 与主类共享状态，公共 API 100% 兼容。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from my_character.emotion_engine import EmotionEngine
from my_character.persona_engine import is_external_character_id
from orchestrator import tool_gate
from orchestrator._init_mixin import _InitPhasesMixin
from orchestrator._stream_mixin import _StreamPipelineMixin
from orchestrator.session_locks import SessionLockManager
from orchestrator.voice_detector import detect_voice_request as _detect_voice_request
from tools.base_tool import ToolResult
from utils.health_check import _is_healthy

logger = logging.getLogger("orchestrator.optimized")
project_root = Path(__file__).resolve().parent.parent

# ── 会话排队参数（2026-09-19）──
# 同一 session 的两条消息必须**串行**（情感引擎 / 记忆状态不可并发写），但
# 「串行」不等于「丢弃」。微信场景下用户连发两条是**常态**，而旧实现在锁被占用时
# 直接返回「处理中, 请稍候...」—— 既把用户刚发的这句话整个丢掉，又用机器口吻播报状态
# （慢 provider 下几乎条条触发）。改为有界排队后，用户会依次收到两条**真实回复**，
# 这也正是真人的做法：先看完两条，再逐条回。
# 等待上限取 60s：上层 `wechat_connector._call_user_manager` 的共享循环桥接超时是 120s，
# 须给本轮生成留出余量，否则排队会把生成预算吃光。
_SESSION_QUEUE_TIMEOUT = 60.0
_SESSION_QUEUE_POLL = 0.2


class OptimizedOrchestrator(_InitPhasesMixin, _StreamPipelineMixin):
    """
    优化版对话编排器 (fast 模式)

    简洁流程: 安全→PII脱敏→注入检测→情感→记忆→RAG→LLM→输出安全→存储→ASE
    """

    # 请求级情绪引擎缓存配置（与 session 锁同构：TTL 优先，其次按最久未访问淘汰）。
    # 旧实现只在 shutdown() 里整体清空，运行期**只增不减** —— 每个
    # (session_id, character_id) 组合都会常驻一个 EmotionEngine（含 500 条情绪历史），
    # 长跑服务下随会话数无界增长。
    _MAX_REQUEST_ENGINES = 256
    _REQUEST_ENGINE_TTL_SECONDS = 3600  # 1小时无使用后回收

    def __init__(self, character_manager=None, **_kwargs):
        self.components: dict[str, Any] = {}
        self._initialized = False
        # per-session 异步锁：委托给 SessionLockManager（TTL 缓存 + 淘汰的唯一 owner）。
        # 旧实现把同一套逻辑（常量/计数器/清理算法）在本类内联复制了一份，
        # 与 orchestrator/session_locks.py 完全重复 —— 两处必然漂移。
        self._session_lock_manager = SessionLockManager()
        self._executor = None  # 延迟初始化的共享线程池
        self._bg_executor = None  # 延迟初始化的后处理串行线程池（见 _get_background_executor）
        # P1-6：profile_sync_agent 在飞会话集（同会话最多一个并发同步，防连发叠跑）
        self._profile_sync_inflight: set[str] = set()
        # Web/API 调用未经过 UserManager 时，也必须按“会话 × 角色”隔离情绪状态。
        # 外部显式传入 emotion_engine（如微信 UserManager）时仍优先使用外部实例。
        self._request_emotion_engines: dict[str, EmotionEngine] = {}
        self._request_emotion_engines_lock = threading.Lock()
        # 每个请求级引擎的最近访问时间，用于 TTL/最久未访问淘汰（见 _cleanup_expired_request_engines）
        self._request_emotion_engines_access: dict[str, float] = {}
        self._request_engine_cleanup_counter: int = 0
        # 后台 task 引用集合（避免被 GC 回收，asyncio.create_task 文档要求）。
        # 必须为实例变量；若为类变量会导致多实例共享同一集合引发 race condition。
        self._background_tasks: set[Any] = set()
        # 计数反诘模块：跟踪用户连续说"没事"等敷衍词的次数
        from my_character.counter_rebuttal import CounterRebuttal
        self._counter_rebuttal = CounterRebuttal()
        # 角色管理器：可从外部注入，也可在 initialize() 中由 ConfigLoader 创建
        if character_manager is not None:
            self.components["character_manager"] = character_manager

    def _get_executor(self):
        if self._executor is None:
            import concurrent.futures
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="opt_init"
            )
        return self._executor

    def _get_background_executor(self):
        """后处理专用线程池（单线程、串行）。

        `_after_process` 需要在后台跑 `memory.after_chat`（内部有
        async→sync 桥接，在主循环线程里会自死锁）。旧实现**每条消息**
        `threading.Thread(...).start()` 新建一个线程：
        高并发下线程数随消息量线性增长，线程创建/销毁本身也是开销，
        且与 memory 的 SQLite 写竞争（多个 after_chat 并发写同一库）。
        改为复用单线程 executor：串行化后处理，线程数恒定。
        """
        if self._bg_executor is None:
            import concurrent.futures
            self._bg_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="opt_after"
            )
        return self._bg_executor

    def shutdown(self):
        """关闭 OptimizedOrchestrator 并释放资源。

        注意: 必须调用此方法以确保 ThreadPoolExecutor / 调度器正确关闭，
        避免程序退出时线程池或后台线程资源泄漏。
        """
        scheduler = self.components.get("scheduler")
        if scheduler is not None and hasattr(scheduler, "stop"):
            try:
                scheduler.stop()
                logger.info("OptimizedOrchestrator 调度器已停止")
            except Exception as e:  # noqa: BLE001
                logger.warning("调度器停止异常: %s", e)

        memory = self.components.get("memory")
        if memory is not None and hasattr(memory, "close"):
            try:
                memory.close()
                logger.info("MemoryPipeline 已关闭")
            except Exception as e:  # noqa: BLE001
                logger.warning("MemoryPipeline 关闭异常: %s", e)

        tools = self.components.get("tools")
        if tools is not None and hasattr(tools, "close"):
            try:
                tools.close()
                logger.info("ToolDispatcher 执行线程池已关闭")
            except Exception as e:  # noqa: BLE001
                logger.warning("ToolDispatcher 关闭异常: %s", e)

        with self._request_emotion_engines_lock:
            request_engines = list(self._request_emotion_engines.values())
            self._request_emotion_engines.clear()
            self._request_emotion_engines_access.clear()
        for engine in request_engines:
            try:
                engine.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("请求级情绪引擎关闭异常: %s", e)

        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("OptimizedOrchestrator 线程池已关闭")

        if self._bg_executor is not None:
            # wait=True：确保退出前把已入队的 after_chat（记忆落盘）写完
            self._bg_executor.shutdown(wait=True)
            self._bg_executor = None
            logger.info("OptimizedOrchestrator 后处理线程池已关闭")

    # ── backward-compatible property aliases (for rest_api etc.) ──

    @property
    def _ase(self):
        return self.components.get("ase")

    @property
    def _emotion(self):
        return self.components.get("emotion")

    @property
    def _memory(self):
        return self.components.get("memory")

    @property
    def _persona(self):
        return self.components.get("persona")

    @property
    def _tools(self):
        return self.components.get("tools")

    # ── 组件快捷访问器 ──

    @property
    def _llm(self):
        return self.components.get("llm")

    @property
    def _safety(self):
        return self.components.get("safety")

    @property
    def _character_manager(self):
        return self.components.get("character_manager")

    @staticmethod
    def _run_async(coro) -> Any:
        """安全运行协程，支持有/无事件循环两种情况。

        代理到 common.async_utils.run_async，保持向后兼容。
        """
        from utils.async_utils import run_async
        return run_async(coro)

    def invalidate_character_persona_cache(self, character_id: str | None = None) -> None:
        """清除角色卡人设缓存（character_id 为 None 时清空全部）。

        2026-09-21 唯一身份路径：这里**不再**自建第二套人设缓存。角色卡的读取与
        mtime 感知缓存唯一 owner 是 `PersonaService._load_character_card`；本方法
        只把 API 侧（角色增/删/改）的失效请求转发给它。
        """
        invalidate = getattr(
            self.components.get("persona"), "invalidate_character_cache", None
        )
        if callable(invalidate):
            invalidate(character_id)

    def _get_affinity_level(self, emotion_state: Any) -> int:
        """从 emotion_state 中提取整数好感度等级（0-8）。"""
        if emotion_state is None:
            return 0
        affinity = getattr(emotion_state, "affinity", 0)
        if isinstance(affinity, dict):
            return int(affinity.get("level", 0))
        try:
            return int(affinity)
        except (TypeError, ValueError):
            return 0

    # （旧 _tool_intent_names 关键词裁决已删除：关键词从"裁决"降级为"晋级"，
    #  规则 owner 迁至 orchestrator/tool_gate.should_escalate —— 2026-09-20）

    async def _run_tools_if_needed(
        self,
        llm: Any,
        query: str,
        system_prompt: str,
        history: list | None,
        affinity_level: int = 0,
        session_key: str = "",
        user_id: int | None = None,
    ) -> tuple[str, str]:
        """三级意图管线：L0 零成本晋级线 → L1 LLM 终审（function calling）
        → 工具执行。

        Args:
            session_key: 会话键（微信侧即 ``owner:peer@im.wechat``），用于
                澄清状态机归属与提醒投递目标。
            user_id: 用户 id，随 ``_meta`` 注入需要归属的工具（服务端注入，
                不由 LLM 决定归属）。

        Returns:
            ``(tool_results, direct_reply)`` —— ``tool_results`` 非空时按旧惯例
            拼入 system_prompt 交主链生成；``direct_reply`` 非空时直接作为本轮
            回复（澄清提问场景，跳过主链避免二次生成）。
        """
        tools = self.components.get("tools")
        if not tools or not tools.registry or llm is None:
            return "", ""

        sm = getattr(self.components.get("memory"), "structured_memory", None)
        pending: dict | None = None
        if sm is not None and session_key:
            try:
                pending = sm.get_active_pending_intent(session_key)
            except Exception:  # noqa: BLE001
                logger.debug("查询待澄清任务失败", exc_info=True)

        # L0：零成本晋级线——未命中直接走主聊天链路（普通闲聊零影响）
        if not tool_gate.should_escalate(query, has_pending_intent=pending is not None):
            return "", ""

        pending_slots: dict | None = None
        pending_ask_count = 0
        if pending:
            try:
                pending_slots = {
                    "intent": pending.get("intent", "set_reminder"),
                    **json.loads(pending.get("slots_json") or "{}"),
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                pending_slots = {"intent": pending.get("intent", "set_reminder")}
            pending_ask_count = int(pending.get("ask_count") or 0)

        review_system, review_query = tool_gate.build_review_messages(
            query,
            system_prompt,
            history,
            pending_slots=pending_slots,
            pending_ask_count=pending_ask_count,
        )

        async def _review_call(extra_rule: str = "") -> dict[str, Any] | None:
            """L1 终审调用（独立低温度；失败降级主链，不阻塞聊天）"""
            try:
                resp = llm.chat_with_tools(
                    query=review_query,
                    system_prompt=(
                        review_system + extra_rule if extra_rule else review_system
                    ),
                    history=history or [],
                    tools=[
                        *tools.registry.get_tools_by_permission(affinity_level),
                        tool_gate.ASK_USER_TOOL,
                    ],
                    temperature=0.3,
                    max_tokens=1024,
                )
                if inspect.isawaitable(resp):
                    resp = await asyncio.wait_for(resp, timeout=15.0)
                return resp if isinstance(resp, dict) else None
            except Exception as e:  # noqa: BLE001
                logger.debug("工具终审失败，降级主链: %s", e)
                return None

        resp = await _review_call()
        if resp is None:
            return "", ""
        tool_calls = resp.get("tool_calls") or []

        # 防假承诺：声称会做却没调任何工具（生产实证「听到啦」）→ 强制复核一次
        if not tool_calls and tool_gate.contains_promise(resp.get("content") or ""):
            logger.info("[tool_gate] 拦截空口承诺，强制复核一次")
            resp = await _review_call(
                extra_rule=(
                    "\n【系统复核】你上一轮答应了用户却没有调用任何工具。"
                    "重新判断：信息齐全必须真的调用工具；不全就调 ask_user 提问；"
                    "做不到承诺就不要承诺。"
                )
            )
            if resp is None:
                return "", ""
            tool_calls = resp.get("tool_calls") or []

        async def _dispatch(tc: dict[str, Any]) -> dict[str, Any]:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            args_raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                args = {}
            # 服务端注入调用归属（仅声明需要的工具；不由 LLM 决定归属）
            tool_inst = (
                tools.registry.get(name)
                if hasattr(tools.registry, "get") else None
            )
            if tool_inst is not None and getattr(tool_inst, "wants_call_context", False):
                args["_meta"] = {"session_key": session_key, "user_id": user_id}
            try:
                result = await asyncio.to_thread(
                    tools.dispatch, name, args,
                    affinity_level=affinity_level,
                    caller_id=str(user_id or session_key or ""),
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("工具 %s 执行异常: %s", name, e)
                result = ToolResult(False, error="tool_execution_failed")
            return {"name": name, "result": result.to_dict()}

        # 分支一：ask_user 澄清（只有 ask_user、无真工具时才走这里）
        ask = tool_gate.extract_ask_user(tool_calls)
        if ask is not None:
            if sm is None or not session_key:
                return "", ""
            if pending_ask_count >= 2:
                # 两轮澄清仍未成 → 放弃，回归闲聊（防无限追问骚扰）
                sm.resolve_pending_intent(session_key, "cancelled")
                return "", ""
            known = ask.get("known") if isinstance(ask.get("known"), dict) else {}
            slots = {"intent": "set_reminder", **(known or {})}
            sm.upsert_pending_intent(
                session_key,
                slots.get("intent", "set_reminder"),
                slots,
                pending_ask_count + 1,
                str(ask.get("question") or ""),
                user_id=user_id,
            )
            logger.info(
                "[tool_gate] 澄清提问 session=%s ask_count=%s missing=%s",
                session_key, pending_ask_count + 1, ask.get("missing"),
            )
            return "", str(ask.get("question") or "信息有点不全，我再跟你确认下哈。")

        real_calls = tool_gate.limit_tool_calls(
            [
                tc for tc in tool_calls
                if (tc.get("function", {}) or {}).get("name") != "ask_user"
            ]
        )
        if real_calls:
            # 2026-09-21：禁止「先标 fulfilled 再执行」——旧实现任何工具
            # （含 calendar）都会把 pending 标完成，set_reminder 被权限拒绝时
            # 提醒未落库、状态却已完成 → 到点永不投递。
            results = await asyncio.gather(*(_dispatch(tc) for tc in real_calls))
            tool_names = [r.get("name", "") for r in results]
            any_ok = any(
                bool((r.get("result") or {}).get("success")) for r in results
            )
            has_set_reminder = "set_reminder" in tool_names
            if pending and sm is not None:
                # 仅当真正写入提醒（set_reminder 成功）或其它真工具成功时才结案
                if has_set_reminder:
                    set_ok = any(
                        bool((r.get("result") or {}).get("success"))
                        for r in results
                        if r.get("name") == "set_reminder"
                    )
                    if set_ok:
                        sm.resolve_pending_intent(session_key, "fulfilled")
                    else:
                        # 工具失败：保持 pending，允许下一轮重试；记日志
                        logger.warning(
                            "[tool_gate] set_reminder 执行失败 session=%s results=%s",
                            session_key,
                            [
                                (r.get("name"), (r.get("result") or {}).get("error"))
                                for r in results
                            ],
                        )
                elif any_ok and pending.get("intent") != "set_reminder":
                    sm.resolve_pending_intent(session_key, "fulfilled")
                elif has_set_reminder is False and pending.get("intent") == "set_reminder":
                    # 模型调度了别的工具但用户托付是提醒 → 不结案，防假完成
                    logger.warning(
                        "[tool_gate] 托付=set_reminder 但调度了 %s，pending 不结案 session=%s",
                        tool_names, session_key,
                    )
                    # 若调度了查询类工具且消息含托付信号，强制补跑 set_reminder
                    # （用 pending 槽位最优猜测；无时间则不补）
                    slots = pending.get("slots_json") if isinstance(pending.get("slots_json"), dict) else {}
                    if not slots:
                        import json as _json
                        try:
                            slots = _json.loads(pending.get("slots_json") or "{}")
                        except Exception:  # noqa: BLE001
                            slots = {}
                    content = str(slots.get("content") or "").strip()
                    trigger = str(slots.get("trigger_time") or "").strip()
                    if content and trigger:
                        forced = await _dispatch({
                            "function": {
                                "name": "set_reminder",
                                "arguments": json.dumps(
                                    {"content": content, "trigger_time": trigger},
                                    ensure_ascii=False,
                                ),
                            }
                        })
                        results.append(forced)
                        tool_names.append("set_reminder")
                        if bool((forced.get("result") or {}).get("success")):
                            sm.resolve_pending_intent(session_key, "fulfilled")
                            logger.info(
                                "[tool_gate] 已强制补跑 set_reminder session=%s trigger=%s",
                                session_key, trigger,
                            )
            # C1：untrusted 信封 + 失败禁称成功 + C2 结果截断
            try:
                _cfg = getattr(self, "components", {}).get("config") or {}
                _tools_cfg = (
                    _cfg.get("tools") if isinstance(_cfg, dict) else None
                ) if hasattr(_cfg, "get") else None
                limits = tool_gate.load_tool_limits(
                    _tools_cfg if isinstance(_tools_cfg, dict) else _cfg
                )
            except Exception:  # noqa: BLE001
                limits = tool_gate.load_tool_limits(None)
            wrapped = tool_gate.wrap_tool_results(
                results, chars_max=limits["tool_result_chars_max"]
            )
            logger.info(
                "[tool_gate] 终审调度工具 session=%s tools=%s any_ok=%s",
                session_key, tool_names, any_ok,
            )
            return wrapped, ""

        # 分支三：模型判纯闲聊（无承诺、无工具）——pending 存在说明用户转移话题
        if pending and sm is not None:
            sm.resolve_pending_intent(session_key, "cancelled")
        return "", ""

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取 per-session 异步锁，确保不同 session 可并行处理。

        唯一 owner 是 ``orchestrator.session_locks.SessionLockManager``：
        TTL（1 小时未访问）+ 最大 1000 的缓存淘汰都在那里实现。
        本方法只做委托——旧实现在本类内联复制了同一套逻辑，属重复 owner。
        """
        return self._session_lock_manager.get_lock(session_id)

    async def _await_session_free(
        self, session_id: str, timeout: float | None = None
    ) -> bool:
        """等待同会话上一轮处理结束（有界）。本就空闲则立即返回 True。

        为什么不直接 `await lock.acquire()`：调用方随后仍要走既有的
        `async with lock:` 分支，而 `asyncio.Lock` **不可重入** —— 在这里抢先拿到锁
        会在 `async with` 处死锁。所以这里只"等它空出来"，真正的互斥仍交给原锁。

        Args:
            timeout: 等待上限（秒）。None 时取模块级 `_SESSION_QUEUE_TIMEOUT`
                —— 用 None 而非默认值绑定，是为了让该常量可被运行期/测试覆盖。
        """
        budget = _SESSION_QUEUE_TIMEOUT if timeout is None else timeout
        lock = self._get_session_lock(session_id)
        if not lock.locked():
            return True
        deadline = time.monotonic() + budget
        while lock.locked():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_SESSION_QUEUE_POLL)
        return True

    # ─────────────────────────────────────────────────────────────
    # 共享预处理 / 后处理（process_message 与 process_message_stream 复用）
    # ─────────────────────────────────────────────────────────────

    def _get_request_emotion_engine(
        self,
        session_id: str,
        character_id: str,
        explicit_engine: Any | None = None,
    ) -> Any:
        """返回请求所属的情绪引擎，避免角色切换继承另一人格的情绪。"""
        if explicit_engine is not None:
            return explicit_engine

        template = self.components["emotion"]
        # 测试替身和第三方兼容引擎不强制复制，保持旧接口兼容。
        if not isinstance(template, EmotionEngine):
            return template

        key = f"{session_id or 'default'}::{character_id or 'default'}"
        current_time = time.time()
        evicted: list[Any] = []
        with self._request_emotion_engines_lock:
            self._request_engine_cleanup_counter = (self._request_engine_cleanup_counter + 1) % 100
            if len(self._request_emotion_engines) >= self._MAX_REQUEST_ENGINES or \
               (self._request_emotion_engines and self._request_engine_cleanup_counter == 0):
                evicted = self._cleanup_expired_request_engines(current_time, keep=key)

            engine = self._request_emotion_engines.get(key)
            if engine is None:
                engine = EmotionEngine(
                    llm_gateway=getattr(template, "_llm", None),
                    use_llm=getattr(template, "_classifier", None) is not None,
                    classifier_mode=getattr(template, "_classifier_mode", "rule"),
                )
                self._restore_request_affinity(engine, session_id, character_id)
                self._restore_request_emotion(engine, session_id, character_id)
                self._request_emotion_engines[key] = engine
            self._request_emotion_engines_access[key] = current_time

        # close() 放到锁外：可能涉及 I/O，不应阻塞其他请求
        for old in evicted:
            try:
                old.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("请求级情绪引擎淘汰关闭异常: %s", e)
        return engine

    @staticmethod
    def _restore_request_emotion(
        engine: Any, session_id: str, character_id: str
    ) -> None:
        """新建请求级引擎必须从持久化情绪恢复（并按离线时长衰减）。

        2026-09-21 重扫：旧实现只恢复好感度，**情绪/能量/轮次计数一律归零** ——
        重启或引擎 LRU 淘汰后角色"记得你但心情归零"，人设状态自相矛盾。
        对标 nana `EmotionalState`：加载即 `decay_mood()`。
        """
        if not session_id or not hasattr(engine, "restore"):
            return
        try:
            from utils.emotion_state import load_emotion_state

            snapshot = load_emotion_state(session_id, str(character_id or ""))
            if snapshot:
                engine.restore(snapshot)
        except Exception as e:  # noqa: BLE001
            logger.debug("情感状态恢复跳过: %s", e)

    def apply_request_emotion_decay(self, hours: float) -> int:
        """对请求级情绪缓存扇出时间衰减（每日维护调用）。

        2026-09-22：审计 item45 只修了 UserManager 引擎（微信路径），
        web 对话主路径用的是本缓存 —— 不衰减则 web 用户情绪永不冷却。
        返回实际衰减的引擎数。
        """
        if hours <= 0:
            return 0
        with self._request_emotion_engines_lock:
            engines = list(self._request_emotion_engines.values())
        applied = 0
        for eng in engines:
            try:
                if hasattr(eng, "apply_time_decay"):
                    eng.apply_time_decay(hours)
                    applied += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("请求级情绪衰减失败: %s", e)
        return applied

    @staticmethod
    def _restore_request_affinity(
        engine: Any, session_id: str, character_id: str
    ) -> None:
        """审计 item42：新建请求级引擎必须从已持久化好感度恢复起点。

        旧实现从 affection_points=0 起步，而对话尾 AffinityMapper.sync 按
        delta=目标−已存 增量同步——每条消息都把 shisi 侧已存好感度向 0 拉低
        最多 3 分（聊得越久好感度越低）。恢复后首轮 delta≈0，漂移消失。
        """
        try:
            from api.deps import deps as _deps
            mapper = getattr(getattr(_deps, "shisi_reg", None), "affinity_mapper", None)
            if mapper is None:
                return
            points = mapper.current_points(character_id or "", session_id or "")
            if not points or points <= 0:
                return
            from shisi.affinity import scale as affinity_scale
            state = getattr(engine, "state", None)
            if state is None:
                return
            state.affection_points = float(points)
            state.affinity = affinity_scale.points_to_level(points)
        except Exception as e:  # noqa: BLE001
            logger.debug("请求级情绪引擎恢复好感度失败: %s", e)

    def _cleanup_expired_request_engines(
        self, current_time: float, keep: str | None = None
    ) -> list[Any]:
        """回收请求级情绪引擎，返回需要 ``close()`` 的实例（由调用方在锁外关闭）。

        策略与 ``SessionLockManager._cleanup_expired_locks`` 同构：

        1. 优先回收超过 TTL 未访问的。活跃会话每轮消息都会刷新访问时间，
           因此**不会被回收**——情绪连续性不受影响；被回收的都是已闲置会话。
        2. 仍超上限时，按最久未访问淘汰。

        ``keep`` 用于保护当前正在取用的 key，避免刚拿到就被自己淘汰掉。
        """
        evicted: list[Any] = []

        def _drop(k: str) -> None:
            engine = self._request_emotion_engines.pop(k, None)
            self._request_emotion_engines_access.pop(k, None)
            if engine is not None:
                evicted.append(engine)

        for k, last in list(self._request_emotion_engines_access.items()):
            if k == keep:
                continue
            if current_time - last > self._REQUEST_ENGINE_TTL_SECONDS:
                _drop(k)

        if len(self._request_emotion_engines) >= self._MAX_REQUEST_ENGINES:
            ordered = sorted(
                ((k, t) for k, t in self._request_emotion_engines_access.items() if k != keep),
                key=lambda x: x[1],
            )
            excess = len(self._request_emotion_engines) - self._MAX_REQUEST_ENGINES + 32
            for k, _ in ordered[:excess]:
                _drop(k)

        if evicted:
            logger.debug(
                "回收 %d 个请求级情绪引擎，当前总数: %d",
                len(evicted), len(self._request_emotion_engines),
            )
        return evicted

    async def _prepare_context(
        self,
        user_msg_clean: str,
        session_id: str,
        character_id: str,
        emotion_engine: Any | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """共享预处理逻辑。

        执行：PersonaExtractor 设置 → 并行任务（人格/情感/记忆/RAG）
        → 对话历史 → 世界信息 → system prompt 组装 → 角色卡注入
        → 人格增强 → 工具调用。

        调用方需在调用前完成安全检查、PII 脱敏、注入检测，
        并自行管理会话锁。

        Returns:
            包含 ``emotion_state``、``system_prompt``、``chat_history``、
            ``affinity_level``、``user_msg_clean`` 的字典。
        """
        # 请求级画像 ID（多用户/多角色隔离）。禁止再切换共享组件的全局 user_id。
        pe = self.components.get("persona_extractor")
        effective_user_id = (
            f"{character_id}:{session_id}" if session_id else f"{character_id}"
        )

        # 并行执行独立任务（recent 传 session_id：会话隔离，防跨用户串扰）
        # P1-10（2026-09-21 审查修复）：get_recent_context 自 v1.17 起读 DB——
        # 旧实现直接在事件循环上同步调用（同函数内 retrieve_context 已包 executor，
        # 两种口径）。挪到 executor，与其余并行任务同规。
        loop = asyncio.get_running_loop()
        recent = await loop.run_in_executor(
            None,
            lambda: self.components["memory"].get_recent_context(3, session_id=session_id),
        )

        tasks: dict[str, Any] = {}
        if pe is not None:
            tasks["persona"] = pe.process_message(
                message=user_msg_clean,
                context=recent,
                user_id=effective_user_id,
            )
        active_emotion_engine = self._get_request_emotion_engine(
            session_id,
            character_id,
            explicit_engine=emotion_engine,
        )
        tasks["emotion"] = loop.run_in_executor(
            None, active_emotion_engine.analyze,
            user_msg_clean, recent,
        )
        tasks["memory"] = loop.run_in_executor(
            None,
            lambda: self.components["memory"].retrieve_context(
                query=user_msg_clean, session_id=session_id, top_k=5,
            ),
        )
        rag = self.components["rag"]
        # 优先使用 retrieve_async（带超时保护）；回退到 run_in_executor + 同步 retrieve
        if hasattr(rag, "retrieve_async"):
            async def _rag_async():
                retrieve_params = inspect.signature(
                    rag.retrieve_async
                ).parameters
                if "character_id" in retrieve_params:
                    return await rag.retrieve_async(
                        user_msg_clean, character_id=character_id,
                    )
                return await rag.retrieve_async(user_msg_clean)
            tasks["rag"] = _rag_async()
        else:
            # 兼容无 retrieve_async 的旧实现
            retrieve_params = inspect.signature(rag.retrieve).parameters
            if "character_id" in retrieve_params:
                def rag_call():
                    return rag.retrieve(user_msg_clean, character_id=character_id)
            else:
                def rag_call():
                    return rag.retrieve(user_msg_clean)
            tasks["rag"] = loop.run_in_executor(None, rag_call)

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        persona_enhancement = ""
        emotion_state = None
        memory_context = ""
        rag_context = ""

        for name, task_result in zip(tasks.keys(), results, strict=False):
            if isinstance(task_result, Exception):
                logger.debug("并行任务 %s 异常: %s", name, task_result)
                continue
            if name == "persona":
                persona_enhancement = task_result or ""  # type: ignore[assignment]
            elif name == "emotion":
                emotion_state = task_result
            elif name == "memory":
                raw_mem = task_result
                # 清洗注入 system 的记忆：去掉对话痕迹反射与碎片事实
                try:
                    from utils.prompt_sanitize import (
                        sanitize_episodic,
                        sanitize_fact_list,
                        sanitize_reflections,
                    )

                    if isinstance(raw_mem, dict):
                        raw_mem = dict(raw_mem)
                        raw_mem["facts"] = sanitize_fact_list(
                            raw_mem.get("facts") or raw_mem.get("semantic")
                        )
                        raw_mem["reflections"] = sanitize_reflections(
                            raw_mem.get("reflections")
                        )
                        raw_mem["episodic"] = sanitize_episodic(raw_mem.get("episodic"))
                        raw_mem.pop("working", None)  # 工作记忆走 messages，不进 system
                        raw_mem["_user_key"] = session_id or ""
                except Exception as e:  # noqa: BLE001
                    logger.debug("sanitize memory_context failed: %s", e)
                memory_context = raw_mem or ""  # type: ignore[assignment]
            elif name == "rag":
                # A4：禁止 json.dumps 整包进 prompt —— 只取可读 content 文本
                from orchestrator.context_budget import rag_payload_to_text

                rag_context = rag_payload_to_text(task_result)

        # 对话历史 + 摘要
        # P1-10：get_chat_context 内含 DB 读 + 可能触发摘要 LLM 调用
        # （ConversationSummarizer._summarize），旧实现直接在事件循环上同步调用。
        chat_history: list = []
        chat_summary: str = ""
        mem = self.components.get("memory")
        if mem and hasattr(mem, 'get_chat_context'):
            # 2026-09-21 重扫：按角色取历史（切换角色不再继承他人台词）
            _hist_sig = inspect.signature(mem.get_chat_context)
            _hist_kwargs: dict[str, Any] = {"session_id": session_id}
            if "character_id" in _hist_sig.parameters:
                _hist_kwargs["character_id"] = str(character_id or "")
            chat_history, chat_summary = await loop.run_in_executor(
                None,
                lambda: mem.get_chat_context(**_hist_kwargs),
            )
        # 2026-09-21：清洗交给 LLM 的历史 — 只保留 user/assistant 角色、去空/系统错误、
        # 防止「当前用户消息」与 history 重复，避免模型分不清该回哪句。
        try:
            from utils.prompt_sanitize import sanitize_llm_history

            chat_history = sanitize_llm_history(
                chat_history, current_user_message=user_msg_clean
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("sanitize_llm_history failed: %s", e)

        # 当前话题续聊钩子（2026-09-23）：从近期对话提取话题，进 memory_context，
        # 由 persona_service 渲染为「# 当前话题」+ 续聊指令（像人一样把话说下去）。
        try:
            from utils.prompt_sanitize import extract_current_topics

            current_topics = extract_current_topics(
                chat_history, current_user_message=user_msg_clean
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("extract_current_topics failed: %s", e)
            current_topics = []

        # 世界信息动态注入
        world_info = ""
        wip = self.components.get("world_info")
        if wip:
            try:
                world_info = wip.render()
            except Exception as e:  # noqa: BLE001
                logger.debug("World info render failed: %s", e)

        # ── A4 上下文预算与去重（知识优先，memory 行级去重）──
        from orchestrator.context_budget import DEFAULT_BUDGET, apply_budget

        budgeted = apply_budget(
            rag_context=rag_context,
            memory_context=memory_context,  # P0-1：dict 保型下传，禁止 str() 打碎契约
            chat_summary=str(chat_summary or ""),
            chat_history=chat_history,
            budget=DEFAULT_BUDGET,
        )
        rag_context = budgeted["rag_context"]
        memory_context = budgeted["memory_context"]
        chat_summary = budgeted["chat_summary"]
        chat_history = budgeted["chat_history"]
        context_lengths = budgeted["lengths"]

        # ── B-d 跨会话尾巴：实时窗口尚浅时注入持久化历史切片（untrusted）──
        # 话题键并入 memory_context（persona_service 渲染续聊钩子）
        if current_topics:
            if isinstance(memory_context, dict):
                memory_context["current_topics"] = current_topics
            elif isinstance(memory_context, str) and memory_context:
                memory_context = (
                    f"{memory_context}\n[当前话题] {'、'.join(current_topics)}"
                )
            else:
                memory_context = {"current_topics": current_topics}
        session_tail = ""
        hist_len = (
            len(chat_history) if isinstance(chat_history, list) else 0
        )
        if hist_len < 2 and mem is not None:
            try:
                tail_lines: list[str] = []
                if hasattr(mem, "get_cross_session_tail"):
                    tail_lines = mem.get_cross_session_tail(session_id, limit=8) or []
                elif hasattr(mem, "structured_memory") and hasattr(
                    mem.structured_memory, "get_cross_session_tail"
                ):
                    tail_lines = mem.structured_memory.get_cross_session_tail(
                        session_id, limit=8
                    ) or []
                from orchestrator.context_budget import (
                    SESSION_TAIL_INJECT_MAX_RECENT_MESSAGES,
                    format_session_tail,
                )

                if hist_len < SESSION_TAIL_INJECT_MAX_RECENT_MESSAGES and tail_lines:
                    session_tail = format_session_tail(tail_lines)
                    if session_tail:
                        context_lengths = dict(context_lengths or {})
                        context_lengths["session_tail"] = len(session_tail)
            except Exception as e:  # noqa: BLE001
                logger.debug("跨会话尾巴注入失败（忽略）: %s", e)

        if session_tail:
            # 尾巴入记忆结构体独立键（persona_service 渲染为 untrusted 段；
            # dict 契约下不得再做 f-string 拼接）
            if isinstance(memory_context, dict):
                memory_context["session_tail"] = session_tail
            else:
                memory_context = (
                    f"{memory_context}\n\n{session_tail}".strip()
                    if memory_context
                    else session_tail
                )

        # 组装 system prompt
        # 注入顺序对齐 research：角色设定（prompt_builder）→ 世界/知识/记忆/状态
        # （PersonaService）→ 角色片段 → 工具结果(历史后/PHI前) → reply_mode
        import uuid as _uuid

        ax_turn_id = _uuid.uuid4().hex[:12]
        ax_reply_id = _uuid.uuid4().hex[:12]
        system_prompt = self.components["persona"].build_system_prompt(
            emotion_state=emotion_state,
            memory_context=memory_context,
            rag_context=rag_context,
            chat_summary=chat_summary,
            world_info=world_info,
            character_id=character_id,
            # 批6b 项11：仅作 prompt_builder 知识槽的检索查询，不回显进 system
            user_message=user_msg_clean,
        )

        # 身份唯一 owner = PersonaService 解析到的那张卡（内置卡 / 文件卡），
        # 已在 build_system_prompt 内一次性注入。2026-09-21 拆除此前的第二身份段
        # （「当前必须扮演的角色（最高优先级）」+ strip_default_identity）：
        # 角色卡若与默认人格同现，才需要"以谁为准"的补丁；单一来源后不需要。

        if persona_enhancement:
            system_prompt = f"{system_prompt}\n\n{persona_enhancement}"

        # 工具调用（三级意图管线；direct_reply 为澄清提问，直接作为本轮回复）
        affinity_level = self._get_affinity_level(emotion_state)
        llm = self.components.get("llm")
        tool_results, direct_reply = await self._run_tools_if_needed(
            llm,
            user_msg_clean,
            system_prompt,
            chat_history,
            affinity_level=affinity_level,
            session_key=session_id,
            user_id=user_id,
        )
        if tool_results:
            # C 正式位次：工具结果插入「对话历史之后 / 扮演规则之前」
            from orchestrator.context_budget import inject_tool_context_before_phi

            system_prompt = inject_tool_context_before_phi(system_prompt, tool_results)
            # AX P2：工具结果入因果账本（可回放「这句是否因工具而变」）
            if session_id:
                try:
                    import uuid as _uuid

                    from shisi.agent_plane.event_ledger import EVENT_TOOL_RESULT
                    from shisi.agent_plane.runtime import get_ledger

                    _tid = ax_turn_id
                    get_ledger().append(
                        session_key=session_id,
                        event_type=EVENT_TOOL_RESULT,
                        character_id=str(character_id or ""),
                        actor="orchestrator",
                        turn_id=str(_tid),
                        reply_id=str(ax_reply_id),
                        payload={
                            "chars": len(str(tool_results)),
                            "preview": str(tool_results)[:400],
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("tool result ledger append failed: %s", e)

        # ── 回复模式（web 控制端可切换，2026-09-19）──
        # 必须放在**最后**：角色卡/人格块里常写着"必须写动作神态"之类的格式要求，
        # 而另一处又要求"像真人发微信" —— 两者冲突时模型会随机挑一个，
        # 表现为同一角色在「（她停下脚步，回头看你）」和「嗯，下了一下午了」之间乱跳。
        # 放在末尾以获得最高显著性，并明确"本节优先于角色卡中的格式要求"。
        try:
            from utils.reply_mode import reply_mode_instruction

            system_prompt = (
                f"{system_prompt}\n\n"
                "# 输出格式（本节优先于角色卡中任何与之冲突的格式要求）\n"
                f"{reply_mode_instruction()}"
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("附加回复模式指令失败（忽略）: %s", e)

        # ── 提示词规模可观测（2026-09-19）──
        # 为什么必须埋点：此前所有关于"提示词太长/设定压过对话"的判断都只能靠猜。
        # 实测（25 个角色）角色常驻段仅 364~1843 字符，中位 1415 —— 与"一万三千字"
        # 的直觉相差一个数量级。没有数字就会做出错误的架构决策。
        # 见 docs/adr/ADR-0015。含各动态块的实际占比。
        try:
            # `character` = 常驻角色段（prompt_builder 产物）长度：即 system 中第一段
            # 动态注入块之前的部分。旧实现统计的是**已删除**的第二身份段
            # （`locals().get("char_segment")`），默认角色恒为 0，曾把人引向
            # 「人设没注入」的错判（见 LOG 2026-09-21）。
            _cut = len(system_prompt)
            for _marker in ("# 记忆上下文", "# 对话角色说明", "# 世界与时间", "# 角色知识库"):
                _i = system_prompt.find(_marker)
                if 0 <= _i < _cut:
                    _cut = _i
            _parts = {
                "character": _cut,
                "memory": len(str(memory_context or "")),
                "rag": len(str(rag_context or "")),
                "summary": len(str(chat_summary or "")),
                "world": len(str(world_info or "")),
                "history_msgs": len(chat_history or []) if isinstance(chat_history, list) else 0,
                "total": len(system_prompt),
            }
            if context_lengths:
                _parts.update(context_lengths)
            logger.info(
                "[prompt] total=%d character=%d rag=%d memory=%d summary=%d world=%d hist_msgs=%d",
                _parts["total"], _parts["character"], _parts["rag"], _parts["memory"],
                _parts["summary"], _parts["world"], _parts["history_msgs"],
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("提示词规模埋点失败（忽略）: %s", e)

        return {
            "emotion_state": emotion_state,
            "system_prompt": system_prompt,
            "chat_history": chat_history,
            "affinity_level": affinity_level,
            "user_msg_clean": user_msg_clean,
            "direct_reply": direct_reply,
            # A3：chat_round 由 prepare 透传，禁止 stream 内二次查库
            "chat_round": len(chat_history) if isinstance(chat_history, list) else 0,
            "context_lengths": context_lengths,
            "ax_turn_id": ax_turn_id,
            "ax_reply_id": ax_reply_id,
        }

    def _after_process(
        self,
        user_msg_clean: str,
        reply: str,
        emotion_state: Any,
        session_id: str,
        character_id: str,
        turn_id: str = "",
        reply_id: str = "",
    ) -> str:
        """共享后处理：after_chat → ASE on_chat → 好感度同步。

        Returns:
            emotion_tag 字符串。
        """
        emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
        import uuid as _uuid

        ax_turn_id = str(turn_id or _uuid.uuid4().hex[:12])
        ax_reply_id = str(reply_id or _uuid.uuid4().hex[:12])

        mem_kwargs: dict[str, Any] = dict(
            user_msg=user_msg_clean,
            reply=reply,
            session_id=session_id,
            # 2026-09-21 重扫：归属随写入落库（旧实现两行无身份 → 切换角色后
            # 新角色把上一角色的话当自己的；「分不清谁说的」的存储层根因）
            character_id=str(character_id or ""),
            turn_id=ax_turn_id,
        )
        memory = self.components["memory"]
        if hasattr(memory, "after_chat"):
            sig = inspect.signature(memory.after_chat)
            if "emotion" in sig.parameters:
                mem_kwargs["emotion"] = emotion_tag
            elif "emotion_tag" in sig.parameters:
                mem_kwargs["emotion_tag"] = emotion_tag
            if "history_already_written" in sig.parameters:
                mem_kwargs["history_already_written"] = True
            # 包 Q · B-a：chat_history 两行**同步**轻写，返回前下一轮即可读到
            if hasattr(memory, "write_chat_history_sync"):
                try:
                    memory.write_chat_history_sync(
                        user_msg=user_msg_clean,
                        reply=reply,
                        emotion_tag=emotion_tag,
                        session_id=session_id,
                        character_id=str(character_id or ""),
                        turn_id=ax_turn_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("sync history write failed: %s", e)
            # 后台线程执行 after_chat 重活（向量/事实/日记），
            # 避免其内部 async→sync 桥接在主事件循环线程自死锁。
            def _safe_after_chat(**kw):
                try:
                    memory.after_chat(**kw)
                except Exception as e:  # noqa: BLE001
                    logger.warning("after_chat failed, skipping: %s", e)
            try:
                self._get_background_executor().submit(_safe_after_chat, **mem_kwargs)
            except RuntimeError as e:
                logger.warning("后处理线程池已关闭，改为同步执行: %s", e)
                _safe_after_chat(**mem_kwargs)
        # ASE：按会话键记账（2026-09-21 P1）。无 session_id 时不写入全局引擎，
        # 避免把某用户的聊天写进共享紧迫度/配额。
        ase = self.components.get("ase")
        if ase is not None and session_id:
            try:
                from proactive.ase_hub import ASEHub

                if isinstance(ase, ASEHub):
                    ase.on_chat(session_id, user_msg_clean, reply)
                elif hasattr(ase, "on_chat"):
                    ase.on_chat(user_msg_clean, reply)
            except Exception as e:  # noqa: BLE001
                logger.debug("ASE on_chat skipped: %s", e)

        # 用户画像：正则关键字提取已从聊天热路径剔除（2026-09-21 用户裁决）。
        # 唯一写权威：profile_sync_agent / L1 工具 → EventLedger 投影。
        if session_id:
            try:
                from shisi.agent_plane.runtime import append_chat_events

                append_chat_events(
                    session_key=session_id,
                    character_id=str(character_id or ""),
                    turn_id=ax_turn_id,
                    reply_id=ax_reply_id,
                    user_msg=user_msg_clean,
                    reply=reply if isinstance(reply, str) else "",
                    slots={
                        "emotion_tag": str(emotion_tag or ""),
                        "turn_id": ax_turn_id,
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("ledger turn events failed: %s", e)
            # P1-6（2026-09-21 审查修复）：
            # ① 旧实现**每条消息无条件**跑一次 profile_sync_agent LLM 工具链——
            #    现在只在用户原话命中画像/记忆信号（L0 晋级线）时触发；
            # ② 旧实现占用单 worker 的 after_chat 串行池、lambda 里再
            #    asyncio.run 新建/销毁循环（与 provider 的按 loop 缓存互相
            #    乒乓）——现在直接提交到常驻 sync loop，不再挤占后处理队列。
            try:
                from orchestrator.tool_gate import has_profile_signal

                if has_profile_signal(user_msg_clean):
                    llm_for_sync = self.components.get("llm")
                    sm_for_sync = None
                    mem = self.components.get("memory")
                    if mem is not None:
                        sm_for_sync = getattr(mem, "structured_memory", None)
                    if session_id not in self._profile_sync_inflight:
                        from llm_provider.multi_provider_gateway import _get_sync_loop
                        from tools.builtin.profile_agent_tools import (
                            run_profile_sync_agent,
                        )

                        fut = asyncio.run_coroutine_threadsafe(
                            run_profile_sync_agent(
                                llm_for_sync, session_id, user_msg_clean,
                                reply if isinstance(reply, str) else "",
                                sm_for_sync,
                            ),
                            _get_sync_loop(),
                        )
                        self._profile_sync_inflight.add(session_id)
                        fut.add_done_callback(
                            lambda _f, _s=session_id: (
                                self._profile_sync_inflight.discard(_s),
                                _f.exception(),  # 取出异常，避免 "never retrieved" 刷屏
                            )
                        )
            except Exception as e:  # noqa: BLE001
                logger.debug("profile_sync_agent schedule failed: %s", e)

        # 好感度同步 — user×character。
        # 🔴 块E（2026-09-22）双真源根治：`user_id` 此前传 **session_id**
        # （`<owner>:<peer>`），而写入侧 `user_scheduler._persist_affinity`
        # 用的是 **裸 user_id**（如 `o9cq80-...@im.wechat`）。两套键空间
        # 按构造**零交集** —— 该字段由 2026-09-21 隔离批（73ded57）引入
        # 「user×character」时未与写入侧对齐口径。生产实证（129 行审计 vs
        # affinity_state.json 3 键）：
        #   · `affinity_records` 主键是 `4:o9cq80...@im.wechat::62105bca`（带 owner）
        #   · `affinity_state.json` 主键是 `4:o9cq805...@im.wechat::62105bca`（带 owner）
        #   即"带 owner 的那份"被写进审计日志，"回放源"却是另一份。
        # 归类：本项目最高频缺陷家族 —— **格式类事实由推断而非生产取样确定**。
        # 现统一为 `user_key_from_session`（唯一 owner，语义 = 会话键原样返回，
        # 与 memory / 事实 / 工具记忆的归属键同口径）。
        if character_id and emotion_state is not None:
            try:
                from api.deps import deps as _deps
                shisi_reg = getattr(_deps, "shisi_reg", None)
                mapper = getattr(shisi_reg, "affinity_mapper", None)
                if mapper is not None:
                    affection_pts = getattr(emotion_state, "affection_points", 0.0)
                    from shisi.memory.legacy.structured_memory import (
                        StructuredMemory,
                    )

                    aura_user_key = StructuredMemory.user_key_from_session(session_id or "")
                    mapper.sync(
                        character_id=character_id,
                        affection_points=affection_pts,
                        reason=f"emotion:{emotion_tag}",
                        source="chat",
                        user_id=aura_user_key,
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("Affinity/Stage 同步跳过: %s", e)

        # 情感状态持久化 + 主动消息「注意力」信号（2026-09-21 重扫）。
        # 旧实现：情绪仅在进程内存里 —— 重启或请求级引擎 LRU 淘汰后回到中性，
        # 而好感度（affection_points）是持久化的 → 人设状态自相矛盾
        # （"记得你但心情归零"）。对标 nana `emotional_state.py`：mood 与
        # relationship 同文件持久化、加载时按时间衰减。
        if session_id:
            try:
                from utils.emotion_state import save_emotion_state

                eng = self._get_request_emotion_engine(session_id, character_id)
                snapshot = eng.snapshot() if hasattr(eng, "snapshot") else None
                if snapshot:
                    save_emotion_state(session_id, str(character_id or ""), snapshot)
            except Exception as e:  # noqa: BLE001
                logger.debug("情感状态持久化跳过: %s", e)
            # 主动消息注意力：用户开口 = 交互信号（提升 response_rate、刷新
            # hours_since_chat 基准）。旧实现把 hours_since_chat 硬编码为 0.0。
            try:
                ase = self.components.get("ase")
                if ase is not None and hasattr(ase, "note_user_interaction"):
                    ase.note_user_interaction(session_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("ASE 交互信号记录跳过: %s", e)

        return emotion_tag

    async def process_message(
        self,
        user_msg: str,
        session_id: str = "",
        message_type: str = "text",
        character_id: str = "default",
        emotion_engine: Any | None = None,
        user_llm_config: dict | None = None,
        user_id: int | None = None,
        attachments: list | None = None,
    ) -> dict[str, Any]:
        if not self._initialized:
            return {"reply": "系统初始化中, 请稍候...", "error": "not_initialized"}

        lock = self._get_session_lock(session_id)
        # ── 会话串行：排队等待，而不是丢掉用户这条消息（2026-09-19 修复）──
        # 旧实现：`if lock.locked(): return {"reply": "处理中, 请稍候..."}`
        if not await self._await_session_free(session_id):
            logger.warning(
                "[session] 排队等待超时（>%.0fs），放弃本条 session=%s",
                _SESSION_QUEUE_TIMEOUT, session_id,
            )
            return {"reply": "等下，我还没回完上一条", "error": "queue_timeout"}
        # 注：queue_timeout / not_initialized / internal_error 走统一出口前
        # 不写历史（用户尚未进入生成链，无 assistant 行可写；user 行由调用方
        # 在成功路径的 write_chat_history_sync 负责）。

        # 用户级 LLM gateway（API Key 隔离）：若提供 user_id + user_llm_config，
        # 本次请求使用用户专属 gateway，否则回退到全局共享 gateway。
        request_llm = self.components["llm"]
        if user_llm_config and user_id is not None:
            try:
                from llm_provider import get_user_llm
                request_llm = get_user_llm(user_id, user_llm_config)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to get user-level LLM gateway, falling back to global: %s", e)

        async with lock:
            start_time = time.perf_counter()

            try:
                safety_result = self.components["safety"].check_input(user_msg)
                if not safety_result.is_safe:
                    return {
                        "reply": self.components["safety"].safe_alternative(safety_result.category),
                        "safety_triggered": True,
                    }

                user_msg_clean, _ = self.components["pii"].anonymize(user_msg)

                is_injection, _, _ = self.components["injection"].detect(user_msg_clean)
                if is_injection:
                    user_msg_clean = self.components["injection"].sanitize(user_msg_clean)

                # ── 共享预处理（并行任务、prompt 组装、工具调用） ──
                ctx = await self._prepare_context(
                    user_msg_clean,
                    session_id,
                    character_id,
                    emotion_engine=emotion_engine,
                    user_id=user_id,
                )
                emotion_state = ctx["emotion_state"]
                system_prompt = ctx["system_prompt"]
                chat_history = ctx["chat_history"]

                # ── 澄清直复通道：终审产出澄清提问时跳过主链生成 ──
                # （提问文本已带角色口吻，再过一次主链反而会稀释追问意图）
                if ctx.get("direct_reply"):
                    reply = ctx["direct_reply"]
                else:
                    # ── A2 反诘：生成前检查，连续否认 N 次写入 system（不 append 机器腔）──
                    try:
                        rebuttal_count = self._counter_rebuttal.check_and_increment(
                            user_msg_clean, session_id
                        )
                        if rebuttal_count:
                            from utils.fallback_lines import inject_rebuttal_constraint
                            system_prompt = inject_rebuttal_constraint(
                                system_prompt, rebuttal_count
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.debug("计数反诘检查异常: %s", e)

                    # ── 主 LLM 对话（带 30s 超时保护，使用用户级或全局 gateway） ──
                    try:
                        reply = await asyncio.wait_for(
                            request_llm.chat(
                                query=user_msg_clean,
                                system_prompt=system_prompt,
                                history=chat_history,
                                temperature=0.85,
                                max_tokens=2048,
                                attachments=attachments,
                            ),
                            timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("LLM 调用超时 (30s), session=%s", session_id)
                        from utils.fallback_lines import get_fallback_line
                        from utils.reply_mode import read_reply_mode
                        _fb = get_fallback_line(
                            character_id, "timeout", read_reply_mode()
                        )
                        # 超时旁路也必须落历史：否则用户这句话凭空消失，
                        # 下一轮她「不记得你说过」甚至把旧话当成你刚说的。
                        try:
                            memory = self.components.get("memory")
                            if memory and hasattr(memory, "write_chat_history_sync"):
                                memory.write_chat_history_sync(
                                    user_msg=user_msg_clean,
                                    reply=_fb,
                                    session_id=session_id,
                                    character_id=str(character_id or ""),
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.debug("timeout history write failed: %s", e)
                        return {
                            "reply": _fb,
                            "error": "timeout",
                        }

                # === 一致性检查（复用 my_character/consistency_checker.py） ===
                from my_character.consistency_checker import check_and_correct_reply

                character_card = None
                persona_service = self.components.get("persona")
                card_loader = getattr(persona_service, "_load_character_card", None)
                if is_external_character_id(character_id) and callable(card_loader):
                    character_card = card_loader(character_id)
                # B4 优化：传入 chat_round 避免重复 get_chat_context 查询
                # chat_history 已在 _prepare_context 中获取，直接复用长度
                reply = await check_and_correct_reply(
                    reply=reply,
                    persona_engine=getattr(persona_service, "engine", None),
                    llm_gateway=request_llm,
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self.components.get("memory"),
                    character_card=character_card,
                    chat_round=len(chat_history) if chat_history else 0,
                )
                # === 检查结束 ===

                # A2 空回复兜底：角色化，沉浸式无括号
                if not str(reply or "").strip():
                    from utils.fallback_lines import get_fallback_line
                    from utils.reply_mode import read_reply_mode
                    reply = get_fallback_line(
                        character_id, "empty_reply", read_reply_mode()
                    )

                # 自问自答清洗（2026-09-23）：剥「用户：/角色：」剧本体，
                # 丢掉自问自答里自己答自己的那段（否则拆条发出像她跟自己说话）
                try:
                    from utils.prompt_sanitize import sanitize_reply_text

                    _cleaned_reply = sanitize_reply_text(reply)
                    if _cleaned_reply and _cleaned_reply != reply:
                        logger.debug(
                            "reply sanitized (dialogue/self-talk) len %d→%d",
                            len(str(reply or "")),
                            len(_cleaned_reply),
                        )
                        reply = _cleaned_reply
                except Exception as e:  # noqa: BLE001
                    logger.debug("sanitize_reply_text failed: %s", e)

                # A3：生成后仅对硬违规做轻量替换（流式已推送不改写）
                try:
                    from my_character.consistency_checker import (
                        detect_hard_violation,
                        light_sanitize_hard_violation,
                    )

                    _char_name = ""
                    if isinstance(character_card, dict):
                        _char_name = str(character_card.get("name") or "")
                    if detect_hard_violation(reply, _char_name):
                        reply = light_sanitize_hard_violation(reply)
                        logger.info("A3 硬违规轻量替换 session=%s", session_id)
                except Exception:  # noqa: BLE001
                    pass

                output_result = self.components["safety"].check_output(reply)
                if not output_result.is_safe:
                    reply = self.components["safety"].safe_alternative(output_result.category)

                # ── 共享后处理（after_chat → ASE → 好感度同步）──
                emotion_tag = self._after_process(
                    user_msg_clean,
                    reply,
                    emotion_state,
                    session_id,
                    character_id,
                    turn_id=str(ctx.get("ax_turn_id") or ""),
                    reply_id=str(ctx.get("ax_reply_id") or ""),
                )

                # ── 语音合成（用户明确要求时触发）──
                voice_audio: bytes | None = None
                want_voice = _detect_voice_request(user_msg)
                if want_voice and len(reply) >= 8:
                    voice_mgr = self.components.get("voice")
                    if voice_mgr and hasattr(voice_mgr, 'synthesize') and voice_mgr.enabled:
                        try:
                            # 截取合适长度（微信语音建议 ≤60 字）
                            voice_text = reply[:200]
                            voice_audio = await voice_mgr.synthesize(
                                voice_text, emotion=emotion_tag,
                            )
                            if voice_audio:
                                logger.info("语音合成成功: %d bytes, engine=%s, emotion=%s",
                                            len(voice_audio), voice_mgr.current_engine, emotion_tag)
                            else:
                                logger.warning("语音合成返回空, engine=%s", voice_mgr.current_engine)
                        except Exception as e:
                            logger.warning("语音合成失败: %s", e)

                process_time = time.perf_counter() - start_time

                result: dict[str, Any] = {
                    "reply": reply,
                    "emotion": emotion_state.to_dict() if emotion_state else None,
                    "process_time": round(process_time, 3),
                }
                if voice_audio:
                    result["voice"] = voice_audio
                    # 微信 silk: ~2.4KB/s, 按字数估算时长
                    result["voice_duration_ms"] = min(60000, max(1500, len(reply) * 180))
                return result

            except Exception:
                logger.exception("消息处理异常")
                _err_fb = "（处理消息时出现异常, 请稍后重试）"
                try:
                    memory = self.components.get("memory")
                    if memory and hasattr(memory, "write_chat_history_sync"):
                        memory.write_chat_history_sync(
                            user_msg=user_msg_clean,
                            reply=_err_fb,
                            session_id=session_id,
                            character_id=str(character_id or ""),
                        )
                except Exception:  # noqa: BLE001
                    pass
                return {"reply": _err_fb, "error": "internal_error"}

    def health_check(self) -> dict[str, Any]:
        results = {}
        all_ok = True

        for name, component in self.components.items():
            if hasattr(component, "health_check"):
                try:
                    status = component.health_check()
                    results[name] = status
                    # 用 _is_healthy 过滤掉懒加载字段（card_loaded 等，见 utils/health_check）
                    if not _is_healthy(status):
                        all_ok = False
                except Exception:
                    logger.exception("组件健康检查异常: %s", name)
                    results[name] = {"error": "component_check_failed"}
                    all_ok = False
            else:
                results[name] = "no check"

        return {"healthy": all_ok, "components": results}

    async def test_voice_pipeline(self, text: str = "你好呀，今天天气真不错") -> dict[str, Any]:
        """端到端语音管线测试（供调试用）"""
        voice_mgr = self.components.get("voice")
        if not voice_mgr:
            return {"ok": False, "error": "voice_mgr not found"}
        if not voice_mgr.enabled:
            return {"ok": False, "error": "voice disabled"}

        result = {"ok": False, "timing": {}}
        import time as _time

        # 1. 测试触发检测
        t0 = _time.perf_counter()
        detected = _detect_voice_request(text)
        result["detection"] = {"triggered": detected, "text": text, "latency_ms": round((_time.perf_counter() - t0) * 1000)}

        # 2. 测试合成
        t0 = _time.perf_counter()
        try:
            audio = await voice_mgr.synthesize(text[:50])
            result["synthesis"] = {
                "success": audio is not None,
                "bytes": len(audio) if audio else 0,
                "engine": voice_mgr.current_engine,
                "latency_ms": round((_time.perf_counter() - t0) * 1000),
            }
            if audio:
                result["ok"] = True
        except Exception as e:
            result["synthesis"] = {"success": False, "error": str(e)}

        return result
