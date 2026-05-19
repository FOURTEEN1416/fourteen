#!/usr/bin/env python3
"""
AI 伴侣女友"小暖" — 融合统一版主入口

融合 V1 + V2 + Optimized 三版优势：
- V1: CowAgent子进程管理 + 心跳监控 + 自动重启
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
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(project_root / "data" / "app.log"),
                encoding="utf-8",
            ),
        ],
    )
    return logging.getLogger("main")


logger = setup_logging()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI伴侣女友 - 小暖 (融合统一版)",
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
    ╔══════════════════════════════════════════════════╗
    ║                                                  ║
    ║           💕 小暖 — AI 伴侣女友 💕               ║
    ║                                                  ║
    ║     情感 · 记忆 · 主动交互 · 风格克隆            ║
    ║            融合统一版 v3.0                        ║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝
    """
    print(banner)


def load_fusion_config(config_dir: str) -> Dict[str, Any]:
    import yaml
    system_yaml = project_root / config_dir / "system.yaml"
    if system_yaml.exists():
        with open(system_yaml, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
        return full_cfg.get("fusion", {})
    logger.warning("未找到 %s, 使用默认 fusion 配置", system_yaml)
    return {}


class OptimizedOrchestrator:
    """
    优化版对话编排器 (fast 模式)

    简洁流程: 安全→PII脱敏→注入检测→情感→记忆→RAG→LLM→输出安全→存储→ASE
    """

    def __init__(self):
        self.components: Dict[str, Any] = {}
        self._initialized = False
        self._lock = False

    def initialize(self, config_dir: str = "config",
                   fusion_cfg: Optional[Dict] = None) -> bool:
        if self._initialized:
            return True

        fusion_cfg = fusion_cfg or {}
        logger.info("[初始化] 启动并行组件初始化 (fast 模式)...")
        start_time = time.perf_counter()

        try:
            from observability.config_manager import ConfigManager
            self.components["config"] = ConfigManager(config_dir=config_dir)
            cfg = self.components["config"].config

            from observability.tracing import tracer
            from observability.health import health_checker
            self.components["tracer"] = tracer
            self.components["health"] = health_checker

            from safety.content_safety import ContentSafetyFilter
            from safety.pii_anonymizer import PIIAnonymizer
            from safety.prompt_injection import PromptInjectionDetector

            self.components["safety"] = ContentSafetyFilter(
                enabled=cfg.safety.input_filter_enabled
            )
            self.components["pii"] = PIIAnonymizer(
                enabled=cfg.safety.pii_anonymizer_enabled
            )
            self.components["injection"] = PromptInjectionDetector(
                enabled=cfg.safety.prompt_injection_detection
            )

            from llm_provider.llm_gateway_v2 import LLMGatewayV2
            self.components["llm"] = LLMGatewayV2(
                models_config=cfg.llm.models_priority
            )

            self.components["safety"].llm_gateway = self.components["llm"]
            self.components["injection"].llm_gateway = self.components["llm"]

            emotion_fusion = fusion_cfg.get("emotion", {})
            blend_ratio = emotion_fusion.get("blend_ratio", cfg.emotion.continuity_blend_ratio)
            classifier_timeout = emotion_fusion.get("classifier_timeout_ms",
                                                     cfg.emotion.llm_classifier_timeout_ms)

            try:
                from my_character.emotion_engine import EmotionEngine as EmotionEngineOptimized
                from my_character.persona_engine import PersonaEngine as PersonaEngineOptimized
            except ImportError:
                from my_character.emotion_engine import EmotionEngine as EmotionEngineOptimized
                from my_character.persona_engine import PersonaEngine as PersonaEngineOptimized

            from my_character.tone_mimic import ToneMimic

            self.components["emotion"] = EmotionEngineOptimized(
                llm_gateway=self.components["llm"],
                use_llm=cfg.emotion.use_llm_classifier,
            )
            self.components["persona"] = PersonaEngineOptimized(
                config_dir=config_dir,
                llm_gateway=self.components["llm"],
            )
            self.components["tone"] = ToneMimic(
                chroma_path=str(project_root / "data" / "chroma_db")
            )

            try:
                from memory.memory_pipeline_optimized import MemoryPipelineOptimized
                self.components["memory"] = MemoryPipelineOptimized(
                    chroma_path=str(project_root / "data" / "chroma_db"),
                    db_path=str(project_root / "data" / "sqlite.db"),
                    llm_gateway=self.components["llm"],
                    working_limit=cfg.memory.working_memory_limit,
                )
            except ImportError:
                from memory import VectorMemory, StructuredMemory
                from memory.memory_pipeline import MemoryPipeline
                vector_memory = VectorMemory(chroma_path=str(project_root / "data" / "chroma_db"))
                structured_memory = StructuredMemory(db_path=str(project_root / "data" / "sqlite.db"))
                self.components["memory"] = MemoryPipelineV2(
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

            from tool_system.base import ToolRegistry, ToolDispatcher
            from tool_system.builtin.weather_tool import WeatherTool
            from tool_system.builtin.search_tool import SearchTool
            from tool_system.builtin.calendar_tool import CalendarTool, CalculatorTool
            from tool_system.builtin.reminder_tool import ReminderTool, CalendarQueryTool

            registry = ToolRegistry()
            for tool_cls in [WeatherTool, SearchTool, CalendarTool, CalculatorTool]:
                registry.register(tool_cls())

            mem = self.components.get("memory")
            sm = getattr(mem, "structured_memory", None)
            if sm:
                registry.register(ReminderTool(sm))
                registry.register(CalendarQueryTool(sm))

            self.components["tool_registry"] = registry
            self.components["tools"] = ToolDispatcher(
                registry,
                timeout=cfg.tools.execution_timeout_seconds,
                rate_limit_per_minute=cfg.tools.rate_limit_per_tool_per_minute,
            )

            from rag_engine.rag_engine_v2 import RAGEngineV2
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

            self._initialized = True
            init_time = time.perf_counter() - start_time
            logger.info("[初始化] 完成, 耗时 %.2fs", init_time)
            return True

        except Exception as e:
            logger.error("[初始化] 失败: %s", e)
            return False

    def process_message(self, user_msg: str, session_id: str = "") -> Dict[str, Any]:
        if not self._initialized:
            return {"reply": "系统初始化中, 请稍候...", "error": "not_initialized"}

        if self._lock:
            return {"reply": "处理中, 请稍候...", "error": "busy"}

        self._lock = True
        start_time = time.perf_counter()

        try:
            safety_result = self.components["safety"].check_input(user_msg)
            if not safety_result.is_safe:
                self._lock = False
                return {
                    "reply": self.components["safety"].safe_alternative(safety_result.category),
                    "safety_triggered": True,
                }

            user_msg_clean, _ = self.components["pii"].anonymize(user_msg)

            is_injection, _, _ = self.components["injection"].detect(user_msg_clean)
            if is_injection:
                user_msg_clean = self.components["injection"].sanitize(user_msg_clean)

            emotion_state = self.components["emotion"].analyze(
                user_msg_clean,
                context=self.components["memory"].get_recent_context(3),
            )

            memory_context = self.components["memory"].retrieve_context(
                query=user_msg_clean,
                session_id=session_id,
                top_k=5,
            )

            rag_context = self.components["rag"].retrieve(user_msg_clean)

            system_prompt = self.components["persona"].build_system_prompt(
                emotion_state=emotion_state,
                memory_context=memory_context,
                rag_context=rag_context,
            )

            reply = self.components["llm"].chat(
                query=user_msg_clean,
                system_prompt=system_prompt,
                temperature=0.85,
                max_tokens=2048,
            )

            output_result = self.components["safety"].check_output(reply)
            if not output_result.is_safe:
                reply = self.components["safety"].safe_alternative(output_result.category)

            self.components["memory"].after_chat(
                user_msg=user_msg_clean,
                reply=reply,
                emotion=emotion_state.primary_emotion.value if emotion_state else "",
                session_id=session_id,
            )

            self.components["ase"].on_chat(user_msg_clean, reply)

            process_time = time.perf_counter() - start_time

            self._lock = False
            return {
                "reply": reply,
                "emotion": emotion_state.to_dict() if emotion_state else None,
                "process_time": round(process_time, 3),
            }

        except Exception as e:
            logger.exception("消息处理异常")
            self._lock = False
            return {"reply": "（处理消息时出现异常, 请稍后重试）", "error": str(e)}

    def health_check(self) -> Dict[str, Any]:
        results = {}
        all_ok = True

        for name, component in self.components.items():
            if hasattr(component, "health_check"):
                try:
                    status = component.health_check()
                    results[name] = status
                    if isinstance(status, dict):
                        if not all(v for v in status.values() if isinstance(v, bool)):
                            all_ok = False
                except Exception as e:
                    results[name] = {"error": str(e)}
                    all_ok = False
            else:
                results[name] = "no check"

        return {"healthy": all_ok, "components": results}


def run_clone_pipeline(args: argparse.Namespace) -> None:
    print("\n" + "=" * 50)
    print("  🧬 风格克隆管线")
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
        name=args.clone_name or None,
        do_train=health["trainer_available"],
    )

    if result.get("error"):
        print(f"  ❌ 克隆失败: {result['error']}")
        return

    print(f"\n{'=' * 50}")
    print(f"  ✅ 克隆完成")
    print(f"{'=' * 50}")
    print(f"  提取对话: {result.get('extracted_turns', 0)} 轮")
    print(f"  风格独特性: {result.get('uniqueness', 0):.0%}")
    if result.get("lora_path"):
        print(f"  LoRA 模型: {result['lora_path']}")
    print(f"  ToneMimic 注入: {result.get('injected_to_tone_mimic', 0)} 条")
    print()


def health_check_all(components: dict) -> bool:
    all_ok = True
    print("\n[健康检查]")
    for name, component in components.items():
        if hasattr(component, "health_check"):
            try:
                status = component.health_check()
                ok = True
                if isinstance(status, dict):
                    ok = not any(v is False for v in status.values())
                print(f"  {'✅' if ok else '❌'} {name}")
                if not ok:
                    logger.warning("%s health check failed: %s", name, status)
                    all_ok = False
            except Exception as e:
                print(f"  ❌ {name} (error: {e})")
                all_ok = False
        else:
            print(f"  ✅ {name}")
    if all_ok:
        print("\n  ✅ 全部通过\n")
    else:
        print("\n  ⚠️ 部分组件异常\n")
    return all_ok


def run_console_chat(orchestrator_or_obj, orchestrator_mode: str,
                     emotion_engine=None, ase_engine=None) -> None:
    print("\n" + "=" * 50)
    print(f"  💬 控制台聊天模式 ({orchestrator_mode} 模式)")
    print("  命令: /quit 退出  /status 查看状态  /health 健康检查  /reset 重置记忆")
    print("=" * 50 + "\n")

    session_id = f"console_{int(time.time())}"

    if orchestrator_mode == "full" and hasattr(orchestrator_or_obj, "_memory"):
        try:
            orchestrator_or_obj._memory.working.start_session(session_id, "console")
        except Exception:
            pass

    try:
        while True:
            try:
                query = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 下次再来找我哦～")
                break

            if not query:
                continue

            if query == "/quit":
                print("👋 笨蛋, 记得想我！")
                break
            elif query == "/reset":
                if orchestrator_mode == "full" and hasattr(orchestrator_or_obj, "_memory"):
                    try:
                        orchestrator_or_obj._memory.structured_memory.clear_session("console")
                        print("✅ 记忆已重置")
                    except Exception as e:
                        print(f"⚠️ 重置失败: {e}")
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
                    print(f"  系统状态: {'✅ 健康' if health['healthy'] else '⚠️ 异常'}")
                    for name, status in health.get("components", {}).items():
                        print(f"    {name}: {status}")
                continue

            result = orchestrator_or_obj.process_message(query, session_id)
            reply = result.get("reply", "")
            emotion = result.get("emotion")

            emotion_tag = ""
            if emotion:
                emotion_tag = f" [{emotion.get('primary', {}).get('type', '')}]"

            print(f"小暖 > {reply}{emotion_tag}")

    except Exception as e:
        logger.exception("控制台聊天异常")


def run_wechat_mode(orchestrator_or_obj, orchestrator_mode: str,
                    args: argparse.Namespace) -> None:
    from cowagent_adapter import patch_cowagent, GirlfriendBot
    import cowagent_adapter._globals as gl

    class OrchestratorAdapter:
        def __init__(self, orch):
            self._orch = orch

        def reply(self, user_msg: str) -> Any:
            result = self._orch.process_message(user_msg)
            return type("Reply", (), {"content": result.get("reply", "")})()

    bot = OrchestratorAdapter(orchestrator_or_obj)
    gl.bot_registry.register(bot)
    gl._girlfriend_bot_instance = bot
    patch_cowagent()

    heartbeat = None
    heartbeat_available = False

    try:
        from cowagent_adapter.heartbeat import WeChatHeartbeat
        heartbeat = WeChatHeartbeat(check_interval=30, max_missed=3)

        def on_heartbeat_change(connected: bool):
            if connected:
                logger.info("💓 微信连接状态: 已连接")
            else:
                logger.warning("💔 微信连接状态: 已断开")

        heartbeat.on_status_change(on_heartbeat_change)
        if hasattr(bot, '_heartbeat'):
            bot._heartbeat = heartbeat
        heartbeat_available = True
    except (ImportError, Exception) as e:
        logger.warning("心跳监控未启用: %s", e)

    wechat_process: Optional[multiprocessing.Process] = None

    def start_cowagent_process():
        nonlocal wechat_process

        config_path = str(project_root / "config" / "cowagent_config.json")
        os.environ["COWAGENT_CONFIG"] = config_path

        def _run_in_subprocess():
            import cowagent_src.app as cowapp
            try:
                cowapp.run()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                logger.error("CowAgent 子进程异常: %s", e)
                raise

        wechat_process = multiprocessing.Process(
            target=_run_in_subprocess,
            daemon=True,
            name="cowagent-wechat",
        )
        wechat_process.start()
        logger.info("CowAgent 子进程已启动 (PID=%d)", wechat_process.pid)

    def check_cowagent_process():
        nonlocal wechat_process
        if wechat_process is None:
            return
        if not wechat_process.is_alive():
            logger.warning("CowAgent 子进程已退出 (exitcode=%s), 正在重启...",
                          wechat_process.exitcode)
            start_cowagent_process()

    print("\n📱 微信模式启动中...")
    print("   请扫描二维码登录微信个人号")
    print("   或按 Ctrl+C 切换回控制台模式\n")

    start_cowagent_process()

    if heartbeat_available and heartbeat:
        heartbeat.start(cowagent_instance=None)

    try:
        while True:
            time.sleep(10)
            check_cowagent_process()
    except KeyboardInterrupt:
        print("\n👋 正在停止...")
        if heartbeat_available and heartbeat:
            heartbeat.stop()
        if wechat_process and wechat_process.is_alive():
            wechat_process.terminate()
            wechat_process.join(timeout=5)
            logger.info("CowAgent 子进程已终止")


def _start_api_service(orchestrator_or_obj, cfg, config_mgr=None) -> None:
    from api.rest_api import create_api_app
    from api.websocket_server import WebSocketServer
    from api.session_manager import SessionManager
    from observability.health import health_checker

    session_mgr = SessionManager()

    app_kwargs = dict(
        orchestrator=orchestrator_or_obj,
        health_checker=health_checker,
        session_manager=session_mgr,
    )
    if config_mgr is not None:
        app_kwargs["config_manager"] = config_mgr

    app = create_api_app(**app_kwargs)

    ws_server = WebSocketServer(
        orchestrator=orchestrator_or_obj,
        port=cfg.api.websocket_port,
    )

    import threading

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


def main() -> None:
    args = parse_args()

    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

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
                   fusion_cfg: Dict[str, Any]) -> None:
    logger.info("=== fast 模式启动 ===")

    orchestrator = OptimizedOrchestrator()

    if not orchestrator.initialize(config_dir=args.config, fusion_cfg=fusion_cfg):
        logger.error("系统初始化失败, 退出")
        sys.exit(1)

    cfg = orchestrator.components["config"].config

    from observability.logging_setup import setup_logging
    setup_logging(cfg.observability.log_level, cfg.observability.log_format)

    from observability.tracing import tracer
    from observability.health import health_checker
    from observability.graceful_shutdown import graceful_shutdown

    if cfg.observability.metrics_enabled:
        from observability.metrics import setup_metrics
        setup_metrics(cfg.observability.metrics_port)

    graceful_shutdown.setup_signal_handlers()

    health = orchestrator.health_check()
    if not health["healthy"]:
        logger.warning("部分组件健康检查未通过, 继续启动...")

    scheduler = None
    if not args.no_scheduler:
        from proactive.scheduler import ProactiveScheduler

        def send_proactive(msg: str):
            logger.info("[主动消息] %s", msg)
            print(f"\n💕 [小暖主动] {msg}")

        def daily_maintenance():
            mem = orchestrator.components.get("memory")
            if mem and hasattr(mem, "daily_maintenance"):
                mem.daily_maintenance()

        scheduler = ProactiveScheduler(
            ase_engine=orchestrator.components["ase"],
            send_message_func=send_proactive,
            daily_maintenance_func=daily_maintenance,
        )

        if scheduler.start():
            logger.info("主动消息调度器已启动")
        else:
            logger.warning("主动消息调度器启动失败")
    else:
        logger.info("主动消息系统已禁用 (--no-scheduler)")

    if not args.no_api:
        logger.info("启动API服务...")
        _start_api_service(orchestrator, cfg, config_mgr=orchestrator.components.get("config"))
    else:
        logger.info("API服务已禁用 (--no-api)")

    if args.init_only:
        logger.info("--init-only 模式, 初始化完成")
        return

    if use_console:
        run_console_chat(orchestrator, "fast")
        if not args.no_api:
            logger.info("控制台聊天已退出, API服务继续保持运行中...")
            while True:
                time.sleep(3600)
    else:
        run_wechat_mode(orchestrator, "fast", args)


