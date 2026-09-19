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
from orchestrator._init_mixin import _InitPhasesMixin
from orchestrator._stream_mixin import _StreamPipelineMixin
from orchestrator.session_locks import SessionLockManager
from orchestrator.voice_detector import detect_voice_request as _detect_voice_request
from tools.base_tool import ToolResult
from utils.character_helpers import normalize_character_card
from utils.health_check import _is_healthy

logger = logging.getLogger("orchestrator.optimized")
project_root = Path(__file__).resolve().parent.parent

# ── 会话排队参数（2026-09-19）──
# 同一 session 的两条消息必须**串行**（情感引擎 / 记忆状态不可并发写），但
# 「串行」不等于「丢弃」。微信场景下用户连发两条是**常态**，而旧实现在锁被占用时
# 直接返回「处理中, 请稍候...」—— 既把用户刚发的这句话整个丢掉，又用机器口吻播报状态
# （慢 provider 下几乎条条触发）。改为有界排队后，用户会依次收到两条**真实回复**，
# 这也正是真人的做法：先看完两条，再逐条回。
# 等待上限取 60s：上层 `wechat_connector._call_user_manager` 的线程池预算是 120s，
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

    # ── 额外向后兼容属性（合并自根目录 orchestrator.py）──

    @property
    def _llm(self):
        return self.components.get("llm")

    @property
    def _rag(self):
        return self.components.get("rag")

    @property
    def _safety(self):
        return self.components.get("safety")

    @property
    def _pii(self):
        return self.components.get("pii")

    @property
    def _injection(self):
        return self.components.get("injection")

    @property
    def _multimodal(self):
        return self.components.get("multimodal")

    @property
    def _character_manager(self):
        return self.components.get("character_manager")

    @property
    def _character_service(self):
        return self.components.get("character_service")

    @staticmethod
    def _run_async(coro) -> Any:
        """安全运行协程，支持有/无事件循环两种情况。

        代理到 common.async_utils.run_async，保持向后兼容。
        """
        from utils.async_utils import run_async
        return run_async(coro)

    # ── 角色卡人设动态加载（v3.1 新增）──
    # 缓存：character_id -> 人设片段字符串。避免每条消息都读文件。
    # 注意：类级共享 + 多线程访问（uvicorn 多 worker / 线程池），必须加锁；
    # 且必须有淘汰策略——旧实现 `if len(cache) < 100` 只在未满时写入，
    # 一旦达到 100 条，后续所有角色都会**永久**回退到磁盘读取。
    _character_persona_cache: dict[str, str] = {}
    _character_persona_loaded: bool = False
    _character_persona_cache_lock: threading.Lock = threading.Lock()
    _CHARACTER_PERSONA_CACHE_MAX = 100

    @classmethod
    def invalidate_character_persona_cache(cls, character_id: str | None = None) -> None:
        """清除角色卡人设缓存。

        - character_id 为 None 时清空全部缓存
        - 否则只清除指定角色，供角色更新/删除后即时生效
        """
        with cls._character_persona_cache_lock:
            if character_id is None:
                cls._character_persona_cache.clear()
            else:
                cls._character_persona_cache.pop(character_id, None)

    @classmethod
    def _load_character_persona_segment(cls, character_id: str) -> str:
        """根据 character_id 加载角色卡人设，返回可追加到 system prompt 的片段。

        - character_id 为空 / "default" / "demo" 时返回空串（保持基线人设）
        - 先按 {character_id}.json 找文件，找不到再遍历 config/characters/ 匹配 JSON 内部 id
        - 使用 normalize_character_card 展平 SillyTavern 等嵌套格式，确保 name/description/
          personality/speaking_style/scenario 等字段被正确提取
        - 结果缓存，避免重复 IO
        """
        if not character_id or character_id in ("default", "demo"):
            return ""

        # 命中缓存（加锁：类级字典在多线程下会被并发读写）
        with cls._character_persona_cache_lock:
            if character_id in cls._character_persona_cache:
                return cls._character_persona_cache[character_id]

        import json as _json
        chars_dir = project_root / "config" / "characters"
        raw_card: dict | None = None

        # 1. 直接按文件名查
        direct_path = chars_dir / f"{character_id}.json"
        if direct_path.exists():
            try:
                with open(direct_path, encoding="utf-8") as fh:
                    raw_card = _json.load(fh)
            except (OSError, _json.JSONDecodeError):
                raw_card = None

        # 2. 遍历匹配 JSON 内部 id 字段
        if raw_card is None and chars_dir.exists():
            try:
                for f in chars_dir.glob("*.json"):
                    try:
                        with open(f, encoding="utf-8") as fh:
                            data = _json.load(fh)
                        if data.get("id") == character_id:
                            raw_card = data
                            break
                    except (OSError, _json.JSONDecodeError):
                        continue
            except OSError:
                pass

        if not raw_card:
            cls._store_persona_segment(character_id, "")
            return ""

        # 展平嵌套角色卡格式，提取真实 name/description/personality 等
        card = normalize_character_card(raw_card)

        # 构造人设片段（2026-09-20 行业对齐精简）：
        # 角色的完整设定（description / personality_text / creator_notes / 核心锚点 /
        # 数值维度 / 知识库 / 对话示例）已由 shisi PersonaService → prompt_builder
        # 以全量字段注入 system prompt。旧实现在这里**再次**注入 500 字截断的简介、
        # 500 字截断的备注、60 字截断的锚点与数值维度 —— 属重复内容（SillyTavern
        # 惯例：角色定义只注入一次），且截断版本可能与上方全文矛盾。
        # 此片段仅保留「身份绑定」职责 + base prompt 覆盖不到的字段
        # （口头禅 / 开场白）；scenario 守卫已迁移至
        # CharacterAggregate.build_system_prompt（含导入卡兼容）。
        lines: list[str] = ["=== 角色卡人设 ==="]
        name = card.get("name", "")
        if name:
            lines.append(f"角色名：{name}")

        catchphrases = card.get("catchphrases", [])
        if catchphrases:
            lines.append(f"口头禅：{' / '.join(str(c) for c in catchphrases[:8])}")

        first_mes = card.get("first_mes", "")
        if first_mes:
            lines.append(f"开场白：{str(first_mes)[:300]}")

        lines.append(
            "（该角色的身份、性格、经历、说话风格与扮演规则已在本提示词上方逐节完整注入，"
            "一律以上方内容为准；如与本段冲突，以上方为准。）"
        )

        segment = "\n".join(lines)
        cls._store_persona_segment(character_id, segment)
        return segment

    @classmethod
    def _store_persona_segment(cls, character_id: str, segment: str) -> None:
        """写入角色人设缓存（加锁 + FIFO 淘汰）。

        旧实现为 `if len(cache) < 100: cache[id] = segment`：达到上限后
        **不再写入任何新角色**，且永不淘汰，导致超出部分的角色每次消息都重新
        读盘解析。现改为满员时先淘汰最早插入的一条。
        """
        with cls._character_persona_cache_lock:
            if (
                character_id not in cls._character_persona_cache
                and len(cls._character_persona_cache) >= cls._CHARACTER_PERSONA_CACHE_MAX
            ):
                cls._character_persona_cache.pop(
                    next(iter(cls._character_persona_cache)), None
                )
            cls._character_persona_cache[character_id] = segment

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

    @staticmethod
    def _tool_intent_names(query: str) -> set[str]:
        """用零成本规则筛选明显工具意图，普通聊天不额外调用一次模型。"""
        text = query.strip().lower()
        if not text:
            return set()
        groups = {
            "weather": ("天气", "气温", "温度", "下雨", "降雨", "weather"),
            "search": ("搜索", "查一下", "查询资料", "网上找", "最新消息", "新闻", "search"),
            "calendar": ("今天几号", "星期几", "当前日期", "现在几点", "日期", "calendar"),
            "calculator": ("计算", "算一下", "等于多少", "calculator"),
            "set_reminder": ("提醒我", "设个提醒", "到点叫我", "remind"),
            "query_reminders": ("有哪些提醒", "查看提醒", "我的提醒"),
            "time_awareness": ("节假日", "农历", "工作日", "放假吗"),
            "memory": ("你还记得", "记得我", "我的偏好", "关于我的记忆"),
            "character_card": ("创建角色", "角色卡", "人物资料", "构建角色"),
            "web_summary": ("总结网页", "概括网页", "这个链接", "网页摘要", "http://", "https://"),
            "image_gen": ("生成图片", "画一张", "画个", "生成一张图", "image"),
            "scheduler": ("安排日程", "创建日程", "定时任务"),
        }
        return {
            name for name, keywords in groups.items()
            if any(keyword in text for keyword in keywords)
        }

    async def _run_tools_if_needed(
        self,
        llm: Any,
        query: str,
        system_prompt: str,
        history: list | None,
        affinity_level: int = 0,
    ) -> str:
        """只对明显工具意图调用模型，并并行执行互不依赖的工具。"""
        tools = self.components.get("tools")
        if not tools or not tools.registry or llm is None:
            return ""

        intent_names = self._tool_intent_names(query)
        if not intent_names:
            return ""
        schemas = [
            schema for schema in tools.registry.get_tools_by_permission(affinity_level)
            if schema.get("function", {}).get("name") in intent_names
        ]
        if not schemas:
            return ""

        try:
            tool_resp = llm.chat_with_tools(
                query=query,
                system_prompt=system_prompt,
                history=history or [],
                tools=schemas,
                temperature=0.85,
                max_tokens=2048,
            )
            if inspect.isawaitable(tool_resp):
                tool_resp = await asyncio.wait_for(tool_resp, timeout=15.0)
            tool_calls = tool_resp.get("tool_calls") if tool_resp else None
            if not tool_calls:
                return ""
        except Exception as e:  # noqa: BLE001
            # 旧写法 `except (asyncio.TimeoutError, Exception)`：asyncio.TimeoutError
            # 本就是 Exception 子类，元组写法纯冗余（易误读为"两类异常分别处理"）。
            logger.debug("工具意图识别失败: %s", e)
            return ""

        async def _dispatch(tc: dict[str, Any]) -> dict[str, Any]:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            args_raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                args = {}
            try:
                result = await asyncio.to_thread(
                    tools.dispatch, name, args, affinity_level=affinity_level,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("工具 %s 执行异常: %s", name, e)
                result = ToolResult(False, error="tool_execution_failed")
            return {"name": name, "result": result.to_dict()}

        results = await asyncio.gather(*(_dispatch(tc) for tc in tool_calls))
        summary = "\n".join(
            f"[{r['name']}] {json.dumps(r['result'], ensure_ascii=False)}"
            for r in results
        )
        return f"\n\n[工具调用结果]\n{summary}\n请根据以上结果自然地回复用户。"

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
                self._request_emotion_engines[key] = engine
            self._request_emotion_engines_access[key] = current_time

        # close() 放到锁外：可能涉及 I/O，不应阻塞其他请求
        for old in evicted:
            try:
                old.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("请求级情绪引擎淘汰关闭异常: %s", e)
        return engine

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

        # 并行执行独立任务
        recent = self.components["memory"].get_recent_context(3)
        loop = asyncio.get_running_loop()

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
                memory_context = task_result or ""  # type: ignore[assignment]
            elif name == "rag":
                if task_result:
                    import json
                    rag_context = json.dumps(task_result, sort_keys=True, ensure_ascii=False)
                else:
                    rag_context = ""

        # 对话历史 + 摘要
        chat_history: list = []
        chat_summary: str = ""
        mem = self.components.get("memory")
        if mem and hasattr(mem, 'get_chat_context'):
            chat_history, chat_summary = mem.get_chat_context(
                session_id=session_id,
            )

        # 世界信息动态注入
        world_info = ""
        wip = self.components.get("world_info")
        if wip:
            try:
                world_info = wip.render()
            except Exception as e:  # noqa: BLE001
                logger.debug("World info render failed: %s", e)

        # 组装 system prompt
        system_prompt = self.components["persona"].build_system_prompt(
            emotion_state=emotion_state,
            memory_context=memory_context,
            rag_context=rag_context,
            chat_summary=chat_summary,
            world_info=world_info,
            character_id=character_id,
        )

        # 角色卡人设动态注入（v3.1）
        if character_id and character_id not in ("default", "demo"):
            char_segment = self._load_character_persona_segment(character_id)
            if char_segment:
                system_prompt = (
                    f"{system_prompt}\n\n"
                    f"# 当前必须扮演的角色（最高优先级）\n"
                    f"{char_segment}\n\n"
                    f"你当前正在扮演以上角色。"
                    f"回复时必须使用该角色的名字、身份、性格、说话风格和口头禅；"
                    f"不要以'十四'或通用 AI 身份自居。"
                )

        if persona_enhancement:
            system_prompt = f"{system_prompt}\n\n{persona_enhancement}"

        # 工具调用
        affinity_level = self._get_affinity_level(emotion_state)
        llm = self.components.get("llm")
        tool_results = await self._run_tools_if_needed(
            llm,
            user_msg_clean,
            system_prompt,
            chat_history,
            affinity_level=affinity_level,
        )
        if tool_results:
            system_prompt = f"{system_prompt}\n\n{tool_results}"

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
            _parts = {
                "character": len(locals().get("char_segment") or ""),
                "memory": len(str(memory_context or "")),
                "rag": len(str(rag_context or "")),
                "summary": len(str(chat_summary or "")),
                "world": len(str(world_info or "")),
                "history_msgs": len(chat_history or []),
                "total": len(system_prompt),
            }
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
        }

    def _after_process(
        self,
        user_msg_clean: str,
        reply: str,
        emotion_state: Any,
        session_id: str,
        character_id: str,
    ) -> str:
        """共享后处理：after_chat → ASE on_chat → 好感度同步。

        Returns:
            emotion_tag 字符串。
        """
        emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""

        mem_kwargs: dict[str, Any] = dict(
            user_msg=user_msg_clean,
            reply=reply,
            session_id=session_id,
        )
        if hasattr(self.components["memory"], "after_chat"):
            sig = inspect.signature(self.components["memory"].after_chat)
            if "emotion" in sig.parameters:
                mem_kwargs["emotion"] = emotion_tag
            elif "emotion_tag" in sig.parameters:
                mem_kwargs["emotion_tag"] = emotion_tag
            # 后台线程执行 after_chat，避免其内部 async→sync 桥接
            # （vector_memory._run_async 的 run_coroutine_threadsafe.result()）
            # 在主事件循环线程自死锁，导致 worker 卡死。
            # 使用复用的单线程池而非每条消息新建线程（见 _get_background_executor）。
            def _safe_after_chat(**kw):
                try:
                    self.components["memory"].after_chat(**kw)
                except Exception as e:  # noqa: BLE001
                    logger.warning("after_chat failed, skipping: %s", e)
            try:
                self._get_background_executor().submit(_safe_after_chat, **mem_kwargs)
            except RuntimeError as e:
                # executor 已随 shutdown() 关闭（进程收尾阶段）→ 同步执行一次，
                # 避免后处理被静默丢弃。
                logger.warning("后处理线程池已关闭，改为同步执行: %s", e)
                _safe_after_chat(**mem_kwargs)
        self.components["ase"].on_chat(user_msg_clean, reply)

        # 好感度同步
        if character_id and character_id != "default" and emotion_state is not None:
            try:
                from api.deps import deps as _deps
                shisi_reg = getattr(_deps, "shisi_reg", None)
                mapper = getattr(shisi_reg, "affinity_mapper", None)
                if mapper is not None:
                    affection_pts = getattr(emotion_state, "affection_points", 0.0)
                    mapper.sync(
                        character_id=character_id,
                        affection_points=affection_pts,
                        reason=f"emotion:{emotion_tag}",
                        source="chat",
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("Affinity/Stage 同步跳过: %s", e)

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
                )
                emotion_state = ctx["emotion_state"]
                system_prompt = ctx["system_prompt"]
                chat_history = ctx["chat_history"]

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
                    return {"reply": "抱歉，处理超时，请稍后重试", "error": "timeout"}

                # === 一致性检查（复用 my_character/consistency_checker.py） ===
                from my_character.consistency_checker import check_and_correct_reply

                character_card = None
                persona_service = self.components.get("persona")
                card_loader = getattr(persona_service, "_load_character_card", None)
                if character_id not in ("default", "demo") and callable(card_loader):
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

                # === 计数反诘：用户连续说"没事"达到阈值时追加反诘 ===
                try:
                    rebuttal = self._counter_rebuttal.check_and_increment(
                        user_msg_clean, session_id
                    )
                    if rebuttal:
                        reply = f"{reply}\n{rebuttal}"
                except Exception as e:  # noqa: BLE001
                    logger.debug("计数反诘检查异常: %s", e)
                # === 反诘结束 ===

                output_result = self.components["safety"].check_output(reply)
                if not output_result.is_safe:
                    reply = self.components["safety"].safe_alternative(output_result.category)

                # ── 共享后处理（after_chat → ASE → 好感度同步）──
                emotion_tag = self._after_process(
                    user_msg_clean, reply, emotion_state, session_id, character_id,
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
                return {"reply": "（处理消息时出现异常, 请稍后重试）", "error": "internal_error"}

    def health_check(self) -> dict[str, Any]:
        results = {}
        all_ok = True

        for name, component in self.components.items():
            if hasattr(component, "health_check"):
                try:
                    status = component.health_check()
                    results[name] = status
                    # 用 _is_healthy 过滤掉懒加载字段（base_prompt_cached, card_loaded 等）
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
