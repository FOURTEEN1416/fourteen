"""优化版对话编排器 — 核心对话处理与组件编排"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator
from typing import Any
from pathlib import Path

logger = logging.getLogger("orchestrator.optimized")
project_root = Path(__file__).resolve().parent.parent

from orchestrator.voice_detector import detect_voice_request as _detect_voice_request
from orchestrator.session_locks import SessionLockManager
from observability.config_manager import ConfigManager
from observability.health import health_checker
from security.content_safety import ContentSafetyFilter
from security.pii_anonymizer import PIIAnonymizer
from security.prompt_injection import PromptInjectionDetector
from llm_provider import get_llm
from my_character.emotion_engine import EmotionEngine
from my_character.character_config import ConfigLoader
from utils.health_check import _is_healthy
from my_character.tone_mimic import ToneMimic
from shisi.application.persona_service import PersonaService
from shisi.application.memory_service import ShisiMemoryService
from shisi.application.knowledge_service import ShisiKnowledgeAdapter
from context.world_info_provider import WorldInfoProvider
from utils.character_helpers import normalize_character_card
from tools.base_tool import ToolDispatcher, ToolRegistry, ToolResult
from tools.builtin.calendar_tool import CalculatorTool, CalendarTool
from tools.builtin.character_crawler_tool import CharacterCrawlerTool
from tools.builtin.extra_tools import ImageGenTool, MemoryTool, SchedulerTool, WebSummaryTool
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool
from tools.builtin.search_tool import SearchTool
from tools.builtin.time_awareness_tool import TimeAwarenessTool
from tools.builtin.weather_tool import WeatherTool

class OptimizedOrchestrator:
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

    async def _run_tools_if_needed(
        self,
        llm: Any,
        query: str,
        system_prompt: str,
        history: list | None,
        affinity_level: int = 0,
    ) -> str:
        """如果系统启用了工具，先让 LLM 判断是否需要调用工具，并返回工具结果摘要。

        返回空字符串表示无需工具调用或调用失败；否则返回一段可追加到 system prompt
        的工具结果文本。
        """
        tools = self.components.get("tools")
        if not tools or not tools.registry:
            return ""
        schemas = tools.registry.get_tools_by_permission(affinity_level)
        if not schemas:
            return ""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            tool_resp = await loop.run_in_executor(
                None,
                lambda: llm.chat_with_tools(
                    query=query,
                    system_prompt=system_prompt,
                    history=history or [],
                    tools=schemas,
                    temperature=0.85,
                    max_tokens=2048,
                ),
            )
            tool_calls = tool_resp.get("tool_calls") if tool_resp else None
            if not tool_calls:
                return ""
        except Exception as e:
            logger.debug("工具意图识别失败: %s", e)
            return ""

        results: list[dict[str, Any]] = []
        for tc in tool_calls:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            args_raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except Exception:
                args = {}
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda _n=name, _a=args: tools.dispatch(_n, _a, affinity_level=affinity_level),
                )
            except Exception as e:
                logger.debug("工具 %s 执行异常: %s", name, e)
                result = ToolResult(False, error="tool_execution_failed")
            results.append({"name": name, "result": result.to_dict()})

        if not results:
            return ""
        summary = "\n".join(
            f"[{r['name']}] {json.dumps(r['result'], ensure_ascii=False)}"
            for r in results
        )
        return f"\n\n[工具调用结果]\n{summary}\n请根据以上结果自然地回复用户。"

    def initialize(self, config_dir: str = "config",
                   fusion_cfg: dict | None = None) -> bool:
        if self._initialized:
            return True

        fusion_cfg = fusion_cfg or {}
        logger.info("[初始化] 启动并行组件初始化 (fast 模式)...")
        start_time = time.perf_counter()

        try:
            self.components["config"] = ConfigManager(config_dir=config_dir)
            cfg = self.components["config"].config

            from observability.tracing import tracer
            self.components["tracer"] = tracer
            self.components["health"] = health_checker

            self.components["safety"] = ContentSafetyFilter(
                enabled=cfg.safety.input_filter_enabled
            )
            self.components["pii"] = PIIAnonymizer(
                enabled=cfg.safety.pii_anonymizer_enabled
            )
            self.components["injection"] = PromptInjectionDetector(
                enabled=cfg.safety.prompt_injection_detection
            )

            self.components["llm"] = get_llm(
                provider=cfg.llm.provider,
                models_config=cfg.llm.models_priority,
            )

            self.components["safety"].llm_gateway = self.components["llm"]
            self.components["injection"].llm_gateway = self.components["llm"]

            emotion_fusion = fusion_cfg.get("emotion", {})
            blend_ratio = emotion_fusion.get("blend_ratio", cfg.emotion.continuity_blend_ratio)
            classifier_timeout_ms = emotion_fusion.get("classifier_timeout_ms",
                                                       cfg.emotion.llm_classifier_timeout_ms)

            self.components["emotion"] = EmotionEngine(
                llm_gateway=self.components["llm"],
                use_llm=cfg.emotion.use_llm_classifier,
                blend_ratio=blend_ratio,
                classifier_timeout_ms=classifier_timeout_ms,
            )
            from my_character.character_config import ConfigLoader
            config_loader = ConfigLoader(config_dir=config_dir)
            self.components["persona"] = PersonaService(
                config_loader=config_loader,
                llm_gateway=self.components["llm"],
                emotion_engine=self.components["emotion"],
            )
            self.components["tone"] = ToneMimic(
                chroma_path=str(project_root / "data" / "chroma_db")
            )

            memory_fusion = fusion_cfg.get("memory", {})
            # Shisi 适配层是唯一对外接口；其内部按需懒加载 VectorMemory / StructuredMemory。
            self.components["memory"] = ShisiMemoryService(
                chroma_path=str(project_root / "data" / "chroma_db"),
                db_path=str(project_root / "data" / "sqlite.db"),
                llm_gateway=self.components["llm"],
                working_limit=cfg.memory.working_memory_limit,
                retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
                forgetting_model=memory_fusion.get("forgetting_model", "exponential"),
            )
            logger.info("使用 shisi 记忆服务适配层 (ShisiMemoryService)")
            self.components["vector_memory"] = self.components["memory"].vector_memory
            self.components["structured_memory"] = self.components["memory"].structured_memory

            ase_fusion = fusion_cfg.get("ase", {})
            try:
                from proactive.ase_engine import ASEEngine as ASEEngineOptimized
                self.components["ase"] = ASEEngineOptimized(
                    llm_gateway=self.components["llm"],
                    max_daily_messages=cfg.proactive.max_daily_messages,
                    min_interval_minutes=cfg.proactive.min_interval_minutes,
                    cooldown_after_reply=cfg.proactive.cooldown_after_reply_minutes,
                    urgency_threshold=cfg.proactive.urgency_threshold,
                    frequency_mode=ase_fusion.get("frequency_mode", "adaptive"),
                    generation_mode=ase_fusion.get("generation_mode", "llm"),
                    reflection_mode=ase_fusion.get("reflection_mode", "rule"),
                )
            except ImportError:
                from proactive.ase_engine import ASEEngine as ASEEngineV2
                self.components["ase"] = ASEEngineV2(
                    llm_gateway=self.components["llm"],
                    max_daily_messages=cfg.proactive.max_daily_messages,
                    min_interval_minutes=cfg.proactive.min_interval_minutes,
                    cooldown_after_reply=cfg.proactive.cooldown_after_reply_minutes,
                    urgency_threshold=cfg.proactive.urgency_threshold,
                )

            # ── 主动消息调度器（启用 apply_time_decay / ASE / 每日维护） ──
            try:
                from proactive.scheduler import ProactiveScheduler

                scheduler = ProactiveScheduler(
                    ase_engine=self.components["ase"],
                    send_message_func=lambda msg: logger.info("[主动消息] %s", msg),
                    emotion_engine=self.components["emotion"],
                )
                # 至少注册一个控制台通道作为兜底；后续可通过 register_channel 注入 ws/wechat
                scheduler.register_channel(
                    "console", lambda: lambda msg: logger.info("[主动消息/console] %s", msg)
                )
                if scheduler.start():
                    self.components["scheduler"] = scheduler
                    logger.info("主动消息调度器已启动")
                else:
                    logger.warning("主动消息调度器启动失败，时间衰减/ASE 将不可用")
                    self.components["scheduler"] = None
            except Exception as e:  # noqa: BLE001
                logger.warning("主动消息调度器初始化失败 (不影响运行): %s", e)
                self.components["scheduler"] = None

            registry = ToolRegistry()
            for tool_cls in [WeatherTool, SearchTool, CalendarTool, CalculatorTool]:
                registry.register(tool_cls())

            mem = self.components.get("memory")
            sm = getattr(mem, "structured_memory", None)
            if sm:
                registry.register(ReminderTool(sm))
                registry.register(CalendarQueryTool(sm))
                registry.register(MemoryTool(sm))
                registry.register(SchedulerTool(sm))

            registry.register(TimeAwarenessTool())
            registry.register(CharacterCrawlerTool())
            registry.register(WebSummaryTool())
            registry.register(ImageGenTool())

            self.components["tool_registry"] = registry
            self.components["tools"] = ToolDispatcher(
                registry,
                timeout=cfg.tools.execution_timeout_seconds,
                rate_limit_per_minute=cfg.tools.rate_limit_per_tool_per_minute,
            )

            rag_vm = self.components.get("vector_memory") or getattr(
                self.components["memory"], "vector_memory", None)
            rag_sm = self.components.get("structured_memory") or getattr(
                self.components["memory"], "structured_memory", None)
            rag_sem = getattr(self.components["memory"], "semantic", None)

            # Shisi 适配层是唯一对外接口；use_legacy_rag=True 时内部委托给 RAGEngineV2。
            self.components["rag"] = ShisiKnowledgeAdapter(
                vector_memory=rag_vm,
                structured_memory=rag_sm,
                semantic_memory=rag_sem,
                tone_mimic=self.components["tone"],

            )
            logger.info("使用 shisi knowledge 适配层 (ShisiKnowledgeAdapter)")

            # ── 世界信息动态注入 ──
            self.components["world_info"] = WorldInfoProvider(
                timezone_offset=cfg.system.timezone_offset_hours
                if hasattr(cfg, "system") and hasattr(cfg.system, "timezone_offset_hours")
                else 8
            )

            # ── 角色卡系统 (v3.0 新增) ──
            card_fusion = fusion_cfg.get("character_card", {})
            card_enabled = card_fusion.get("enabled", cfg.character_card.enabled)
            card_mode = card_fusion.get("mode", "merge")

            if card_enabled:
                try:
                    from character_card.integration import CharacterCardAdapter
                    char_dir = card_fusion.get("card_dir", cfg.character_card.card_dir) or "config/characters"
                    default_card = card_fusion.get("default_card", cfg.character_card.default_card) or ""

                    self.components["character_card"] = CharacterCardAdapter(
                        card_dir=str(project_root / char_dir),
                        default_card_path=str(project_root / default_card) if default_card else None,
                        enabled=True,
                    )
                    self.components["card_mode"] = card_mode
                    # 自动加载默认卡
                    if default_card and self.components["character_card"].load_default():
                        logger.info("默认角色卡已加载: %s", default_card)
                    else:
                        logger.info("未配置默认角色卡，跳过")
                except Exception as e:  # noqa: BLE001
                    logger.warning("角色卡系统初始化失败 (不影响运行): %s", e)
                    self.components["character_card"] = None
            else:
                logger.info("角色卡系统已禁用")

            # ── 语音TTS系统 (v3.0 新增) ──
            voice_fusion = fusion_cfg.get("voice", {})
            voice_enabled = voice_fusion.get("enabled", cfg.voice.enabled)

            if voice_enabled:
                try:
                    from voice import TTSManager
                    # 注入 EmotionVoiceMapper（领域层 → 基础层，避免反向依赖）
                    try:
                        from shisi.voice.emotion_tts import EmotionVoiceMapper
                        _emotion_mapper = EmotionVoiceMapper()
                    except Exception:  # noqa: BLE001
                        _emotion_mapper = None
                    # 将VoiceConfig对象转换为dict以兼容TTSManager.initialize()
                    voice_config = voice_fusion if voice_fusion else cfg.voice.model_dump(by_alias=True)
                    self.components["voice"] = TTSManager(emotion_mapper=_emotion_mapper)
                    self._run_async(self.components["voice"].initialize(
                        voice_config if isinstance(voice_config, dict) else voice_config
                    ))
                    if self.components["voice"].enabled:
                        logger.info("语音系统初始化完成: engine=%s",
                                     self.components["voice"].current_engine)
                    else:
                        self.components["voice"] = None
                        logger.warning("语音系统初始化失败")
                except Exception as e:  # noqa: BLE001
                    logger.warning("语音系统初始化失败 (不影响运行): %s", e)
                    self.components["voice"] = None
            else:
                self.components["voice"] = None
                logger.info("语音系统已禁用")

            # ── 长期记忆增强 (v3.0 新增) ──
            mem_ext_fusion = fusion_cfg.get("memory_ext", {})
            mem_ext_enabled = mem_ext_fusion.get("enabled", cfg.memory_ext.enabled)

            if mem_ext_enabled:
                try:
                    from memory_ext import MemoryEnhancer
                    coll_name = mem_ext_fusion.get("collection_name", cfg.memory_ext.collection_name) or "long_term_memories"
                    self.components["memory_ext"] = MemoryEnhancer(
                        chroma_path=str(project_root / "data" / "chroma_db"),
                        collection_name=coll_name,
                        llm_gateway=self.components["llm"],
                        enabled=True,
                    )
                    self._run_async(self.components["memory_ext"].initialize())
                    logger.info("长期记忆增强已初始化: collection=%s", coll_name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("长期记忆增强初始化失败 (不影响运行): %s", e)
                    self.components["memory_ext"] = None
            else:
                self.components["memory_ext"] = None
                logger.info("长期记忆增强已禁用")

            # ── PersonaExtractor人格克隆 (v3.1 新增) ──
            persona_fusion = fusion_cfg.get("persona_extractor", {})
            persona_ext_enabled = persona_fusion.get("enabled", False)

            if persona_ext_enabled:
                try:
                    from persona_extractor import PersonaExtractor

                    pe_mode = persona_fusion.get("mode", "lite")
                    pe_freq = persona_fusion.get("detect_frequency", 3)
                    pe_inject = persona_fusion.get("inject_persona", True)
                    pe_mh = persona_fusion.get("enable_mental_health", True)

                    self.components["persona_extractor"] = PersonaExtractor(
                        llm_gateway=self.components["llm"],
                        db_path=str(project_root / "data" / "sqlite.db"),
                        pado_mode=pe_mode,
                        detect_frequency=pe_freq,
                        inject_persona=pe_inject,
                        enable_mental_health=pe_mh,
                    )
                    # 注入 ToneMimic 引用
                    if "tone" in self.components:
                        self.components["persona_extractor"].set_tone_mimic(
                            self.components["tone"]
                        )
                    # 异步初始化
                    self._run_async(
                        self.components["persona_extractor"].initialize()
                    )
                    logger.info("PersonaExtractor已初始化: mode=%s, freq=%d",
                                pe_mode, pe_freq)
                except Exception as e:  # noqa: BLE001
                    logger.warning("PersonaExtractor初始化失败 (不影响运行): %s", e)
                    self.components["persona_extractor"] = None
            else:
                self.components["persona_extractor"] = None
                logger.info("PersonaExtractor已禁用")

            # ── 知识宝库系统 (v4.0 新增) ──
            vault_fusion = fusion_cfg.get("vault", {})
            vault_enabled = vault_fusion.get("enabled", True)
            if vault_enabled:
                try:
                    from shisi.vault import VaultCollector
                    self.components["vault_collector"] = VaultCollector()
                    logger.info("知识宝库已初始化")
                except Exception as e:  # noqa: BLE001
                    logger.warning("知识宝库初始化失败 (不影响运行): %s", e)
                    self.components["vault_collector"] = None
            else:
                self.components["vault_collector"] = None
                logger.info("知识宝库已禁用")

            self._initialized = True
            init_time = time.perf_counter() - start_time
            logger.info("[初始化] 完成, 耗时 %.2fs", init_time)
            return True

        except Exception as e:  # noqa: BLE001
            logger.error("[初始化] 失败: %s", e)
            return False

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

    async def _prepare_context(
        self,
        user_msg_clean: str,
        session_id: str,
        character_id: str,
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
        # PersonaExtractor user_id（多用户隔离）
        pe = self.components.get("persona_extractor")
        if pe is not None:
            effective_user_id = (
                f"{character_id}:{session_id}" if session_id else f"{character_id}"
            )
            if pe.user_id != effective_user_id:
                pe.set_user_id(effective_user_id)

        # 并行执行独立任务
        recent = self.components["memory"].get_recent_context(3)
        loop = asyncio.get_running_loop()

        tasks: dict[str, Any] = {}
        if pe is not None:
            tasks["persona"] = pe.process_message(
                message=user_msg_clean, context=recent,
            )
        tasks["emotion"] = loop.run_in_executor(
            None, self.components["emotion"].analyze,
            user_msg_clean, recent,
        )
        tasks["memory"] = loop.run_in_executor(
            None,
            lambda: self.components["memory"].retrieve_context(
                query=user_msg_clean, session_id=session_id, top_k=5,
            ),
        )
        rag = self.components["rag"]
        if hasattr(rag, "set_character_id"):
            rag.set_character_id(character_id)
        tasks["rag"] = loop.run_in_executor(
            None, rag.retrieve, user_msg_clean,
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
                ctx = await self._prepare_context(user_msg_clean, session_id, character_id)
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
                reply = await check_and_correct_reply(
                    reply=reply,
                persona_engine=getattr(
                    self.components.get("persona"), "engine", None
                ),
                llm_gateway=self.components.get("llm"),
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self.components.get("memory"),
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

    async def process_message_stream(
        self,
        user_msg: str,
        session_id: str = "",
        message_type: str = "text",
        character_id: str = "default",
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
                    user_msg, session_id, message_type, character_id,
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
                ctx = await self._prepare_context(user_msg_clean, session_id, character_id)
                emotion_state = ctx["emotion_state"]
                system_prompt = ctx["system_prompt"]
                chat_history = ctx["chat_history"]

                # 9. 真流式 LLM 调用 — 边生成边 yield
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
                            yield {"type": "token", "content": token}
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

                # 10. 流式后处理（不修改已 yield 的内容）
                # 注意：一致性检查和输出安全检查需要完整回复且可能修改内容，
                # 在流式模式下跳过（依赖输入安全 + system prompt 约束输出质量）
                reply = full_reply

                # ── 共享后处理（after_chat → ASE → 好感度同步）──
                self._after_process(
                    user_msg_clean, reply, emotion_state, session_id, character_id,
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

