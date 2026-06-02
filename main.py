#!/usr/bin/env python3
"""
"唯一的你" — AI 虚拟伴侣融合统一版主入口

融合 V1 + V2 + Optimized 三版优势：
- V1: 微信直连通道 + 自动重连
- V2: 可观测性(链路追踪+指标+健康检查) + 安全层(加密) + 工具系统 + RAG + API
- Optimized: 并行初始化编排器 + 结构化日志 + --log-level
- 双编排器模式: "full"(V2完整流程) / "fast"(Optimized简洁流程)
- Fusion 配置节: config/system.yaml → fusion

启动方式：
    python main.py                           # 完整启动 (orchestrator_mode=full)
    python main.py --console                 # 控制台聊天模式 (不启动微信)
    python main.py --no-api                  # 不启动REST/WebSocket API
    python main.py --no-scheduler            # 不启动主动消息调度器
    python main.py --log-level DEBUG         # 调试日志
    python main.py --clone wxid_xxx          # 风格克隆
    python main.py --init-only               # 仅初始化
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
import os
import re
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# ── 语音触发检测 ───────────────────────────────────────

# 直接命令词（高置信度）
_VOICE_COMMANDS = [
    "发语音", "语音回复", "发段语音", "发个语音",
    "语音消息", "语音说", "用语音", "用说的",
    "声音回复", "语音告诉我",
]

# 欲望/请求模式
_VOICE_DESIRE_PATTERNS = [
    r"想听你说[句话]?",
    r"用(你的?)?声音",
    r"听(到|见)你的声音",
    r"能不能?发(个|段)?语音",
    r"可以发语音",
    r"说话.*听听?",
    r"(给|帮)我(说|讲|读)",
]

# 情感修饰 + 说
_VOICE_EMOTION_SPEAK = [
    r"(温柔|轻声|小声|大声|悄悄|慢慢|好好)地说",
    r"(温柔|轻声|小声|大声|悄悄|慢慢|好好)说",
]

# 能力询问
_VOICE_CAPABILITY = [
    r"(能|会|可以)说话[吗么]?",
    r"(能|会)发声[吗么]?",
    r"有语音功能",
]

# 上下文触发
_VOICE_CONTEXT = [
    r"说句话",
    r"出[个声]?声",
    r"发声",
    r"说话",
    r"语音",
]


def _detect_voice_request(text: str) -> bool:
    """检测用户消息是否要求语音回复"""
    if not text:
        return False
    text = text.strip()

    # 排除否定
    if re.search(r"(不要|别|不想|不用|懒得|算了).{0,5}(语音|说话|声音|发声)", text):
        return False
    if re.search(r"文字(就|才|更)好|打字", text):
        return False

    # Tier 1: 直接命令词
    for cmd in _VOICE_COMMANDS:
        if cmd in text:
            return True

    # Tier 2: 欲望/请求模式
    for pat in _VOICE_DESIRE_PATTERNS:
        if re.search(pat, text):
            return True

    # Tier 3: 情感修饰 + 说
    for pat in _VOICE_EMOTION_SPEAK:
        if re.search(pat, text):
            return True

    # Tier 4: 能力询问
    for pat in _VOICE_CAPABILITY:
        if re.search(pat, text):
            return True

    # Tier 5: 上下文触发（排除误触发）
    for pat in _VOICE_CONTEXT:
        m = re.search(pat, text)
        if m:
            surrounding = text[max(0, m.start() - 2):m.end() + 2]
            if any(x in surrounding for x in ["识别", "输入", "转文字", "普通", "导航", "搜索"]):
                continue
            if "说句话" in text and ("听听" in text or "吗" in text or "吧" in text):
                return True
            if pat == "说话" and len(text) < 8:
                return True
            if pat == "语音" and len(text) < 10 and "吗" in text:
                return True
            return True

    return False


project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

import contextlib  # noqa: E402

# ── 集中管理重复出现的函数级导入（合并重复 import） ──
from api.app_factory import create_api_app  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from api.websocket_server import WebSocketServer  # noqa: E402
from girlfriend_manager import GirlfriendManager  # noqa: E402
from llm_provider import get_llm  # noqa: E402
from memory import StructuredMemory, VectorMemory  # noqa: E402
from memory.memory_pipeline import MemoryPipeline  # noqa: E402
from my_character.emotion_engine import EmotionEngine  # noqa: E402
from my_character.persona_engine import PersonaEngine  # noqa: E402
from my_character.tone_mimic import ToneMimic  # noqa: E402
from observability.config_manager import ConfigManager  # noqa: E402
from observability.graceful_shutdown import graceful_shutdown  # noqa: E402
from observability.health import health_checker  # noqa: E402
from observability.logging_setup import setup_logging  # noqa: E402
from proactive.ase_engine import ASEEngine  # noqa: E402
from rag_engine.rag_engine import RAGEngineV2  # noqa: E402
from security.content_safety import ContentSafetyFilter  # noqa: E402
from security.encryption import EncryptionManager  # noqa: E402
from security.pii_anonymizer import PIIAnonymizer  # noqa: E402
from security.prompt_injection import PromptInjectionDetector  # noqa: E402
from tools.base_tool import ToolDispatcher, ToolRegistry  # noqa: E402
from tools.builtin.calendar_tool import CalculatorTool, CalendarTool  # noqa: E402
from tools.builtin.character_crawler_tool import CharacterCrawlerTool  # noqa: E402
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool  # noqa: E402
from tools.builtin.search_tool import SearchTool  # noqa: E402
from tools.builtin.time_awareness_tool import TimeAwarenessTool  # noqa: E402
from tools.builtin.weather_tool import WeatherTool  # noqa: E402
from utils.health_check import health_check_all, _is_healthy  # noqa: E402

# ── 加载 .env（手动解析，无需 python-dotenv 依赖） ──
_env_loaded = False
def _load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    env_path = project_root / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip("\"'")
                # 只在环境变量未设置时写入，不覆盖系统已设值
                if key not in os.environ:
                    os.environ[key] = val
    _env_loaded = True

_load_env()


def _setup_basic_logging(log_level: str = "INFO") -> logging.Logger:
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                str(project_root / "data" / "app.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )
    return logging.getLogger("main")


logger = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="唯一的你 - AI 虚拟伴侣 (融合统一版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                           # 完整启动
  %(prog)s --console                 # 控制台模式 (不启动微信)
  %(prog)s --no-api                  # 不启动API服务
  %(prog)s --no-scheduler            # 不启动主动消息调度器
  %(prog)s --log-level DEBUG         # 调试日志
  %(prog)s --clone wxid_xxx --clone-name "小明"  # 风格克隆
  %(prog)s --init-only               # 仅初始化
        """,
    )
    parser.add_argument("--console", action="store_true",
                        help="控制台聊天模式 (不启动微信, V1 --no-wechat 映射)")
    parser.add_argument("--no-wechat", action="store_true",
                        help="同 --console (兼容V1/V2)")
    parser.add_argument("--no-api", action="store_true",
                        help="不启动REST/WebSocket API")
    parser.add_argument("--no-scheduler", action="store_true",
                        help="不启动主动消息调度器")
    parser.add_argument("--config", type=str, default="config",
                        help="配置文件目录 (默认: config)")
    parser.add_argument("--init-only", action="store_true",
                        help="仅初始化, 用于测试")
    parser.add_argument("--clone", type=str, default=None,
                        help="克隆目标 (wxid/文件路径)")
    parser.add_argument("--clone-source", type=str, default="wcf",
                        choices=["wcf", "wechatmsg", "decrypt", "txt", "csv", "json"],
                        help="克隆数据来源 (decrypt=微信4.x数据库解密)")
    parser.add_argument("--clone-name", type=str, default="",
                        help="被克隆者名称")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别")
    return parser.parse_args()


