"""OptimizedOrchestrator 初始化阶段 — Mixin。

将原 ``initialize`` 方法（327 行）按自然注释块边界拆分为 9 个 _init_* 阶段，
便于阅读、定位与局部失败排查。所有阶段通过 ``self.components`` 共享状态，
与原实现语义完全一致；不引入新的抽象层或兼容垫片。

调用顺序由 ``initialize`` 编排，外部不应直接调用本 Mixin 的 _init_* 方法。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from context.world_info_provider import WorldInfoProvider
from llm_provider import get_llm
from my_character.emotion_engine import EmotionEngine
from my_character.tone_mimic import ToneMimic
from observability.config_manager import ConfigManager
from observability.health import health_checker
from security.content_safety import ContentSafetyFilter
from security.encryption import EncryptionManager
from security.pii_anonymizer import PIIAnonymizer
from security.prompt_injection import PromptInjectionDetector
from shisi.application.knowledge_service import ShisiKnowledgeAdapter
from shisi.application.memory_service import ShisiMemoryService
from shisi.application.persona_service import PersonaService
from tools.base_tool import ToolDispatcher, ToolRegistry
from tools.builtin.calendar_tool import CalculatorTool, CalendarTool
from tools.builtin.character_crawler_tool import CharacterCrawlerTool
from tools.builtin.extra_tools import ImageGenTool, MemoryTool, SchedulerTool, WebSummaryTool
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool
from tools.builtin.search_tool import SearchTool
from tools.builtin.time_awareness_tool import TimeAwarenessTool
from tools.builtin.weather_tool import WeatherTool

logger = logging.getLogger("orchestrator.optimized")

# 复用主模块的 project_root，避免路径计算分叉
_project_root = Path(__file__).resolve().parent.parent


class _InitPhasesMixin:
    """``OptimizedOrchestrator.initialize`` 的阶段化拆分。

    依赖主类在 ``__init__`` 中初始化的 ``self.components`` / ``self._initialized``
    以及 ``self._run_async`` 静态方法。不持有自身状态。
    """

    # NOTE: 类型注解使用 Any 避免与主类循环依赖；运行期为 OptimizedOrchestrator 实例。
    components: dict[str, Any]
    _initialized: bool
    _run_async: Any  # 静态方法，由主类提供

    def initialize(self, config_dir: str = "config",
                   fusion_cfg: dict | None = None) -> bool:
        if self._initialized:
            return True

        fusion_cfg = fusion_cfg or {}
        logger.info("[初始化] 启动并行组件初始化 (fast 模式)...")
        start_time = time.perf_counter()

        try:
            self._init_core(config_dir)
            cfg = self.components["config"].config

            self._init_emotion_persona_tone(cfg, fusion_cfg, config_dir)
            self._init_memory_and_rag(cfg, fusion_cfg)
            self._init_world_info(cfg)
            self._init_character_card(cfg, fusion_cfg)
            self._init_voice(cfg, fusion_cfg)
            self._init_memory_ext(cfg, fusion_cfg)
            self._init_persona_extractor(fusion_cfg)
            self._init_vault(fusion_cfg)
            self._init_multimodal(fusion_cfg)

            self._initialized = True
            init_time = time.perf_counter() - start_time
            logger.info("[初始化] 完成, 耗时 %.2fs", init_time)
            return True

        except Exception as e:  # noqa: BLE001
            logger.error("[初始化] 失败: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────
    #  阶段 1: Config / 可观测性 / 安全 / LLM
    # ─────────────────────────────────────────────────────────────
    def _init_core(self, config_dir: str) -> None:
        self.components["config"] = ConfigManager(config_dir=config_dir)

        from observability.tracing import tracer
        self.components["tracer"] = tracer
        self.components["health"] = health_checker

        cfg = self.components["config"].config
        self.components["safety"] = ContentSafetyFilter(
            enabled=cfg.safety.input_filter_enabled
        )
        self.components["pii"] = PIIAnonymizer(
            enabled=cfg.safety.pii_anonymizer_enabled
        )
        # EncryptionManager：full 模式历史能力，统一收纳到 _init_core 避免重复初始化
        self.components["encryption"] = EncryptionManager(
            key_env=cfg.safety.encryption_key_env,
            enabled=cfg.safety.encryption_enabled,
        )
        self.components["injection"] = PromptInjectionDetector(
            enabled=cfg.safety.prompt_injection_detection
        )

        self.components["llm"] = get_llm(
            provider=cfg.llm.provider,
            models_config=cfg.llm.models_priority,
            config=cfg.llm,
        )

        self.components["safety"].llm_gateway = self.components["llm"]
        self.components["injection"].llm_gateway = self.components["llm"]

    # ─────────────────────────────────────────────────────────────
    #  阶段 2: Emotion / Persona / Tone
    # ─────────────────────────────────────────────────────────────
    def _init_emotion_persona_tone(self, cfg: Any, fusion_cfg: dict, config_dir: str) -> None:
        emotion_fusion = fusion_cfg.get("emotion", {})
        blend_ratio = emotion_fusion.get("blend_ratio", cfg.emotion.continuity_blend_ratio)
        classifier_timeout_ms = emotion_fusion.get(
            "classifier_timeout_ms", cfg.emotion.llm_classifier_timeout_ms
        )
        classifier_mode = emotion_fusion.get("classifier_mode", "hybrid")

        self.components["emotion"] = EmotionEngine(
            llm_gateway=self.components["llm"],
            use_llm=cfg.emotion.use_llm_classifier,
            blend_ratio=blend_ratio,
            classifier_timeout_ms=classifier_timeout_ms,
            classifier_mode=classifier_mode,
        )

        from my_character.character_config import ConfigLoader
        config_loader = ConfigLoader(config_dir=config_dir)
        persona_fusion = fusion_cfg.get("persona", {})
        prompt_mode = persona_fusion.get("prompt_mode", "layered")
        anchor_verification = persona_fusion.get("anchor_verification_enabled", True)
        self.components["persona"] = PersonaService(
            config_loader=config_loader,
            llm_gateway=self.components["llm"],
            emotion_engine=self.components["emotion"],
            prompt_mode=prompt_mode,
            anchor_verification_enabled=anchor_verification,
        )
        self.components["tone"] = ToneMimic(
            chroma_path=str(_project_root / "data" / "chroma_db")
        )

    # ─────────────────────────────────────────────────────────────
    #  阶段 3: Memory / ASE / Scheduler / Tools / RAG
    # ─────────────────────────────────────────────────────────────
    def _init_memory_and_rag(self, cfg: Any, fusion_cfg: dict) -> None:
        memory_fusion = fusion_cfg.get("memory", {})
        # Shisi 适配层是唯一对外接口；其内部按需懒加载 VectorMemory / StructuredMemory。
        self.components["memory"] = ShisiMemoryService(
            chroma_path=str(_project_root / "data" / "chroma_db"),
            db_path=str(_project_root / "data" / "sqlite.db"),
            llm_gateway=self.components["llm"],
            working_limit=cfg.memory.working_memory_limit,
            retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
            forgetting_model=memory_fusion.get("forgetting_model", "exponential"),
        )
        logger.info("使用 shisi 记忆服务适配层 (ShisiMemoryService)")
        self.components["vector_memory"] = self.components["memory"].vector_memory
        self.components["structured_memory"] = self.components["memory"].structured_memory

        self._init_ase_and_scheduler(cfg, fusion_cfg)
        self._init_tools(cfg)
        self._init_rag()

    def _init_ase_and_scheduler(self, cfg: Any, fusion_cfg: dict) -> None:
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

    def _init_tools(self, cfg: Any) -> None:
        registry = ToolRegistry()
        if cfg.tools.enabled:
            enabled_tools = set(cfg.tools.builtin_tools)
            simple_tools = {
                "weather": WeatherTool,
                "search": SearchTool,
                "calendar": CalendarTool,
                "calculator": CalculatorTool,
                "time_awareness": TimeAwarenessTool,
                "character_card": CharacterCrawlerTool,
                "web_summary": WebSummaryTool,
                "image_gen": ImageGenTool,
            }
            for name, tool_cls in simple_tools.items():
                if name in enabled_tools:
                    registry.register(tool_cls())

            mem = self.components.get("memory")
            sm = getattr(mem, "structured_memory", None)
            if sm:
                # 字典键必须与 system.yaml builtin_tools 中的名称一致，
                # 否则 `if name in enabled_tools` 判断会失败，工具永不注册。
                memory_tools = {
                    "set_reminder": ReminderTool,
                    "query_reminders": CalendarQueryTool,
                    "memory": MemoryTool,
                    "scheduler": SchedulerTool,
                }
                for name, tool_cls in memory_tools.items():
                    if name in enabled_tools:
                        registry.register(tool_cls(sm))

        self.components["tool_registry"] = registry
        self.components["tools"] = (
            ToolDispatcher(
                registry,
                timeout=cfg.tools.execution_timeout_seconds,
                rate_limit_per_minute=cfg.tools.rate_limit_per_tool_per_minute,
            )
            if cfg.tools.enabled else None
        )

    def _init_rag(self) -> None:
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

    # ─────────────────────────────────────────────────────────────
    #  阶段 4: 世界信息
    # ─────────────────────────────────────────────────────────────
    def _init_world_info(self, cfg: Any) -> None:
        self.components["world_info"] = WorldInfoProvider(
            timezone_offset=cfg.system.timezone_offset_hours
            if hasattr(cfg, "system") and hasattr(cfg.system, "timezone_offset_hours")
            else 8
        )

    # ─────────────────────────────────────────────────────────────
    #  阶段 5: 角色卡系统 (v3.0)
    # ─────────────────────────────────────────────────────────────
    def _init_character_card(self, cfg: Any, fusion_cfg: dict) -> None:
        card_fusion = fusion_cfg.get("character_card", {})
        card_enabled = card_fusion.get("enabled", cfg.character_card.enabled)
        card_mode = card_fusion.get("mode", "merge")

        if not card_enabled:
            logger.info("角色卡系统已禁用")
            return

        try:
            from character_card.integration import CharacterCardAdapter
            char_dir = card_fusion.get("card_dir", cfg.character_card.card_dir) or "config/characters"
            default_card = card_fusion.get("default_card", cfg.character_card.default_card) or ""

            self.components["character_card"] = CharacterCardAdapter(
                card_dir=str(_project_root / char_dir),
                default_card_path=str(_project_root / default_card) if default_card else None,
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

    # ─────────────────────────────────────────────────────────────
    #  阶段 6: 语音 TTS (v3.0)
    # ─────────────────────────────────────────────────────────────
    def _init_voice(self, cfg: Any, fusion_cfg: dict) -> None:
        voice_fusion = fusion_cfg.get("voice", {})
        voice_enabled = voice_fusion.get("enabled", cfg.voice.enabled)

        if not voice_enabled:
            self.components["voice"] = None
            logger.info("语音系统已禁用")
            return

        try:
            from voice import TTSManager
            # 将 VoiceConfig 对象转换为 dict 以兼容 TTSManager.initialize()
            voice_config = voice_fusion if voice_fusion else cfg.voice.model_dump(by_alias=True)
            # 2026-08-28 MiMo-only 收敛：情感映射由 MiMoTTSProvider 内部处理，不再注入 EmotionVoiceMapper
            self.components["voice"] = TTSManager()
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

    # ─────────────────────────────────────────────────────────────
    #  阶段 7: 长期记忆增强 (v3.0)
    # ─────────────────────────────────────────────────────────────
    def _init_memory_ext(self, cfg: Any, fusion_cfg: dict) -> None:
        mem_ext_fusion = fusion_cfg.get("memory_ext", {})
        mem_ext_enabled = mem_ext_fusion.get("enabled", cfg.memory_ext.enabled)

        if not mem_ext_enabled:
            self.components["memory_ext"] = None
            logger.info("长期记忆增强已禁用")
            return

        try:
            from memory_ext import MemoryEnhancer
            coll_name = mem_ext_fusion.get(
                "collection_name", cfg.memory_ext.collection_name
            ) or "long_term_memories"
            self.components["memory_ext"] = MemoryEnhancer(
                chroma_path=str(_project_root / "data" / "chroma_db"),
                collection_name=coll_name,
                llm_gateway=self.components["llm"],
                enabled=True,
            )
            self._run_async(self.components["memory_ext"].initialize())
            logger.info("长期记忆增强已初始化: collection=%s", coll_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("长期记忆增强初始化失败 (不影响运行): %s", e)
            self.components["memory_ext"] = None

    # ─────────────────────────────────────────────────────────────
    #  阶段 8: PersonaExtractor 人格克隆 (v3.1)
    # ─────────────────────────────────────────────────────────────
    def _init_persona_extractor(self, fusion_cfg: dict) -> None:
        persona_fusion = fusion_cfg.get("persona_extractor", {})
        persona_ext_enabled = persona_fusion.get("enabled", False)

        if not persona_ext_enabled:
            self.components["persona_extractor"] = None
            logger.info("PersonaExtractor已禁用")
            return

        try:
            from persona_extractor import PersonaExtractor

            pe_mode = persona_fusion.get("mode", "lite")
            pe_freq = persona_fusion.get("detect_frequency", 3)
            pe_inject = persona_fusion.get("inject_persona", True)
            pe_mh = persona_fusion.get("enable_mental_health", True)

            self.components["persona_extractor"] = PersonaExtractor(
                llm_gateway=self.components["llm"],
                db_path=str(_project_root / "data" / "sqlite.db"),
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

    # ─────────────────────────────────────────────────────────────
    #  阶段 9: 知识宝库 (v4.0)
    # ─────────────────────────────────────────────────────────────
    def _init_vault(self, fusion_cfg: dict) -> None:
        vault_fusion = fusion_cfg.get("vault", {})
        vault_enabled = vault_fusion.get("enabled", True)
        if not vault_enabled:
            self.components["vault_collector"] = None
            logger.info("知识宝库已禁用")
            return

        try:
            from shisi.vault import VaultCollector
            self.components["vault_collector"] = VaultCollector()
            logger.info("知识宝库已初始化")
        except Exception as e:  # noqa: BLE001
            logger.warning("知识宝库初始化失败 (不影响运行): %s", e)
            self.components["vault_collector"] = None

    # ─────────────────────────────────────────────────────────────
    #  阶段 10: 多模态处理 (v3.0)
    # ─────────────────────────────────────────────────────────────
    def _init_multimodal(self, fusion_cfg: dict) -> None:
        """多模态处理器：图片/语音/视频理解。

        历史上仅在 `_run_full_mode` 中手动创建，统一收纳到 _init_mixin 后，
        fast 与 full 模式均自动获得多模态能力。失败时降级为 None，不影响主流程。
        """
        mm_fusion = fusion_cfg.get("multimodal", {})
        mm_enabled = mm_fusion.get("enabled", True)
        if not mm_enabled:
            self.components["multimodal"] = None
            logger.info("多模态处理已禁用")
            return

        try:
            from multimodal.multimodal_processor import MultimodalProcessor
            self.components["multimodal"] = MultimodalProcessor(
                llm_gateway=self.components["llm"],
            )
            logger.info("多模态处理器已初始化")
        except Exception as e:  # noqa: BLE001
            logger.warning("多模态处理器初始化失败 (不影响运行): %s", e)
            self.components["multimodal"] = None
