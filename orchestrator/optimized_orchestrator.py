"""优化版对话编排器 — 核心对话处理与组件编排

模块结构：
- 本文件：``OptimizedOrchestrator`` 主类，负责 ``__init__`` / 会话锁 / 上下文准备 /
  ``process_message`` / 健康检查等核心流程。
- ``orchestrator._init_mixin._InitPhasesMixin``：``initialize`` 阶段化拆分（9 个 _init_*）。
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
from orchestrator.voice_detector import detect_voice_request as _detect_voice_request
from tools.base_tool import ToolResult
from utils.character_helpers import normalize_character_card
from utils.health_check import _is_healthy

logger = logging.getLogger("orchestrator.optimized")
project_root = Path(__file__).resolve().parent.parent

class OptimizedOrchestrator(_InitPhasesMixin, _StreamPipelineMixin):
    """
    优化版对话编排器 (fast 模式)

    简洁流程: 安全→PII脱敏→注入检测→情感→记忆→RAG→LLM→输出安全→存储→ASE
    """

    # Session锁缓存配置：最大缓存数、锁过期时间（秒）
    _MAX_SESSION_LOCKS = 1000
    _SESSION_LOCK_TTL_SECONDS = 3600  # 1小时无使用后清理

    def __init__(self, character_manager=None, **_kwargs):
        self.components: dict[str, Any] = {}
        self._initialized = False
        # per-session 异步锁，使用带TTL的缓存防止内存无限增长
        self._session_locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._session_lock_access_time: dict[str, float] = {}
        self._locks_mutex = threading.Lock()
        self._lock_cleanup_counter: int = 0  # 替代 hash() 的概率触发
        self._executor = None  # 延迟初始化的共享线程池
        # Web/API 调用未经过 UserManager 时，也必须按“会话 × 角色”隔离情绪状态。
        # 外部显式传入 emotion_engine（如微信 UserManager）时仍优先使用外部实例。
        self._request_emotion_engines: dict[str, EmotionEngine] = {}
        self._request_emotion_engines_lock = threading.Lock()
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
        for engine in request_engines:
            try:
                engine.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("请求级情绪引擎关闭异常: %s", e)

        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
            logger.info("OptimizedOrchestrator 线程池已关闭")

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
    _character_persona_cache: dict[str, str] = {}
    _character_persona_loaded: bool = False

    @classmethod
    def invalidate_character_persona_cache(cls, character_id: str | None = None) -> None:
        """清除角色卡人设缓存。

        - character_id 为 None 时清空全部缓存
        - 否则只清除指定角色，供角色更新/删除后即时生效
        """
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

        # 命中缓存
        if character_id in cls._character_persona_cache:
            return cls._character_persona_cache[character_id]

        import json as _json
        chars_dir = project_root / "config" / "characters"
        raw_card: dict | None = None

        # 1. 直接按文件名查
        direct_path = chars_dir / f"{character_id}.json"
        if direct_path.exists():
            try:
                with open(direct_path, encoding="utf-8") as f:
                    raw_card = _json.load(f)
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
            cls._character_persona_cache[character_id] = ""
            return ""

        # 展平嵌套角色卡格式，提取真实 name/description/personality 等
        card = normalize_character_card(raw_card)

        # 构造人设片段
        lines: list[str] = ["=== 角色卡人设 ==="]
        name = card.get("name", "")
        if name:
            lines.append(f"角色名：{name}")

        desc = card.get("description", "")
        if desc:
            lines.append(f"简介：{desc[:500]}")

        # 创作者备注通常包含更细致的人设，优先作为补充
        creator_notes = card.get("creator_notes") or raw_card.get("creator_notes") or ""
        if creator_notes and creator_notes != desc:
            lines.append(f"细节设定：{str(creator_notes)[:500]}")

        anchors = [str(a) for a in card.get("core_anchors", []) if a]
        # 过长的锚点（>20 字）通常是整句性格描述，归到性格描述中更自然
        short_anchors = [a for a in anchors if len(a) <= 20]
        if short_anchors:
            lines.append(f"核心锚点：{'、'.join(short_anchors)}")

        # 性格维度：优先使用可量化的字典；否则使用文本描述
        personality = card.get("personality", {})
        personality_text = card.get("personality_text", "")
        if personality:
            lines.append("性格维度：")
            dim_map = {
                "warmth": "温暖度", "playfulness": "顽皮度", "independence": "独立性",
                "jealousy": "嫉妒度", "stubbornness": "固执度", "intelligence": "聪慧度",
                "sweetness": "甜美度", "elegance": "优雅度", "mystery": "神秘度",
                "loyalty": "忠诚度", "creativity": "创造力",
            }
            for k, v in personality.items():
                label = dim_map.get(k, k)
                try:
                    val = float(v)
                    lines.append(f"- {label} {val:.2f}")
                except (TypeError, ValueError):
                    lines.append(f"- {label}: {v}")
        elif personality_text:
            lines.append(f"性格描述：{personality_text[:400]}")

        speaking = card.get("speaking_style", {})
        speaking_text = card.get("speaking_style_text", "")
        if speaking and isinstance(speaking, dict):
            style_parts: list[str] = []
            for k, v in speaking.items():
                if k == "catchphrases":
                    continue
                try:
                    val = float(v)
                    if 0 <= val <= 1:
                        style_parts.append(f"{k}={val:.2f}")
                    else:
                        style_parts.append(f"{k}={v}")
                except (TypeError, ValueError):
                    style_parts.append(f"{k}={v}")
            if style_parts:
                lines.append(f"说话风格：{', '.join(style_parts)}")
        elif speaking_text:
            lines.append(f"说话风格：{speaking_text[:400]}")

        catchphrases = card.get("catchphrases", [])
        if catchphrases:
            lines.append(f"口头禅：{' / '.join(str(c) for c in catchphrases[:8])}")

        scenario = card.get("scenario", "")
        if scenario:
            lines.append(f"场景设定：{str(scenario)[:500]}")

        first_mes = card.get("first_mes", "")
        if first_mes:
            lines.append(f"开场白：{str(first_mes)[:300]}")

        segment = "\n".join(lines)
        # 缓存（最多 100 个，防止内存膨胀）
        if len(cls._character_persona_cache) < 100:
            cls._character_persona_cache[character_id] = segment
        return segment

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
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
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

        注意: asyncio.Lock 必须在 async 上下文中创建以绑定正确的事件循环。
        采用延迟创建策略，首次在 async 上下文中调用时才实例化 Lock。

        内存优化：
        - 使用带TTL的锁缓存，防止session过多导致内存无限增长
        - 定期清理过期的session锁（超过1小时未访问）
        - 最大缓存数限制为1000个session
        """
        current_time = time.time()

        with self._locks_mutex:
            # 清理过期锁（每100次访问触发一次清理，避免频繁清理）
            self._lock_cleanup_counter = (self._lock_cleanup_counter + 1) % 100
            if len(self._session_locks) >= self._MAX_SESSION_LOCKS or \
               (len(self._session_locks) > 0 and self._lock_cleanup_counter == 0):
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
            if current_time - last_access > self._SESSION_LOCK_TTL_SECONDS:
                expired_sessions.append(sid)

        for sid in expired_sessions:
            del self._session_locks[sid]
            if sid in self._session_lock_access_time:
                del self._session_lock_access_time[sid]

        # 如果仍然超过最大限制，清理最久未访问的
        if len(self._session_locks) >= self._MAX_SESSION_LOCKS:
            sorted_sessions = sorted(
                self._session_lock_access_time.items(),
                key=lambda x: x[1]
            )
            sessions_to_remove = len(self._session_locks) - self._MAX_SESSION_LOCKS + 100
            for sid, _ in sorted_sessions[:sessions_to_remove]:
                if sid in self._session_locks:
                    del self._session_locks[sid]
                del self._session_lock_access_time[sid]

        if expired_sessions:
            logger.debug("清理 %d 个过期session锁，当前总数: %d", len(expired_sessions), len(self._session_locks))

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
        with self._request_emotion_engines_lock:
            engine = self._request_emotion_engines.get(key)
            if engine is None:
                engine = EmotionEngine(
                    llm_gateway=getattr(template, "_llm", None),
                    use_llm=getattr(template, "_classifier", None) is not None,
                    classifier_mode=getattr(template, "_classifier_mode", "rule"),
                )
                self._request_emotion_engines[key] = engine
            return engine

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
        retrieve_params = inspect.signature(rag.retrieve).parameters
        if "character_id" in retrieve_params:
            def rag_call():
                return rag.retrieve(user_msg_clean, character_id=character_id)
        else:
            # 兼容旧 RAGEngineV2 和测试替身；它们没有角色游标。
            def rag_call():
                return rag.retrieve(user_msg_clean)
        tasks["rag"] = loop.run_in_executor(
            None, rag_call,
        )

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
            import inspect
            sig = inspect.signature(self.components["memory"].after_chat)
            if "emotion" in sig.parameters:
                mem_kwargs["emotion"] = emotion_tag
            elif "emotion_tag" in sig.parameters:
                mem_kwargs["emotion_tag"] = emotion_tag
            try:
                self.components["memory"].after_chat(**mem_kwargs)
            except Exception as e:  # noqa: BLE001
                logger.warning("after_chat failed, skipping: %s", e)
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
    ) -> dict[str, Any]:
        if not self._initialized:
            return {"reply": "系统初始化中, 请稍候...", "error": "not_initialized"}

        lock = self._get_session_lock(session_id)
        if lock.locked():
            return {"reply": "处理中, 请稍候...", "error": "busy"}

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

                # ── 主 LLM 对话（带 30s 超时保护） ──
                try:
                    reply = await asyncio.wait_for(
                        self.components["llm"].chat(
                            query=user_msg_clean,
                            system_prompt=system_prompt,
                            history=chat_history,
                            temperature=0.85,
                            max_tokens=2048,
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
                reply = await check_and_correct_reply(
                    reply=reply,
                    persona_engine=getattr(persona_service, "engine", None),
                    llm_gateway=self.components.get("llm"),
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self.components.get("memory"),
                    character_card=character_card,
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