def print_banner() -> None:
    banner = """
    ==================================================
             唯一的你 -- AI 虚拟伴侣
    ==================================================
        情感 . 记忆 . 主动交互 . 风格克隆
               融合统一版 v3.0
    ==================================================
    """
    try:
        print(banner)
    except UnicodeEncodeError:
        safe = banner.replace('\u2500', '-').replace('\u2502', '|').replace('\u250c', '+').replace('\u2510', '+').replace('\u2514', '+').replace('\u2518', '+')
        print(safe)


def load_fusion_config(config_dir: str) -> dict[str, Any]:
    import yaml
    system_yaml = project_root / config_dir / "system.yaml"
    if system_yaml.exists():
        with open(system_yaml, encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
        return full_cfg.get("fusion", {})  # type: ignore[no-any-return]
    logger.warning("未找到 %s, 使用默认 fusion 配置", system_yaml)
    return {}


class OptimizedOrchestrator:
    """
    优化版对话编排器 (fast 模式)

    简洁流程: 安全→PII脱敏→注入检测→情感→记忆→RAG→LLM→输出安全→存储→ASE
    """

    # Session锁缓存配置：最大缓存数、锁过期时间（秒）
    _MAX_SESSION_LOCKS = 1000
    _SESSION_LOCK_TTL_SECONDS = 3600  # 1小时无使用后清理

    def __init__(self):
        self.components: dict[str, Any] = {}
        self._initialized = False
        # per-session 异步锁，使用带TTL的缓存防止内存无限增长
        self._session_locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._session_lock_access_time: dict[str, float] = {}
        self._locks_mutex = threading.Lock()
        self._lock_cleanup_counter: int = 0  # 替代 hash() 的概率触发
        self._executor = None  # 延迟初始化的共享线程池

    def _get_executor(self):
        if self._executor is None:
            import concurrent.futures
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="opt_init"
            )
        return self._executor

    def shutdown(self):
        """关闭 OptimizedOrchestrator 并释放资源。

        注意: 必须调用此方法以确保 ThreadPoolExecutor 正确关闭，
        避免程序退出时线程池资源泄漏。
        """
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

    @staticmethod
    def _run_async(coro) -> Any:
        """安全运行协程，支持有/无事件循环两种情况。

        代理到 common.async_utils.run_async，保持向后兼容。
        """
        from utils.async_utils import run_async
        return run_async(coro)

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
                models_config=cfg.llm.models_priority
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
            self.components["persona"] = PersonaEngine(
                config_loader=config_loader,
                llm_gateway=self.components["llm"],
                emotion_engine=self.components["emotion"],
            )
            self.components["tone"] = ToneMimic(
                chroma_path=str(project_root / "data" / "chroma_db")
            )

            vector_memory = VectorMemory(chroma_path=str(project_root / "data" / "chroma_db"))
            structured_memory = StructuredMemory(db_path=str(project_root / "data" / "sqlite.db"))
            self.components["memory"] = MemoryPipeline(
                structured_memory=structured_memory,
                vector_memory=vector_memory,
                llm_gateway=self.components["llm"],
                working_limit=cfg.memory.working_memory_limit,
                retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
            )
            self.components["vector_memory"] = vector_memory
            self.components["structured_memory"] = structured_memory

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

            registry = ToolRegistry()
            for tool_cls in [WeatherTool, SearchTool, CalendarTool, CalculatorTool]:
                registry.register(tool_cls())

            mem = self.components.get("memory")
            sm = getattr(mem, "structured_memory", None)
            if sm:
                registry.register(ReminderTool(sm))
                registry.register(CalendarQueryTool(sm))

            registry.register(TimeAwarenessTool())
            registry.register(CharacterCrawlerTool())

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

            self.components["rag"] = RAGEngineV2(
                vector_memory=rag_vm,
                structured_memory=rag_sm,
                semantic_memory=rag_sem,
                tone_mimic=self.components["tone"],
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
                    # 将VoiceConfig对象转换为dict以兼容TTSManager.initialize()
                    voice_config = voice_fusion if voice_fusion else cfg.voice.model_dump(by_alias=True)
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

    async def process_message(self, user_msg: str, session_id: str = "", message_type: str = "text") -> dict[str, Any]:
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

                # ── 并行执行独立任务 ──
                recent = self.components["memory"].get_recent_context(3)
                loop = asyncio.get_running_loop()
                pe = self.components.get("persona_extractor")

                tasks = {}

                # 人格抽取 (async)
                if pe is not None:
                    tasks["persona"] = pe.process_message(
                        message=user_msg_clean, context=recent,
                    )

                # 情感分析 (sync, 走线程)
                tasks["emotion"] = loop.run_in_executor(
                    None, self.components["emotion"].analyze,
                    user_msg_clean, recent,
                )

                # 记忆检索 (sync)
                tasks["memory"] = loop.run_in_executor(
                    None,
                    lambda: self.components["memory"].retrieve_context(
                        query=user_msg_clean, session_id=session_id, top_k=5,
                    ),
                )

                # RAG (sync)
                tasks["rag"] = loop.run_in_executor(
                    None, self.components["rag"].retrieve, user_msg_clean,
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
                            # RAG 返回 dict (results/style_examples/total_*)
                            # 序列化为 JSON 字符串供下游缓存键使用
                            import json
                            rag_context = json.dumps(task_result, sort_keys=True, ensure_ascii=False)
                        else:
                            rag_context = ""

                # 获取对话历史 + 摘要
                chat_history: list = []
                chat_summary: str = ""
                mem = self.components.get("memory")
                if mem and hasattr(mem, 'get_chat_context'):
                    chat_history, chat_summary = mem.get_chat_context(
                        session_id=session_id,
                    )

                # ── 组装 system prompt ──
                system_prompt = self.components["persona"].build_system_prompt(
                    emotion_state=emotion_state,
                    memory_context=memory_context,
                    rag_context=rag_context,
                    chat_summary=chat_summary,
                )
                if persona_enhancement:
                    system_prompt = f"{system_prompt}\n\n{persona_enhancement}"

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
                    persona_engine=self.components.get("persona"),
                    llm_gateway=self.components.get("llm"),
                    emotion_state=emotion_state,
                    session_id=session_id,
                    memory=self.components.get("memory"),
                )
                # === 检查结束 ===

                output_result = self.components["safety"].check_output(reply)
                if not output_result.is_safe:
                    reply = self.components["safety"].safe_alternative(output_result.category)

                # ── 聊后处理 ──
                emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""  # type: ignore[union-attr]
                mem_kwargs = dict(
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
                self.components["memory"].after_chat(**mem_kwargs)
                self.components["ase"].on_chat(user_msg_clean, reply)

                # ── 语音合成（用户明确要求时触发）──
                voice_audio: bytes | None = None
                want_voice = _detect_voice_request(user_msg)
                if want_voice and len(reply) >= 8:
                    voice_mgr = self.components.get("voice")
                    if voice_mgr and hasattr(voice_mgr, 'synthesize') and voice_mgr.enabled:
                        try:
                            # 截取合适长度（微信语音建议 ≤60 字）
                            voice_text = reply[:200]
                            # 注入情感参数
                            emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
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


def run_clone_pipeline(args: argparse.Namespace) -> None:
    print("\n" + "=" * 50)
    print("  [CLONE] 风格克隆管线")
    print("=" * 50)
    print(f"  目标: {args.clone}")
    print(f"  来源: {args.clone_source}")
    if args.clone_name:
        print(f"  名称: {args.clone_name}")
    print()

    from weclone_adapter import WeCloneAdapter

    adapter = WeCloneAdapter(
        data_dir=str(project_root / "data" / "clone"),
        output_dir=str(project_root / "data" / "training"),
    )

    health = adapter.health_check()
    print(f"  训练器: {'可用' if health['trainer_available'] else '未安装(跳过训练)'}")
    print(f"  量化: {'可用' if health['quant_available'] else '未安装'}")
    print()

    result = adapter.clone(
        target=args.clone,
        source=args.clone_source,
        name=args.clone_name or None,  # type: ignore[arg-type]
        do_train=health["trainer_available"],
    )

    if result.get("error"):
        print(f"  [FAIL] 克隆失败: {result['error']}")
        return

    print(f"\n{'=' * 50}")
    print("  [OK] 克隆完成")
    print(f"{'=' * 50}")
    print(f"  提取对话: {result.get('extracted_turns', 0)} 轮")
    print(f"  风格独特性: {result.get('uniqueness', 0):.0%}")
    if result.get("lora_path"):
        print(f"  LoRA 模型: {result['lora_path']}")
    print(f"  ToneMimic 注入: {result.get('injected_to_tone_mimic', 0)} 条")
    print()


def run_console_chat(orchestrator_or_obj, orchestrator_mode: str,
                     emotion_engine=None, ase_engine=None) -> None:
    print("\n" + "=" * 50)
    print(f"  [CHAT] 控制台聊天模式 ({orchestrator_mode} 模式)")
    print("  命令: /quit 退出  /status 查看状态  /health 健康检查  /reset 重置记忆")
    print("=" * 50 + "\n")

    session_id = f"console_{int(time.time())}"

    if orchestrator_mode == "full" and hasattr(orchestrator_or_obj, "_memory"):
        with contextlib.suppress(Exception):
            orchestrator_or_obj._memory.working.start_session(session_id, "console")

    try:
        while True:
            try:
                query = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[BYE] 下次再来找我哦~")
                break

            if not query:
                continue

            if query == "/quit":
                print("[BYE] 笨蛋, 记得想我！")
                break
            elif query == "/reset":
                if orchestrator_mode == "full" and hasattr(orchestrator_or_obj, "_memory"):
                    try:
                        orchestrator_or_obj._memory.structured_memory.clear_session("console")
                        print("[OK] 记忆已重置")
                    except Exception as e:  # noqa: BLE001
                        print(f"[WARN] 重置失败: {e}")
                continue
            elif query == "/status":
                if orchestrator_mode == "full" and emotion_engine:
                    state = emotion_engine.state
                    print(f"  情感: {state.primary_emotion.value} | "
                          f"强度: {state.primary_intensity:.2f} | "
                          f"能量: {state.energy:.1f} | "
                          f"好感度: {state.affinity}")
                    if ase_engine:
                        print(f"  主动消息紧迫度: {ase_engine.urgency:.2f}")
                elif orchestrator_mode == "fast":
                    emotion = orchestrator_or_obj.components.get("emotion")
                    memory = orchestrator_or_obj.components.get("memory")
                    if emotion and memory:
                        state = emotion.state
                        print(f"  情感: {state.primary_emotion.value} | "
                              f"强度: {state.primary_intensity:.2f} | "
                              f"能量: {state.energy:.1f} | "
                              f"好感度: {state.affinity}")
                        if hasattr(memory, "structured_memory"):
                            print(f"  今日对话: {memory.structured_memory.count_chats_today()} 条")
                continue
            elif query == "/health":
                if orchestrator_mode == "full":
                    from observability.health import health_checker
                    print(f"  系统状态: {health_checker.check()}")
                elif orchestrator_mode == "fast":
                    health = orchestrator_or_obj.health_check()
                    print(f"  系统状态: {'[OK] 健康' if health['healthy'] else '[WARN] 异常'}")
                    for name, status in health.get("components", {}).items():
                        print(f"    {name}: {status}")
                continue

            result = asyncio.run(orchestrator_or_obj.process_message(query, session_id)) if asyncio.iscoroutinefunction(orchestrator_or_obj.process_message) else orchestrator_or_obj.process_message(query, session_id)
            reply = result.get("reply", "")
            emotion = result.get("emotion")

            emotion_tag = ""
            if emotion:
                emotion_tag = f" [{emotion.get('primary', {}).get('type', '')}]"

            print(f"十四 > {reply}{emotion_tag}")

    except Exception:
        logger.exception("控制台聊天异常")


def run_wechat_mode(girlfriend_manager, orchestrator_mode: str,
                    args: argparse.Namespace) -> None:
    from wechat_direct import WeChatConnector

    print("\n📱 微信模式启动中（多用户版）...")
    print("   请用微信扫码登录，多个好友可与同一 bot 聊天")
    print("   每个用户有独立情感/记忆/角色")
    print("   或按 Ctrl+C 退出\n")

    connector = WeChatConnector(girlfriend_manager)

    try:
        connector.run()
    except KeyboardInterrupt:
        print("\n👋 正在停止...")
        connector.stop()


def _start_api_service(orchestrator_or_obj, cfg, config_mgr=None, girlfriend_manager=None):
    session_mgr = SessionManager()

    app_kwargs = dict(
        orchestrator=orchestrator_or_obj,
        health_checker=health_checker,
        session_manager=session_mgr,
        girlfriend_manager=girlfriend_manager,
    )
    if config_mgr is not None:
        app_kwargs["config_manager"] = config_mgr

    app = create_api_app(**app_kwargs)

    ws_server = WebSocketServer(
        orchestrator=orchestrator_or_obj,
        port=cfg.api.websocket_port,
    )

    def _run_api():
        import uvicorn
        uvicorn.run(app, host=cfg.api.host, port=cfg.api.port, log_level="info")

    def _run_ws():
        asyncio.run(ws_server.start())

    api_thread = threading.Thread(target=_run_api, daemon=True)
    api_thread.start()
    logger.info("REST API: http://%s:%d", cfg.api.host, cfg.api.port)

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()
    logger.info("WebSocket: ws://%s:%d", cfg.api.host, cfg.api.websocket_port)

    return ws_server


def _create_proactive_sender(ws_server_holder: dict, wechat_connector_holder: dict):
    """创建主动消息发送器 — 多通道统一出口（通过holder dict实现延迟注入）"""
    def send_proactive(msg: str):
        logger.info("[主动消息] %s", msg)
        print(f"\n💕 [十四主动] {msg}")

        ws_server = ws_server_holder.get("ws")
        if ws_server:
            try:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    if loop.is_running():
                        asyncio.ensure_future(ws_server.broadcast_proactive(msg))
                    else:
                        loop.run_until_complete(ws_server.broadcast_proactive(msg))
                except RuntimeError:
                    asyncio.run(ws_server.broadcast_proactive(msg))
            except Exception as e:  # noqa: BLE001
                logger.warning("[主动消息] WebSocket推送失败: %s", e)

        wechat_connector = wechat_connector_holder.get("connector")
        if wechat_connector:
            try:
                wechat_connector.send_text(msg)
            except Exception as e:  # noqa: BLE001
                logger.warning("[主动消息] 微信发送失败: %s", e)

    return send_proactive


def main() -> None:
    args = parse_args()

    # P0: 从 --log-level 开始配置日志（不等到 setup_logging 才生效）
    _setup_basic_logging(args.log_level)

    # P0: 检查 API_KEY 是否为出厂默认值
    _default_api_key = "CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN"
    _api_key_env = os.environ.get("API_KEY", _default_api_key)
    if _api_key_env == _default_api_key:
        logger.warning("⚠️ API_KEY 使用出厂默认值！生产环境必须修改！")
        if os.environ.get("APP_ENV", "").lower() in ("prod", "production"):
            logger.critical("生产环境禁止使用默认 API_KEY！请设置环境变量 API_KEY 后再启动。")
            sys.exit(1)

    use_console = args.console or args.no_wechat

    print_banner()

    if args.clone:
        run_clone_pipeline(args)
        return

    fusion_cfg = load_fusion_config(args.config)
    orchestrator_mode = fusion_cfg.get("orchestrator_mode", "full")
    logger.info("编排器模式: %s", orchestrator_mode)

    if orchestrator_mode == "fast":
        _run_fast_mode(args, use_console, fusion_cfg)
    else:
        _run_full_mode(args, use_console, fusion_cfg)


def _run_fast_mode(args: argparse.Namespace, use_console: bool,
                   fusion_cfg: dict[str, Any]) -> None:
    logger.info("=== fast 模式启动（多用户版） ===")

    orchestrator = OptimizedOrchestrator()

    if not orchestrator.initialize(config_dir=args.config, fusion_cfg=fusion_cfg):
        logger.error("系统初始化失败, 退出")
        sys.exit(1)

    # 注册 orchestrator 关闭函数，确保线程池被正确释放
    atexit.register(orchestrator.shutdown)

    cfg = orchestrator.components["config"].config

    setup_logging(cfg.observability.log_level, cfg.observability.log_format)

    if cfg.observability.metrics_enabled:
        from observability.metrics import setup_metrics
        setup_metrics(cfg.observability.metrics_port)

    graceful_shutdown.setup_signal_handlers()

    health = orchestrator.health_check()
    if not health["healthy"]:
        logger.warning("部分组件健康检查未通过, 继续启动...")

    # ── 创建女友管理器（多用户核心） ──
    girlfriend_mgr = GirlfriendManager(orchestrator)
    logger.info("女友管理器已创建")

    ws_server_fast = None
    _ws_holder = {}
    _wechat_holder = {}  # type: ignore[var-annotated]
    if not args.no_api:
        logger.info("启动API服务...")
        ws_server_fast = _start_api_service(orchestrator, cfg, config_mgr=orchestrator.components.get("config"), girlfriend_manager=girlfriend_mgr)
        _ws_holder["ws"] = ws_server_fast
    else:
        logger.info("API服务已禁用 (--no-api)")

    scheduler = None
    if not args.no_scheduler:
        from proactive.scheduler import ProactiveScheduler

        send_proactive = _create_proactive_sender(
            ws_server_holder=_ws_holder,
            wechat_connector_holder=_wechat_holder,
        )

        def daily_maintenance():
            mem = orchestrator.components.get("memory")
            if mem and hasattr(mem, "daily_maintenance"):
                mem.daily_maintenance()

        scheduler = ProactiveScheduler(
            ase_engine=orchestrator.components["ase"],
            send_message_func=send_proactive,  # 兜底
            daily_maintenance_func=daily_maintenance,
        )

        # 注册通道
        if ws_server_fast:
            scheduler.register_channel("websocket", lambda: ws_server_fast.broadcast_proactive)
        scheduler.register_channel("console", lambda: lambda msg: logger.info("[主动消息] %s", msg))

        if scheduler.start():
            logger.info("主动消息调度器已启动")
            atexit.register(scheduler.stop)
        else:
            logger.warning("主动消息调度器启动失败")
    else:
        logger.info("主动消息系统已禁用 (--no-scheduler)")

    if args.init_only:
        logger.info("--init-only 模式, 初始化完成")
        return

    if use_console:
        try:
            run_console_chat(orchestrator, "fast")
        finally:
            # 确保在控制台模式退出时关闭资源
            orchestrator.shutdown()
        if not args.no_api:
            logger.info("控制台聊天已退出, API服务继续保持运行中...")
            while True:
                time.sleep(3600)
    else:
        try:
            run_wechat_mode(girlfriend_mgr, "fast", args)
        finally:
            # 确保在微信模式退出时关闭资源
            orchestrator.shutdown()


def _run_full_mode(args: argparse.Namespace, use_console: bool,
                   fusion_cfg: dict[str, Any]) -> None:
    logger.info("=== full 模式启动（多用户版） ===")

    emotion_fusion = fusion_cfg.get("emotion", {})
    persona_fusion = fusion_cfg.get("persona", {})
    memory_fusion = fusion_cfg.get("memory", {})
    ase_fusion = fusion_cfg.get("ase", {})

    logger.info("[1/12] 加载V2配置...")
    config_mgr = ConfigManager(config_dir=args.config)
    cfg = config_mgr.config
    logger.info("      环境: %s, LLM模型: %s", cfg.env, cfg.llm.primary_model)

    logger.info("[2/12] 初始化可观测性...")
    setup_logging(cfg.observability.log_level, cfg.observability.log_format)

    if cfg.observability.metrics_enabled:
        from observability.metrics import setup_metrics
        setup_metrics(cfg.observability.metrics_port)

    graceful_shutdown.setup_signal_handlers()

    logger.info("[3/12] 初始化安全层...")
    safety_filter = ContentSafetyFilter(enabled=cfg.safety.input_filter_enabled)
    pii_anonymizer = PIIAnonymizer(enabled=cfg.safety.pii_anonymizer_enabled)
    encryption_mgr = EncryptionManager(
        key_env=cfg.safety.encryption_key_env,
        enabled=cfg.safety.encryption_enabled,
    )
    injection_detector = PromptInjectionDetector(enabled=cfg.safety.prompt_injection_detection)

    logger.info("[4/12] 初始化LLM网关V2...")
    from llm_provider.prompt_template_manager import PromptTemplateMgr

    llm = get_llm(models_config=cfg.llm.models_priority)
    PromptTemplateMgr()

    safety_filter.llm_gateway = llm
    injection_detector.llm_gateway = llm

    health = llm.health_check()
    llm_ok = health.get("configured", False) or health.get("reachable", False)
    if llm_ok:
        logger.info("      ✅ %s 已配置 (model=%s)", type(llm).__name__, llm.model)
    else:
        logger.info("      ⚠️ LLM 未配置或不可用，将使用模拟回复")

    logger.info("[5/12] 初始化角色引擎 (融合)...")
    from my_character import ConfigLoader
    config_loader = ConfigLoader(config_dir=args.config)

    classifier_mode = emotion_fusion.get("classifier_mode", "hybrid")
    blend_ratio = emotion_fusion.get("blend_ratio", cfg.emotion.continuity_blend_ratio)
    classifier_timeout = emotion_fusion.get("classifier_timeout_ms",
                                             cfg.emotion.llm_classifier_timeout_ms)

    emotion_engine = EmotionEngine(
        llm_gateway=llm,
        use_llm=cfg.emotion.use_llm_classifier,
        blend_ratio=blend_ratio,
        classifier_timeout_ms=classifier_timeout,
        classifier_mode=classifier_mode,
    )

    prompt_mode = persona_fusion.get("prompt_mode", "layered")
    anchor_verification = persona_fusion.get("anchor_verification_enabled", True)

    persona_engine = PersonaEngine(
        config_loader=config_loader,
        llm_gateway=llm,
        prompt_mode=prompt_mode,
        anchor_verification_enabled=anchor_verification,
    )

    tone_mimic = None
    try:
        from my_character import ToneMimic
        tone_mimic = ToneMimic(chroma_path=str(project_root / "data" / "chroma_db"))
    except ImportError:
        logger.warning("ToneMimic 不可用")

    logger.info("[6/12] 初始化记忆系统 (融合)...")
    forgetting_model = memory_fusion.get("forgetting_model", "exponential")

    vector_memory = VectorMemory(chroma_path=str(project_root / "data" / "chroma_db"))
    structured_memory = StructuredMemory(db_path=str(project_root / "data" / "sqlite.db"))

    memory_pipeline = MemoryPipeline(
        structured_memory=structured_memory,
        vector_memory=vector_memory,
        llm_gateway=llm,
        working_limit=cfg.memory.working_memory_limit,
        retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
        forgetting_model=forgetting_model,
    )

    logger.info("[7/12] 初始化工具系统...")
    tool_registry = ToolRegistry()
    tool_dispatcher = ToolDispatcher(
        tool_registry,
        timeout=cfg.tools.execution_timeout_seconds,
        rate_limit_per_minute=cfg.tools.rate_limit_per_tool_per_minute,
    )

    for tool_cls in [WeatherTool, SearchTool, CalendarTool, CalculatorTool]:
        tool_registry.register(tool_cls())
    tool_registry.register(ReminderTool(structured_memory))
    tool_registry.register(CalendarQueryTool(structured_memory))
    tool_registry.register(TimeAwarenessTool())
    tool_registry.register(CharacterCrawlerTool())
    logger.info("      已注册 %d 个工具", len(tool_registry.tool_names))

    logger.info("[8/12] 初始化RAG引擎V2...")
    rag_engine = RAGEngineV2(
        vector_memory=vector_memory,
        structured_memory=structured_memory,
        semantic_memory=memory_pipeline.semantic,
        tone_mimic=tone_mimic,
    )

    logger.info("[9/12] 初始化主动消息 (融合)...")
    ase_frequency_mode = ase_fusion.get("frequency_mode", "adaptive")
    ase_generation_mode = ase_fusion.get("generation_mode", "llm")
    ase_reflection_mode = ase_fusion.get("reflection_mode", "rule")

    ase_engine = ASEEngine(
        llm_gateway=llm,
        max_daily_messages=cfg.proactive.max_daily_messages,
        min_interval_minutes=cfg.proactive.min_interval_minutes,
        cooldown_after_reply=cfg.proactive.cooldown_after_reply_minutes,
        urgency_threshold=cfg.proactive.urgency_threshold,
        frequency_mode=ase_frequency_mode,
        generation_mode=ase_generation_mode,
        reflection_mode=ase_reflection_mode,
    )

    scheduler = None
    _ws_holder_full = {}  # type: ignore[var-annotated]
    _wechat_holder_full = {}  # type: ignore[var-annotated]
    if not args.no_scheduler:
        from proactive.scheduler import ProactiveScheduler

        _send_proactive = _create_proactive_sender(
            ws_server_holder=_ws_holder_full,
            wechat_connector_holder=_wechat_holder_full,
        )

        def _daily_maintenance():
            try:
                summary = memory_pipeline.daily_maintenance()
                if summary:
                    logger.info("每日摘要: %s", summary)
            except Exception as e:  # noqa: BLE001
                logger.warning("每日维护异常: %s", e)

        scheduler = ProactiveScheduler(
            ase_engine=ase_engine,
            send_message_func=_send_proactive,  # 兜底
            daily_maintenance_func=_daily_maintenance,
        )

        # 注册通道（ws_server在后续API启动后注入）
        scheduler.register_channel("console", lambda: lambda msg: logger.info("[主动消息] %s", msg))

        if scheduler.start():
            logger.info("      调度器已启动")
            atexit.register(scheduler.stop)
        else:
            logger.warning("      调度器启动失败，以无调度模式运行")
            scheduler = None
    else:
        logger.info("      主动消息系统已禁用 (--no-scheduler)")

    logger.info("[10/12] 组装对话编排器 (full)...")
    from multimodal.multimodal_processor import MultimodalProcessor
    from orchestrator import Orchestrator

    multimodal = MultimodalProcessor(llm_gateway=llm)
    orchestrator = Orchestrator(
        llm_gateway=llm,
        emotion_engine=emotion_engine,
        persona_engine=persona_engine,
        memory_pipeline=memory_pipeline,
        rag_engine=rag_engine,
        tool_dispatcher=tool_dispatcher,
        multimodal_processor=multimodal,
        ase_engine=ase_engine,
        safety_filter=safety_filter,
        pii_anonymizer=pii_anonymizer,
        injection_detector=injection_detector,
    )

    # ── 创建女友管理器（多用户核心） ──
    girlfriend_mgr = GirlfriendManager(orchestrator)
    logger.info("女友管理器已创建")

    if not args.no_api:
        logger.info("[11/12] 启动API服务...")
        session_mgr = SessionManager()

        app = create_api_app(
            orchestrator=orchestrator,
            health_checker=health_checker,
            config_manager=config_mgr,
            session_manager=session_mgr,
            girlfriend_manager=girlfriend_mgr,
        )
        ws_server = WebSocketServer(
            orchestrator=orchestrator,
            port=cfg.api.websocket_port,
        )

        health_checker.register_defaults(
            emotion_engine=emotion_engine,
            tone_mimic=tone_mimic,
            vector_memory=vector_memory,
            structured_memory=structured_memory,
            llm_gateway=llm,
            ase_engine=ase_engine,
            scheduler=scheduler,
        )

        def _run_api():
            import uvicorn
            uvicorn.run(app, host=cfg.api.host, port=cfg.api.port, log_level="info")

        def _run_ws():
            asyncio.run(ws_server.start())

        api_thread = threading.Thread(target=_run_api, daemon=True)
        api_thread.start()
        logger.info("      REST API: http://%s:%d", cfg.api.host, cfg.api.port)

        ws_thread = threading.Thread(target=_run_ws, daemon=True)
        ws_thread.start()
        logger.info("      WebSocket: ws://%s:%d", cfg.api.host, cfg.api.websocket_port)

        if scheduler:
            _ws_holder_full["ws"] = ws_server
            scheduler.register_channel("websocket", lambda: ws_server.broadcast_proactive)
            logger.info("      WebSocket已注入调度器")
    else:
        logger.info("[11/12] API服务已禁用 (--no-api)")

    logger.info("[12/12] 启动聊天通道...")

    components = {
        "ConfigManager": config_mgr,
        "EmotionEngine": emotion_engine,
        "PersonaEngine": persona_engine,
        "ToneMimic": tone_mimic,
        "MemoryPipeline": memory_pipeline,
        "LLMGateway": llm,
        "ToolRegistry": tool_registry,
        "RAGEngine": rag_engine,
        "ASEEngine": ase_engine,
        "Orchestrator": orchestrator,
        "ContentSafety": safety_filter,
        "PIIAnonymizer": pii_anonymizer,
        "EncryptionManager": encryption_mgr,
        "PromptInjectionDetector": injection_detector,
    }
    health_check_all(components)

    if args.init_only:
        logger.info("--init-only 模式, 初始化完成")
        return

    if use_console:
        run_console_chat(orchestrator, "full",
                         emotion_engine=emotion_engine,
                         ase_engine=ase_engine)
        if not args.no_api:
            logger.info("控制台聊天已退出, API服务继续保持运行中...")
            while True:
                time.sleep(3600)
    else:
        run_wechat_mode(girlfriend_mgr, "full", args)


if __name__ == "__main__":
    main()
