#!/usr/bin/env python3
"""
"唯一的你" — 融合统一版主入口

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
import contextlib
import logging
import os
import re
import sys
import threading
import time
from collections.abc import AsyncIterator
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

from api.app_factory import create_api_app  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from api.websocket_server import WebSocketServer  # noqa: E402
from context.world_info_provider import WorldInfoProvider  # noqa: E402
from llm_provider import get_llm  # noqa: E402
from my_character.emotion_engine import EmotionEngine  # noqa: E402
from my_character.tone_mimic import ToneMimic  # noqa: E402
from observability.config_manager import ConfigManager  # noqa: E402
from observability.graceful_shutdown import graceful_shutdown  # noqa: E402
from observability.health import health_checker  # noqa: E402
from observability.logging_setup import setup_logging  # noqa: E402
from proactive.ase_engine import ASEEngine  # noqa: E402
from security.content_safety import ContentSafetyFilter  # noqa: E402
from security.encryption import EncryptionManager  # noqa: E402
from security.pii_anonymizer import PIIAnonymizer  # noqa: E402
from security.prompt_injection import PromptInjectionDetector  # noqa: E402
from shisi.application.knowledge_service import ShisiKnowledgeAdapter  # noqa: E402
from shisi.application.memory_service import ShisiMemoryService  # noqa: E402
from shisi.application.persona_service import PersonaService  # noqa: E402
from tools.base_tool import ToolDispatcher, ToolRegistry, ToolResult  # noqa: E402
from tools.builtin.calendar_tool import CalculatorTool, CalendarTool  # noqa: E402
from tools.builtin.character_crawler_tool import CharacterCrawlerTool  # noqa: E402
from tools.builtin.extra_tools import ImageGenTool, MemoryTool, SchedulerTool, WebSummaryTool  # noqa: E402
from tools.builtin.reminder_tool import CalendarQueryTool, ReminderTool  # noqa: E402
from tools.builtin.search_tool import SearchTool  # noqa: E402
from tools.builtin.time_awareness_tool import TimeAwarenessTool  # noqa: E402
from tools.builtin.weather_tool import WeatherTool  # noqa: E402
from user_scheduler import UserManager  # noqa: E402
from utils.health_check import _is_healthy, health_check_all  # noqa: E402

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
        description="唯一的你 (融合统一版)",
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
             唯一的你 -- AI 系统
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


def _should_use_shisi_memory(fusion_cfg: dict[str, Any]) -> bool:
    """判断是否使用 shisi 记忆服务适配层。

    优先级：环境变量 USE_SHISI_MEMORY > fusion.memory.use_shisi_memory
    """
    env_val = os.environ.get("USE_SHISI_MEMORY")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")
    return fusion_cfg.get("memory", {}).get("use_shisi_memory", False)


def _should_use_shisi_rag(fusion_cfg: dict[str, Any]) -> bool:
    """判断是否使用 shisi knowledge 适配层。

    优先级：环境变量 USE_SHISI_RAG > fusion.rag.use_shisi_rag
    """
    env_val = os.environ.get("USE_SHISI_RAG")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")
    return fusion_cfg.get("rag", {}).get("use_shisi_rag", False)


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
        # 计数反诘模块：跟踪用户连续说"没事"等敷衍词的次数
        from my_character.counter_rebuttal import CounterRebuttal
        self._counter_rebuttal = CounterRebuttal()

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
    def _load_character_persona_segment(cls, character_id: str) -> str:
        """根据 character_id 加载角色卡人设，返回可追加到 system prompt 的片段。

        - character_id 为空 / "default" / "demo" 时返回空串（保持基线人设）
        - 先按 {character_id}.json 找文件，找不到再遍历 config/characters/ 匹配 JSON 内部 id
        - 结果缓存，避免重复 IO
        """
        if not character_id or character_id in ("default", "demo"):
            return ""

        # 命中缓存
        if character_id in cls._character_persona_cache:
            return cls._character_persona_cache[character_id]

        import json as _json
        chars_dir = project_root / "config" / "characters"
        card_data: dict | None = None

        # 1. 直接按文件名查
        direct_path = chars_dir / f"{character_id}.json"
        if direct_path.exists():
            try:
                with open(direct_path, encoding="utf-8") as f:
                    card_data = _json.load(f)
            except (OSError, _json.JSONDecodeError):
                card_data = None

        # 2. 遍历匹配 JSON 内部 id 字段
        if card_data is None and chars_dir.exists():
            try:
                for f in chars_dir.glob("*.json"):
                    try:
                        with open(f, encoding="utf-8") as fh:
                            data = _json.load(fh)
                        if data.get("id") == character_id:
                            card_data = data
                            break
                    except (OSError, _json.JSONDecodeError):
                        continue
            except OSError:
                pass

        if not card_data:
            cls._character_persona_cache[character_id] = ""
            return ""

        # 构造人设片段
        lines: list[str] = ["=== 角色卡人设 ==="]
        name = card_data.get("name", "")
        if name:
            lines.append(f"角色名：{name}")
        desc = card_data.get("description", "")
        if desc:
            lines.append(f"简介：{desc[:200]}")

        anchors = card_data.get("core_anchors", [])
        if anchors:
            lines.append(f"核心锚点：{'、'.join(anchors)}")

        personality = card_data.get("personality", {})
        if personality:
            lines.append("性格维度：")
            # 常见维度中文映射
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

        speaking = card_data.get("speaking_style", {})
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

        catchphrases = speaking.get("catchphrases", []) if isinstance(speaking, dict) else []
        if not catchphrases:
            catchphrases = card_data.get("catchphrases", [])
        if catchphrases:
            lines.append(f"口头禅：{' / '.join(catchphrases[:5])}")

        scenario = card_data.get("scenario", "")
        if scenario:
            lines.append(f"场景设定：{str(scenario)[:300]}")

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
            if _should_use_shisi_memory(fusion_cfg):
                logger.info("使用 shisi 记忆服务适配层 (ShisiMemoryService)")
            else:
                logger.info("使用原有记忆管线 (经 ShisiMemoryService 包装的 MemoryPipeline)")
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
                use_legacy_rag=not _should_use_shisi_rag(fusion_cfg),
            )
            if _should_use_shisi_rag(fusion_cfg):
                logger.info("使用 shisi knowledge 适配层 (ShisiKnowledgeAdapter)")
            else:
                logger.info("使用原有 RAG 引擎 (经 ShisiKnowledgeAdapter 包装的 RAGEngineV2)")

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

                # ── 动态设置 PersonaExtractor 的 user_id（修复 P0-B：避免多用户串味） ──
                pe = self.components.get("persona_extractor")
                if pe is not None:
                    # 用 (character_id, session_id) 拼接作为 user_id，session_id 为空时退化为 character_id
                    effective_user_id = (
                        f"{character_id}:{session_id}" if session_id else f"{character_id}"
                    )
                    if pe.user_id != effective_user_id:
                        pe.set_user_id(effective_user_id)

                # ── 并行执行独立任务 ──
                recent = self.components["memory"].get_recent_context(3)
                loop = asyncio.get_running_loop()

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

                # ── 世界信息动态注入 ──
                world_info = ""
                wip = self.components.get("world_info")
                if wip:
                    try:
                        world_info = wip.render()
                    except Exception as e:  # noqa: BLE001
                        logger.debug("World info render failed: %s", e)

                # ── 组装 system prompt ──
                system_prompt = self.components["persona"].build_system_prompt(
                    emotion_state=emotion_state,
                    memory_context=memory_context,
                    rag_context=rag_context,
                    chat_summary=chat_summary,
                    world_info=world_info,
                    character_id=character_id,
                )

                # 角色卡人设动态注入（v3.1）：放在核心位置，确保角色身份优先于基线人设
                if character_id and character_id not in ("default", "demo"):
                    char_segment = self._load_character_persona_segment(character_id)
                    if char_segment:
                        system_prompt = (
                            f"{system_prompt}\n\n"
                            f"# 当前必须扮演的角色（最高优先级）\n"
                            f"{char_segment}\n\n"
                            f"你当前正在扮演以上角色。"
                            f"回复时必须使用该角色的名字、身份、性格、说话风格和口头禅；"
                            f"不要以‘十四’或通用 AI 身份自居。"
                        )

                if persona_enhancement:
                    system_prompt = f"{system_prompt}\n\n{persona_enhancement}"

                # ── 工具调用（fast 模式）──
                affinity_level = self._get_affinity_level(emotion_state)
                tool_results = await self._run_tools_if_needed(
                    self.components["llm"],
                    user_msg_clean,
                    system_prompt,
                    chat_history,
                    affinity_level=affinity_level,
                )
                if tool_results:
                    system_prompt = f"{system_prompt}\n\n{tool_results}"

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
                    try:
                        self.components["memory"].after_chat(**mem_kwargs)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("after_chat failed, skipping: %s", e)
                self.components["ase"].on_chat(user_msg_clean, reply)

                # ── 同步 AffinityEnhancer + EmotionStageEngine（修复 P0-C） ──
                # 把 EmotionEngine 的 affection_points 增量同步到 shisi 体系，
                # 让"好感度"在三个系统（EmotionEngine/AffinityEnhancer/StageEngine）一致
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
                # 5. 动态设置 PersonaExtractor 的 user_id（多用户隔离）
                pe = self.components.get("persona_extractor")
                if pe is not None:
                    effective_user_id = (
                        f"{character_id}:{session_id}" if session_id else f"{character_id}"
                    )
                    if pe.user_id != effective_user_id:
                        pe.set_user_id(effective_user_id)

                # 6. 并行执行独立任务（情感/记忆/RAG/人格抽取）
                recent = self.components["memory"].get_recent_context(3)
                loop = asyncio.get_running_loop()

                tasks = {}
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
                        persona_enhancement = task_result or ""
                    elif name == "emotion":
                        emotion_state = task_result
                    elif name == "memory":
                        memory_context = task_result or ""
                    elif name == "rag":
                        if task_result:
                            import json
                            rag_context = json.dumps(task_result, sort_keys=True, ensure_ascii=False)
                        else:
                            rag_context = ""

                # 7. 获取对话历史 + 摘要
                chat_history: list = []
                chat_summary: str = ""
                mem = self.components.get("memory")
                if mem and hasattr(mem, 'get_chat_context'):
                    chat_history, chat_summary = mem.get_chat_context(
                        session_id=session_id,
                    )

                # 8. 世界信息动态注入
                world_info = ""
                wip = self.components.get("world_info")
                if wip:
                    try:
                        world_info = wip.render()
                    except Exception as e:  # noqa: BLE001
                        logger.debug("World info render failed: %s", e)

                # 8. 组装 system prompt
                system_prompt = self.components["persona"].build_system_prompt(
                    emotion_state=emotion_state,
                    memory_context=memory_context,
                    rag_context=rag_context,
                    chat_summary=chat_summary,
                    world_info=world_info,
                    character_id=character_id,
                )

                # 角色卡人设动态注入（v3.1）：放在核心位置，确保角色身份优先于基线人设
                if character_id and character_id not in ("default", "demo"):
                    char_segment = self._load_character_persona_segment(character_id)
                    if char_segment:
                        system_prompt = (
                            f"{system_prompt}\n\n"
                            f"# 当前必须扮演的角色（最高优先级）\n"
                            f"{char_segment}\n\n"
                            f"你当前正在扮演以上角色。"
                            f"回复时必须使用该角色的名字、身份、性格、说话风格和口头禅；"
                            f"不要以‘十四’或通用 AI 身份自居。"
                        )

                if persona_enhancement:
                    system_prompt = f"{system_prompt}\n\n{persona_enhancement}"

                # 8.5 工具调用（fast 模式流式）
                affinity_level = self._get_affinity_level(emotion_state)
                tool_results = await self._run_tools_if_needed(
                    llm,
                    user_msg_clean,
                    system_prompt,
                    chat_history,
                    affinity_level=affinity_level,
                )
                if tool_results:
                    system_prompt = f"{system_prompt}\n\n{tool_results}"

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

                emotion_tag = emotion_state.primary_emotion.value if emotion_state else ""
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
                    try:
                        self.components["memory"].after_chat(**mem_kwargs)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("after_chat failed, skipping: %s", e)
                self.components["ase"].on_chat(user_msg_clean, reply)

                # 好感度同步（与 process_message 保持一致）
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


def run_wechat_mode(user_manager, orchestrator_mode: str,
                    args: argparse.Namespace,
                    wechat_connector_holder: dict | None = None) -> None:
    from wechat_direct import WeChatConnector

    print("\n📱 微信模式启动中（多用户版）...")
    print("   请用微信扫码登录，多个好友可与同一 bot 聊天")
    print("   每个用户有独立情感/记忆/角色")
    print("   或按 Ctrl+C 退出\n")

    connector = WeChatConnector(user_manager)

    # 注入 connector 到 holder，供主动消息调度器使用
    # 必须在 connector.run() 之前注入，这样调度器的健康检查能在登录后自动拾取
    if wechat_connector_holder is not None:
        wechat_connector_holder["connector"] = connector
        logger.info("微信连接器已注入主动消息通道 holder")

    try:
        connector.run()
    except KeyboardInterrupt:
        print("\n👋 正在停止...")
        connector.stop()


def _start_api_service(orchestrator_or_obj, cfg, config_mgr=None, user_manager=None):
    session_mgr = SessionManager()

    app_kwargs = dict(
        orchestrator=orchestrator_or_obj,
        health_checker=health_checker,
        session_manager=session_mgr,
        user_manager=user_manager,
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

    # ── 创建用户调度器（多用户核心） ──
    user_mgr = UserManager(orchestrator)
    logger.info("用户调度器已创建")

    ws_server_fast = None
    _ws_holder = {}
    _wechat_holder = {}  # type: ignore[var-annotated]
    if not args.no_api:
        logger.info("启动API服务...")
        ws_server_fast = _start_api_service(orchestrator, cfg, config_mgr=orchestrator.components.get("config"), user_manager=user_mgr)
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
            emotion_engine=orchestrator.components.get("emotion"),
        )

        # 注册通道
        if ws_server_fast:
            scheduler.register_channel("websocket", lambda: ws_server_fast.broadcast_proactive)
        scheduler.register_channel("console", lambda: lambda msg: logger.info("[主动消息] %s", msg))

        # 注册微信通道 — 工厂从 holder 延迟读取 connector
        # connector 在 run_wechat_mode 中注入，调度器健康检查会自动拾取
        def _wechat_sender_factory(_holder=_wechat_holder):
            connector = _holder.get("connector")
            if connector is None:
                return None
            async def _send(msg: str):
                connector.send_text(msg)
            return _send
        scheduler.register_channel("wechat", _wechat_sender_factory)

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
            run_wechat_mode(user_mgr, "fast", args, wechat_connector_holder=_wechat_holder)
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

    llm = get_llm(provider=cfg.llm.provider, models_config=cfg.llm.models_priority)
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

    persona_service = PersonaService(
        config_loader=config_loader,
        llm_gateway=llm,
        prompt_mode=prompt_mode,
        anchor_verification_enabled=anchor_verification,
    )
    persona_engine = persona_service.engine

    tone_mimic = None
    try:
        from my_character import ToneMimic
        tone_mimic = ToneMimic(chroma_path=str(project_root / "data" / "chroma_db"))
    except ImportError:
        logger.warning("ToneMimic 不可用")

    logger.info("[6/12] 初始化记忆系统 (融合)...")
    forgetting_model = memory_fusion.get("forgetting_model", "exponential")

    # Shisi 适配层是唯一对外接口；其内部按需懒加载 VectorMemory / StructuredMemory。
    memory_pipeline = ShisiMemoryService(
        chroma_path=str(project_root / "data" / "chroma_db"),
        db_path=str(project_root / "data" / "sqlite.db"),
        llm_gateway=llm,
        working_limit=cfg.memory.working_memory_limit,
        retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
        forgetting_model=forgetting_model,
    )
    vector_memory = memory_pipeline.vector_memory
    structured_memory = memory_pipeline.structured_memory
    if _should_use_shisi_memory(fusion_cfg):
        logger.info("      使用 shisi 记忆服务适配层 (ShisiMemoryService)")
    else:
        logger.info("      使用原有记忆管线 (经 ShisiMemoryService 包装的 MemoryPipeline)")

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
    tool_registry.register(MemoryTool(structured_memory))
    tool_registry.register(SchedulerTool(structured_memory))
    tool_registry.register(TimeAwarenessTool())
    tool_registry.register(CharacterCrawlerTool())
    tool_registry.register(WebSummaryTool())
    tool_registry.register(ImageGenTool())
    logger.info("      已注册 %d 个工具", len(tool_registry.tool_names))

    logger.info("[8/12] 初始化RAG引擎V2...")
    # Shisi 适配层是唯一对外接口；use_legacy_rag=True 时内部委托给 RAGEngineV2。
    rag_engine = ShisiKnowledgeAdapter(
        vector_memory=vector_memory,
        structured_memory=structured_memory,
        semantic_memory=memory_pipeline.semantic,
        tone_mimic=tone_mimic,
        use_legacy_rag=not _should_use_shisi_rag(fusion_cfg),
    )
    if _should_use_shisi_rag(fusion_cfg):
        logger.info("      使用 shisi knowledge 适配层 (ShisiKnowledgeAdapter)")
    else:
        logger.info("      使用原有 RAG 引擎 (经 ShisiKnowledgeAdapter 包装的 RAGEngineV2)")

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
            emotion_engine=emotion_engine,
        )

        # 注册通道（ws_server在后续API启动后注入）
        scheduler.register_channel("console", lambda: lambda msg: logger.info("[主动消息] %s", msg))

        # 注册微信通道 — 工厂从 holder 延迟读取 connector
        # connector 在 run_wechat_mode 中注入，调度器健康检查会自动拾取
        def _wechat_sender_factory_full(_holder=_wechat_holder_full):
            connector = _holder.get("connector")
            if connector is None:
                return None
            async def _send(msg: str):
                connector.send_text(msg)
            return _send
        scheduler.register_channel("wechat", _wechat_sender_factory_full)

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

    # ── 创建用户调度器（多用户核心） ──
    user_mgr = UserManager(orchestrator)
    logger.info("用户调度器已创建")

    if not args.no_api:
        logger.info("[11/12] 启动API服务...")
        session_mgr = SessionManager()

        app = create_api_app(
            orchestrator=orchestrator,
            health_checker=health_checker,
            config_manager=config_mgr,
            session_manager=session_mgr,
            user_manager=user_mgr,
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
        run_wechat_mode(user_mgr, "full", args, wechat_connector_holder=_wechat_holder_full)


if __name__ == "__main__":
    main()
