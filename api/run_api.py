"""
唯一的你 API-Only 启动入口

用法:
    uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload

或直接运行:
    python api/run_api.py
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

# 确保项目根在 sys.path
_project_root = Path(__file__).parent.parent.absolute()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 自动加载 .env 文件（如果存在），生产部署优先用系统环境变量
_env_file = _project_root / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file, override=False)
    except ImportError:
        pass  # python-dotenv 未安装，环境变量需手动设置
    except Exception:
        pass  # 静默失败，不影响启动

# 必须在任何 onnxruntime/chromadb import 之前设置 ORT 日志级别
# 0=VERBOSE 1=INFO 2=WARNING 3=ERROR 4=FATAL
# 设 3 屏蔽 "EP Error nvinfer_10.dll missing" 噪声（系统缺 TensorRT 库）
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")

from sqlalchemy import select  # noqa: E402

from api.app_factory import create_api_app  # noqa: E402
from api.database import User, WechatBinding, _async_session, init_db  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from api.websocket_server import HAS_WEBSOCKETS, WebSocketServer  # noqa: E402
from main import OptimizedOrchestrator, UserManager  # noqa: E402
from observability.graceful_shutdown import graceful_shutdown  # noqa: E402
from observability.health import health_checker  # noqa: E402
from observability.logging_setup import setup_logging  # noqa: E402

logger = logging.getLogger("run_api")

# ── 初始化编排器 ──
orchestrator = OptimizedOrchestrator()
if not orchestrator.initialize(config_dir="config"):
    logger.critical("编排器初始化失败，检查 config/system.yaml 是否存在")
    sys.exit(1)

atexit.register(orchestrator.shutdown)

# ── 配置可观测性 ──
cfg = orchestrator.components["config"].config
setup_logging(cfg.observability.log_level, cfg.observability.log_format)
graceful_shutdown.setup_signal_handlers()

# ── 检查组件健康 ──
health = orchestrator.health_check()
if not health.get("healthy", False):
    # 找出真正失败的字段（嵌套 components 也要展开）
    failing = {}
    for k, v in health.get("components", {}).items():
        if isinstance(v, dict):
            false_keys = [kk for kk, vv in v.items() if isinstance(vv, bool) and not vv]
            if false_keys:
                failing[k] = {kk: False for kk in false_keys}
    logger.warning("部分组件健康检查未通过（不影响启动）: %s", failing or health)

# ── 创建女友管理器 ──
user_mgr = UserManager(orchestrator)

# ── 启动 WebSocket 服务器（主动消息 websocket 通道） ──
_ws_holder: dict[str, WebSocketServer | None] = {}
if HAS_WEBSOCKETS:
    _ws_port = getattr(cfg.api, "websocket_port", 8765)

    def _run_ws_server(holder: dict[str, WebSocketServer | None] = _ws_holder, port: int = _ws_port) -> None:
        ws_server = WebSocketServer(orchestrator=orchestrator, port=port)
        holder["ws"] = ws_server
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ws_server.start())
        finally:
            try:
                loop.run_until_complete(ws_server.stop())
            except Exception as e:  # noqa: BLE001
                logger.debug("WebSocket 服务器关闭时异常: %s", e)
            try:
                loop.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("WebSocket 事件循环关闭时异常: %s", e)

    _ws_thread = threading.Thread(target=_run_ws_server, daemon=True)
    _ws_thread.start()
    logger.info("WebSocket 服务器线程已启动，端口 %d", _ws_port)

    @atexit.register
    def _stop_ws_server() -> None:
        ws_server = _ws_holder.get("ws")
        loop = _ws_holder.get("loop")
        if ws_server is None:
            return
        if loop is None or loop.is_closed():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(ws_server.stop(), loop)
            future.result(timeout=5)
        except Exception as e:  # noqa: BLE001
            logger.debug("WebSocket 服务器关闭时异常: %s", e)

# ── 向主动消息调度器注册 websocket / wechat 通道 ──
# 多 worker 单例性保护：uvicorn --workers N 启动 N 个进程，每个都执行
# orchestrator.initialize() → _init_ase_and_scheduler → scheduler.start()。
# flock 文件锁确保只有 master worker 持有调度器，其他 worker 停止调度器。
# DISABLE_SCHEDULER=1 环境变量等价 --no-scheduler，供 uvicorn 直接启动场景使用。
def _ensure_scheduler_singleton() -> None:
    """确保多 worker 场景下只有一个调度器运行。

    逻辑：
    1. 若 DISABLE_SCHEDULER=1 → 停止调度器（等价 --no-scheduler）
    2. 否则用 flock 文件锁竞争 master worker：
       - 获得锁 → master worker，保持调度器运行
       - 未获得锁 → 非 master worker，停止调度器避免重复运行
    """
    scheduler = orchestrator.components.get("scheduler")
    if scheduler is None:
        return  # _init_mixin 启动失败，无需处理

    # 情况 1：环境变量显式禁用
    if os.environ.get("DISABLE_SCHEDULER", "").lower() in ("1", "true", "yes"):
        if hasattr(scheduler, "stop"):
            scheduler.stop()
        orchestrator.components["scheduler"] = None
        logger.info("调度器已禁用 (DISABLE_SCHEDULER=1)")
        return

    # 情况 2：多 worker flock 单例保护
    try:
        import fcntl
        lock_path = "/tmp/ai-girlfriend-scheduler.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # 其他 worker 已持有锁，本 worker 停止调度器
            if hasattr(scheduler, "stop"):
                scheduler.stop()
            orchestrator.components["scheduler"] = None
            os.close(lock_fd)
            logger.info("其他 worker 已持有调度器锁，本 worker 停止调度器")
            return
        # 持有锁直到进程退出（不释放，进程退出时自动释放）
        logger.info("本 worker 持有调度器锁（master worker）")
    except ImportError:
        # Windows 无 fcntl，开发模式单 worker 无锁竞争
        logger.debug("fcntl 不可用（Windows 开发模式），跳过调度器单例保护")
    except Exception as e:  # noqa: BLE001
        logger.warning("调度器单例保护检查失败: %s", e)


_ensure_scheduler_singleton()

# ── 向调度器注册通道（仅 master worker 的调度器存活时执行） ──
_scheduler = orchestrator.components.get("scheduler")
if _scheduler is not None:
    def _websocket_sender_factory(holder: dict[str, WebSocketServer | None] = _ws_holder):
        ws_server = holder.get("ws")
        if ws_server is None:
            return None
        return ws_server.broadcast_proactive

    _scheduler.register_channel("websocket", _websocket_sender_factory)

    def _wechat_sender_factory():
        try:
            from wechat_direct import get_connector
            connector = get_connector()
        except Exception:  # noqa: BLE001
            return None
        if connector is None or not getattr(connector, "token", ""):
            return None

        async def _send(msg: str) -> None:
            connector.send_text(msg)

        return _send

    _scheduler.register_channel("wechat", _wechat_sender_factory)
    logger.info("已向主动消息调度器注册 websocket/wechat 通道")

# ── 自动恢复微信连接（如果存在持久化凭证） ──
# 启动时若 ~/.weixin_cow_credentials.json 存在，则自动启动 connector.run() 恢复消息轮询，
# 避免服务重启后 wechat_state.json 仍显示 connected:true 但轮询线程未启动。
# uvicorn --workers 4 启动 4 个进程，flock 文件锁确保只有一个 worker 启动 connector。
def _autostart_wechat_connector():
    """若 ~/.weixin_cow_credentials.json 存在，自动启动微信连接器恢复消息轮询。

    使用 flock 文件锁确保 4 个 uvicorn worker 中只有一个启动 connector，
    避免多进程同时轮询导致消息重复处理。
    """
    try:
        import fcntl

        from wechat_direct import WeChatConnector
        from wechat_direct.wechat_connector import CREDENTIALS_PATH

        if not os.path.exists(CREDENTIALS_PATH):
            logger.info("微信凭证不存在，跳过自动连接（需用户扫码登录）")
            return

        # 文件锁：确保只有一个 worker 进程启动 connector
        lock_path = "/tmp/ai-girlfriend-wechat-autostart.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # 其他 worker 已持有锁，本 worker 跳过自动连接
            logger.info("其他 worker 已持有微信连接锁，本 worker 跳过自动连接")
            os.close(lock_fd)
            return
        # 持有锁直到进程退出（不释放，进程退出时自动释放）
        logger.info("检测到微信凭证，自动恢复连接（本 worker 持有锁）...")

        def _do_autostart():
            try:
                connector = WeChatConnector(user_mgr)
                connector.run()
            except Exception as e:  # noqa: BLE001
                logger.exception("微信自动连接失败: %s", e)

        t = threading.Thread(target=_do_autostart, daemon=True, name="wx_autostart")
        t.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("微信自动连接检查失败: %s", e)


_autostart_wechat_connector()

# ── 数据库初始化 + 预加载微信绑定（FastAPI lifespan） ──

async def _init_and_preload():
    """确保数据库表存在，并将微信绑定加载到 UserManager 缓存"""
    await init_db()
    async with _async_session() as session:
        result = await session.execute(select(WechatBinding))
        bindings = result.scalars().all()
        binding_dicts = [
            {
                "wxid": b.wxid,
                "user_id": b.user_id,
                "nickname": b.nickname,
                "character_card_id": b.character_card_id,
            }
            for b in bindings
        ]
        await user_mgr.load_bindings(binding_dicts)
    logger.info("✅ 数据库就绪，已加载 %d 条微信绑定", len(binding_dicts))

    # 一次性数据迁移：清除已下线 provider（opencode_zen）的用户配置
    # 避免 _build_backend 抛 ValueError 导致用户聊天 500
    await _migrate_retired_providers()


async def _migrate_retired_providers():
    """清除用户 llm_config 中已下线的 provider（opencode_zen 等）。

    将这些用户的 provider 重置为 "auto"，并清空对应的 api_key/api_base/model，
    避免遗留配置导致 _build_backend 抛 ValueError。
    """
    retired_providers = {"opencode_zen"}
    try:
        async with _async_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()
            migrated = 0
            for user in users:
                cfg = user.llm_config
                if not isinstance(cfg, dict):
                    continue
                if cfg.get("provider") in retired_providers:
                    # 直接清空整个 llm_config，让用户回退到全局默认配置
                    user.llm_config = None
                    migrated += 1
            if migrated > 0:
                await session.commit()
                logger.info("✅ 已迁移 %d 个用户的过期 LLM 配置（opencode_zen → 全局默认）", migrated)
    except Exception as e:  # noqa: BLE001
        logger.warning("用户 LLM 配置迁移失败（不影响启动）: %s", e)


@asynccontextmanager
async def _app_lifespan(_app) -> AsyncGenerator[None, None]:
    """FastAPI lifespan：启动时初始化数据库 + 预加载绑定，关闭时清理"""
    await _init_and_preload()
    yield
    logger.info("🛑 API 应用关闭")


# ── 创建 FastAPI 应用（暴露 app 变量供 uvicorn 使用） ──
session_mgr = SessionManager()
app = create_api_app(
    orchestrator=orchestrator,
    health_checker=health_checker,
    config_manager=orchestrator.components.get("config"),
    session_manager=session_mgr,
    user_manager=user_mgr,
    lifespan=_app_lifespan,
)

logger.info("✅ API 应用就绪 — %d 条路由", len(app.routes))

# ── 直接运行时启动 uvicorn ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=cfg.api.host, port=cfg.api.port, log_level="info",
                reload=False, access_log=True)
    logger.info("服务器已停止，退出")
