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
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from sqlalchemy import select  # noqa: E402

from orchestrator.optimized_orchestrator import OptimizedOrchestrator

# Import project_root
project_root = Path(__file__).parent.absolute()
sys.path.insert(0, str(project_root))

from api.app_factory import create_api_app  # noqa: E402
from api.database import WechatBinding, _async_session  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from api.websocket_server import WebSocketServer  # noqa: E402
from observability.graceful_shutdown import graceful_shutdown  # noqa: E402
from observability.health import health_checker  # noqa: E402
from observability.logging_setup import setup_logging  # noqa: E402
from user_scheduler import UserManager  # noqa: E402

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


async def _init_user_bindings(user_mgr: UserManager) -> list[dict[str, Any]]:
    """启动时从 DB 加载微信绑定并注入用户调度器缓存。"""
    async with _async_session() as session:
        result = await session.execute(select(WechatBinding))
        bindings = [b.to_dict() for b in result.scalars().all()]
    await user_mgr.load_bindings(bindings)
    return bindings


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

    _run_orchestrator(args, use_console, fusion_cfg, orchestrator_mode)


def _run_orchestrator(args: argparse.Namespace, use_console: bool,
                      fusion_cfg: dict[str, Any], mode: str) -> None:
    """统一编排器启动入口（fast / full 模式共享）。

    `_init_mixin.initialize()` 是唯一初始化真相源，两个模式仅通过 fusion_cfg 差异化配置。
    调度器由 _init_mixin 创建，本函数仅在 `--no-scheduler` 时停止调度器并设置 DISABLE_SCHEDULER=1，
    或在启用时注册 ws/wechat 通道。
    """
    logger.info("=== %s 模式启动（多用户版） ===", mode)

    # ── 1. 创建并初始化编排器（_init_mixin.initialize() 是唯一初始化真相源） ──
    orchestrator = OptimizedOrchestrator()
    if not orchestrator.initialize(config_dir=args.config, fusion_cfg=fusion_cfg):
        logger.error("系统初始化失败, 退出")
        sys.exit(1)
    atexit.register(orchestrator.shutdown)

    cfg = orchestrator.components["config"].config

    # ── 2. 可观测性 ──
    setup_logging(cfg.observability.log_level, cfg.observability.log_format)
    if cfg.observability.metrics_enabled:
        from observability.metrics import setup_metrics
        setup_metrics(cfg.observability.metrics_port)
    graceful_shutdown.setup_signal_handlers()

    # ── 3. 健康检查 ──
    health = orchestrator.health_check()
    if not health["healthy"]:
        logger.warning("部分组件健康检查未通过, 继续启动...")

    # ── 4. 用户调度器（多用户核心） ──
    user_mgr = UserManager(orchestrator)
    logger.info("用户调度器已创建")
    try:
        bindings = asyncio.run(_init_user_bindings(user_mgr))
        logger.info("已从数据库加载 %d 条微信绑定", len(bindings))
    except Exception as e:  # noqa: BLE001
        logger.warning("加载微信绑定失败: %s", e)

    # ── 5. API 服务 ──
    _ws_holder: dict[str, Any] = {}
    _wechat_holder: dict[str, Any] = {}
    if not args.no_api:
        logger.info("启动API服务...")
        ws_server = _start_api_service(
            orchestrator, cfg,
            config_mgr=orchestrator.components.get("config"),
            user_manager=user_mgr,
        )
        _ws_holder["ws"] = ws_server
    else:
        logger.info("API服务已禁用 (--no-api)")

    # ── 6. 主动消息调度器通道注册 ──
    # 调度器由 _init_mixin._init_ase_and_scheduler 统一创建并启动，本节仅注册通道。
    scheduler = orchestrator.components.get("scheduler")
    if args.no_scheduler:
        # 用户明确禁用 → 停止 _init_mixin 已启动的调度器
        # 同时设置环境变量供子进程（如 uvicorn worker）继承，避免子进程重新启动调度器
        os.environ["DISABLE_SCHEDULER"] = "1"
        if scheduler is not None and hasattr(scheduler, "stop"):
            scheduler.stop()
            orchestrator.components["scheduler"] = None
        logger.info("主动消息系统已禁用 (--no-scheduler)，已设置 DISABLE_SCHEDULER=1 供子进程继承")
    elif scheduler is not None:
        # 增强为多通道发送（_init_mixin 默认 send 仅日志输出）
        scheduler._send = _create_proactive_sender(
            ws_server_holder=_ws_holder,
            wechat_connector_holder=_wechat_holder,
        )

        def _daily_maintenance():
            mem = orchestrator.components.get("memory")
            if mem and hasattr(mem, "daily_maintenance"):
                try:
                    summary = mem.daily_maintenance()
                    if summary:
                        logger.info("每日摘要: %s", summary)
                except Exception as e:  # noqa: BLE001
                    logger.warning("每日维护异常: %s", e)
        scheduler._daily_maintenance = _daily_maintenance

        # 注册 ws / console / wechat 通道（console 覆盖 _init_mixin 的默认注册）
        ws_server = _ws_holder.get("ws")
        if ws_server is not None:
            scheduler.register_channel("websocket", lambda: ws_server.broadcast_proactive)
        scheduler.register_channel(
            "console", lambda: lambda msg: logger.info("[主动消息] %s", msg)
        )

        def _wechat_sender_factory(_holder=_wechat_holder):
            connector = _holder.get("connector")
            if connector is None:
                return None
            async def _send(msg: str):
                connector.send_text(msg)
            return _send
        scheduler.register_channel("wechat", _wechat_sender_factory)
        logger.info("主动消息调度器通道已注册（ws/console/wechat）")
    else:
        logger.warning("主动消息调度器未初始化（_init_mixin 启动失败），跳过通道注册")

    # ── 7. init_only 模式 ──
    if args.init_only:
        logger.info("--init-only 模式, 初始化完成")
        return

    # ── 8. 运行聊天通道 ──
    if use_console:
        try:
            run_console_chat(orchestrator, mode)
        finally:
            orchestrator.shutdown()
        if not args.no_api:
            logger.info("控制台聊天已退出, API服务继续保持运行中...")
            while True:
                time.sleep(3600)
    else:
        try:
            run_wechat_mode(user_mgr, mode, args, wechat_connector_holder=_wechat_holder)
        finally:
            orchestrator.shutdown()


if __name__ == "__main__":
    main()