def _run_full_mode(args: argparse.Namespace, use_console: bool,
                   fusion_cfg: Dict[str, Any]) -> None:
    logger.info("=== full 模式启动 ===")

    emotion_fusion = fusion_cfg.get("emotion", {})
    persona_fusion = fusion_cfg.get("persona", {})
    memory_fusion = fusion_cfg.get("memory", {})
    ase_fusion = fusion_cfg.get("ase", {})

    logger.info("[1/12] 加载V2配置...")
    from observability.config_manager import ConfigManager
    config_mgr = ConfigManager(config_dir=args.config)
    cfg = config_mgr.config
    logger.info("      环境: %s, LLM模型: %s", cfg.env, cfg.llm.primary_model)

    logger.info("[2/12] 初始化可观测性...")
    from observability.logging_setup import setup_logging
    setup_logging(cfg.observability.log_level, cfg.observability.log_format)

    from observability.tracing import tracer
    from observability.health import health_checker
    from observability.graceful_shutdown import graceful_shutdown

    if cfg.observability.metrics_enabled:
        from observability.metrics import setup_metrics
        setup_metrics(cfg.observability.metrics_port)

    graceful_shutdown.setup_signal_handlers()

    logger.info("[3/12] 初始化安全层...")
    from safety.content_safety import ContentSafetyFilter
    from safety.pii_anonymizer import PIIAnonymizer
    from safety.encryption import EncryptionManager
    from safety.prompt_injection import PromptInjectionDetector

    safety_filter = ContentSafetyFilter(enabled=cfg.safety.input_filter_enabled)
    pii_anonymizer = PIIAnonymizer(enabled=cfg.safety.pii_anonymizer_enabled)
    encryption_mgr = EncryptionManager(
        key_env=cfg.safety.encryption_key_env,
        enabled=cfg.safety.encryption_enabled,
    )
    injection_detector = PromptInjectionDetector(enabled=cfg.safety.prompt_injection_detection)

    logger.info("[4/12] 初始化LLM网关V2...")
    from llm_provider.llm_gateway_v2 import LLMGatewayV2
    from llm_provider.prompt_template_mgr import PromptTemplateMgr

    llm = LLMGatewayV2(models_config=cfg.llm.models_priority)
    template_mgr = PromptTemplateMgr()

    safety_filter.llm_gateway = llm
    injection_detector.llm_gateway = llm

    llm_ok = llm.health_check().get("configured", False)
    if llm_ok:
        logger.info("      ✅ LLM API已配置 (model=%s)", llm.model)
    else:
        logger.info("      ⚠️ 未检测到API Key, 将使用模拟回复")

    logger.info("[5/12] 初始化角色引擎 (融合)...")
    from my_character import ConfigLoader
    config_loader = ConfigLoader(config_dir=args.config)

    classifier_mode = emotion_fusion.get("classifier_mode", "hybrid")
    blend_ratio = emotion_fusion.get("blend_ratio", cfg.emotion.continuity_blend_ratio)
    classifier_timeout = emotion_fusion.get("classifier_timeout_ms",
                                             cfg.emotion.llm_classifier_timeout_ms)

    from my_character.emotion_engine import EmotionEngine as EmotionEngineV2
    emotion_engine = EmotionEngineV2(
        llm_gateway=llm,
        use_llm=cfg.emotion.use_llm_classifier,
        blend_ratio=blend_ratio,
        classifier_timeout_ms=classifier_timeout,
    )

    prompt_mode = persona_fusion.get("prompt_mode", "layered")
    anchor_verification = persona_fusion.get("anchor_verification_enabled", True)

    from my_character.persona_engine import PersonaEngine as PersonaEngineV2
    persona_engine = PersonaEngineV2(config_loader=config_loader, llm_gateway=llm)

    tone_mimic = None
    try:
        from my_character import ToneMimic
        tone_mimic = ToneMimic(chroma_path=str(project_root / "data" / "chroma_db"))
    except ImportError:
        logger.warning("ToneMimic 不可用")

    logger.info("[6/12] 初始化记忆系统 (融合)...")
    forgetting_model = memory_fusion.get("forgetting_model", "exponential")

    from memory import VectorMemory, StructuredMemory
    from memory.memory_pipeline import MemoryPipeline

    vector_memory = VectorMemory(chroma_path=str(project_root / "data" / "chroma_db"))
    structured_memory = StructuredMemory(db_path=str(project_root / "data" / "sqlite.db"))

    memory_pipeline = MemoryPipelineV2(
        structured_memory=structured_memory,
        vector_memory=vector_memory,
        llm_gateway=llm,
        working_limit=cfg.memory.working_memory_limit,
        retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
    )

    logger.info("[7/12] 初始化工具系统...")
    from tool_system.base import ToolRegistry, ToolDispatcher
    from tool_system.builtin.weather_tool import WeatherTool
    from tool_system.builtin.search_tool import SearchTool
    from tool_system.builtin.calendar_tool import CalendarTool, CalculatorTool
    from tool_system.builtin.reminder_tool import ReminderTool, CalendarQueryTool

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
    logger.info("      已注册 %d 个工具", len(tool_registry.tool_names))

    logger.info("[8/12] 初始化RAG引擎V2...")
    from rag_engine.rag_engine_v2 import RAGEngineV2

    rag_engine = RAGEngineV2(
        vector_memory=vector_memory,
        structured_memory=structured_memory,
        semantic_memory=memory_pipeline.semantic,
        tone_mimic=tone_mimic,
    )

    logger.info("[9/12] 初始化主动消息 (融合)...")
    frequency_mode = ase_fusion.get("frequency_mode", "adaptive")
    generation_mode = ase_fusion.get("generation_mode", "llm")
    reflection_mode = ase_fusion.get("reflection_mode", "rule")

    from proactive.ase_engine import ASEEngine as ASEEngineV2

    ase_engine = ASEEngineV2(
        llm_gateway=llm,
        max_daily_messages=cfg.proactive.max_daily_messages,
        min_interval_minutes=cfg.proactive.min_interval_minutes,
        cooldown_after_reply=cfg.proactive.cooldown_after_reply_minutes,
        urgency_threshold=cfg.proactive.urgency_threshold,
    )

    scheduler = None
    if not args.no_scheduler:
        from proactive.scheduler import ProactiveScheduler

        def _send_proactive(msg: str):
            logger.info("[PROACTIVE] %s", msg)
            print(f"\n💕 [小暖主动] {msg}")

        def _daily_maintenance():
            try:
                summary = memory_pipeline.daily_maintenance()
                if summary:
                    logger.info("每日摘要: %s", summary)
            except Exception as e:
                logger.warning("每日维护异常: %s", e)

        scheduler = ProactiveScheduler(
            ase_engine=ase_engine,
            send_message_func=_send_proactive,
            daily_maintenance_func=_daily_maintenance,
        )
        logger.info("      调度器已就绪")
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

    if not args.no_api:
        logger.info("[11/12] 启动API服务...")
        from api.rest_api import create_api_app
        from api.websocket_server import WebSocketServer
        from api.session_manager import SessionManager

        session_mgr = SessionManager()

        app = create_api_app(
            orchestrator=orchestrator,
            health_checker=health_checker,
            config_manager=config_mgr,
            session_manager=session_mgr,
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

        import threading

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

    if scheduler:
        try:
            scheduler.start()
            logger.info("调度器已启动")
        except Exception as e:
            logger.warning("调度器启动失败: %s", e)

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
        run_wechat_mode(orchestrator, "full", args)


if __name__ == "__main__":
    main()
