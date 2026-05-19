#!/usr/bin/env python3
"""
AI 伴侣女友"小暖" — V2 主入口

启动流程：
1. 加载V2配置（Pydantic + 热重载）
2. 初始化可观测性（链路追踪 + 指标 + 健康检查）
3. 初始化安全层（内容过滤 + PII脱敏 + 加密 + 注入检测）
4. 初始化LLM网关V2（流式 + FC + 多模型）
5. 初始化角色引擎V2（情感V2 + 人格V2 + 语气模仿）
6. 初始化记忆系统V2（三层记忆 + 重要性评分 + 遗忘）
7. 初始化工具系统（调度器 + 沙箱 + 内置工具）
8. 初始化RAG引擎V2（混合检索 + 重排序 + 反幻觉）
9. 初始化主动消息V2（LLM生成 + 情境感知 + 频率自适应）
10. 组装对话编排器
11. 启动API服务（REST + WebSocket）
12. 启动聊天通道（CowAgent微信 / 控制台）

使用方式：
    python main_v2.py                  # 完整V2启动
    python main_v2.py --no-wechat      # 控制台聊天模式
    python main_v2.py --no-api         # 不启动REST/WebSocket API
    python main_v2.py --no-scheduler   # 无主动消息
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(project_root / "data" / "app.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("main_v2")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI伴侣女友 V2 - 小暖")
    parser.add_argument("--no-wechat", action="store_true", help="不启动微信通道")
    parser.add_argument("--no-scheduler", action="store_true", help="不启动主动消息调度器")
    parser.add_argument("--no-api", action="store_true", help="不启动REST/WebSocket API")
    parser.add_argument("--config", type=str, default="config", help="配置文件目录")
    parser.add_argument("--init-only", action="store_true", help="仅初始化")
    parser.add_argument("--clone", type=str, default=None, help="风格克隆目标")
    parser.add_argument("--clone-source", type=str, default="wcf", choices=["wcf", "wechatmsg", "txt", "csv", "json"])
    parser.add_argument("--clone-name", type=str, default="")
    return parser.parse_args()


def print_banner():
    print("""
    ╔══════════════════════════════════╗
    ║      💕 小暖 — AI 伴侣女友      ║
    ║        v2.0 · 全面升级版        ║
    ╚══════════════════════════════════╝
    """)


def main():
    args = parse_args()
    print_banner()

    if args.clone:
        _run_clone_pipeline(args)
        return

    # ── 1. 配置加载 ──
    logger.info("[1/12] 加载V2配置...")
    from observability.config_manager import ConfigManager
    config_mgr = ConfigManager(config_dir=args.config)
    cfg = config_mgr.config
    logger.info("      环境: %s, LLM模型: %s", cfg.env, cfg.llm.primary_model)

    # ── 2. 可观测性初始化 ──
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

    # ── 3. 安全层初始化 ──
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

    # ── 4. LLM网关V2 ──
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
        logger.info("      ⚠️ 未检测到API Key，将使用模拟回复")

    # ── 5. 角色引擎V2 ──
    logger.info("[5/12] 初始化角色引擎V2...")
    from my_character.emotion_engine_v2 import EmotionEngineV2
    from my_character.persona_engine_v2 import PersonaEngineV2
    from my_character import ToneMimic

    from my_character import ConfigLoader
    config_loader = ConfigLoader(config_dir=args.config)

    emotion_engine = EmotionEngineV2(
        llm_gateway=llm,
        use_llm=cfg.emotion.use_llm_classifier,
        blend_ratio=cfg.emotion.continuity_blend_ratio,
        classifier_timeout_ms=cfg.emotion.llm_classifier_timeout_ms,
    )
    persona_engine = PersonaEngineV2(config_loader=config_loader, llm_gateway=llm)
    tone_mimic = ToneMimic(chroma_path=str(project_root / "data" / "chroma_db"))

    # ── 6. 记忆系统V2 ──
    logger.info("[6/12] 初始化记忆系统V2...")
    from memory import VectorMemory, StructuredMemory
    from memory_v2.memory_pipeline_v2 import MemoryPipelineV2

    vector_memory = VectorMemory(chroma_path=str(project_root / "data" / "chroma_db"))
    structured_memory = StructuredMemory(db_path=str(project_root / "data" / "sqlite.db"))

    memory_pipeline = MemoryPipelineV2(
        structured_memory=structured_memory,
        vector_memory=vector_memory,
        llm_gateway=llm,
        working_limit=cfg.memory.working_memory_limit,
        retrieval_timeout=cfg.memory.retrieval_timeout_seconds,
    )

    # ── 7. 工具系统 ──
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

    # ── 8. RAG引擎V2 ──
    logger.info("[8/12] 初始化RAG引擎V2...")
    from rag_engine.rag_engine_v2 import RAGEngineV2

    rag_engine = RAGEngineV2(
        vector_memory=vector_memory,
        structured_memory=structured_memory,
        semantic_memory=memory_pipeline.semantic,
        tone_mimic=tone_mimic,
    )

    # ── 9. 主动消息V2 ──
    logger.info("[9/12] 初始化主动消息V2...")
    from proactive.ase_engine_v2 import ASEEngineV2

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

        scheduler = ProactiveScheduler(
            ase_engine=ase_engine,
            send_message_func=_send_proactive,
        )
        logger.info("      调度器已就绪")
    else:
        logger.info("      主动消息系统已禁用 (--no-scheduler)")

    # ── 10. 对话编排器 ──
    logger.info("[10/12] 组装对话编排器...")
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

    # ── 11. API服务 ──
    api_server = None
    ws_server = None
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

    # ── 12. 启动聊天通道 ──
    logger.info("[12/12] 启动聊天通道...")

    components = {
        "ConfigManager": config_mgr,
        "EmotionEngineV2": emotion_engine,
        "PersonaEngineV2": persona_engine,
        "ToneMimic": tone_mimic,
        "MemoryPipelineV2": memory_pipeline,
        "LLMGatewayV2": llm,
        "ToolRegistry": tool_registry,
        "RAGEngineV2": rag_engine,
        "ASEEngineV2": ase_engine,
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
        logger.info("--init-only 模式，初始化完成")
        return

    if args.no_wechat:
        _run_console_chat_v2(orchestrator, emotion_engine, ase_engine)
        if not args.no_api:
            logger.info("控制台聊天已退出，API服务继续保持运行中...")
            import time
            while True:
                time.sleep(3600)
    else:
        _run_wechat_chat_v2(orchestrator, emotion_engine, ase_engine, config_loader)


def _run_console_chat_v2(orchestrator, emotion_engine, ase_engine):
    print("\n" + "=" * 50)
    print("  V2 控制台聊天模式")
    print("  输入 /quit 退出  /status 查看状态  /health 健康检查")
    print("=" * 50 + "\n")

    session_id = "console_v2"
    orchestrator._memory.working.start_session(session_id, "console")

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
                print("👋 笨蛋，记得想我！")
                break
            elif query == "/status":
                state = emotion_engine.state
                print(f"  情感: {state.primary_emotion.value} | 强度: {state.primary_intensity:.2f} | 能量: {state.energy:.1f} | 好感度: {state.affinity}")
                if ase_engine:
                    print(f"  主动消息紧迫度: {ase_engine.urgency:.2f}")
                continue
            elif query == "/health":
                from observability.health import health_checker
                print(f"  系统状态: {health_checker.check()}")
                continue

            result = orchestrator.process_message(query, session_id)
            reply = result.get("reply", "")
            emotion = result.get("emotion")
            emotion_str = ""
            if emotion:
                emotion_str = f" [{emotion.get('primary', {}).get('type', '')}]"
            print(f"小暖 > {reply}{emotion_str}")

    except Exception as e:
        logger.exception("控制台聊天异常")


def _run_wechat_chat_v2(orchestrator, emotion_engine, ase_engine, config_loader):
    from cowagent_adapter import patch_cowagent, GirlfriendBot
    import cowagent_adapter._globals as gl

    class OrchestratorAdapter:
        def __init__(self, orchestrator):
            self._orch = orchestrator

        def reply(self, user_msg: str):
            result = self._orch.process_message(user_msg)
            return type("Reply", (), {"content": result.get("reply", "")})()

    bot = OrchestratorAdapter(orchestrator)
    gl.bot_registry.register(bot)
    gl._girlfriend_bot_instance = bot
    patch_cowagent()

    logger.info("正在启动 CowAgent 微信通道（V2模式）...")
    print("\n📱 微信模式启动中...")

    try:
        os.environ["COWAGENT_CONFIG"] = str(project_root / "config" / "cowagent_config.json")
        import cowagent_src.app as cowapp
        cowapp.run()
    except KeyboardInterrupt:
        print("\n👋 CowAgent 已停止")
    except Exception as e:
        logger.error("CowAgent 运行异常: %s", e)
        raise


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


def _run_clone_pipeline(args):
    from main import _run_clone_pipeline as _orig
    _orig(args)


if __name__ == "__main__":
    main()
