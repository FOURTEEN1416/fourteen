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
from utils import session_key as session_key_mod  # noqa: E402

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
_ws_holder: dict[str, object] = {}
if HAS_WEBSOCKETS:
    _ws_port = getattr(cfg.api, "websocket_port", 8765)

    def _run_ws_server(holder: dict[str, object] = _ws_holder, port: int = _ws_port) -> None:
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
        if not isinstance(ws_server, WebSocketServer) or not isinstance(loop, asyncio.AbstractEventLoop):
            return
        if loop.is_closed():
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
        import fcntl  # type: ignore[import-not-found,attr-defined]  # POSIX-only（Windows 分支见 except）

        lock_path = "/tmp/ai-girlfriend-scheduler.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined,union-attr]
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
    def _websocket_sender_factory(holder: dict[str, object] = _ws_holder):
        ws_server = holder.get("ws")
        if not isinstance(ws_server, WebSocketServer):
            return None

        async def _send(msg: str, session_key: str | None = None) -> None:
            # 2026-09-22：带 session_key 的调用**定向**（web 会话主动消息），
            # 0 送达抛异常由调度器判失败；无 session_key 的系统级消息保留广播。
            if session_key:
                delivered = await ws_server.send_proactive_to_session(session_key, msg)
                if delivered == 0:
                    raise RuntimeError(
                        f"websocket 定向投递未送达任何归属连接（session={session_key}）"
                    )
                return
            await ws_server.broadcast_proactive(msg)

        return _send

    _scheduler.register_channel("websocket", _websocket_sender_factory)
    # P1-23：ws 连接/锁属 _run_ws 线程的事件循环——调度线程 _deliver 必须
    # 经 run_coroutine_threadsafe 桥到该循环，直接 asyncio.run 为跨循环未定义行为
    if hasattr(_scheduler, "set_delivery_loop"):
        _scheduler.set_delivery_loop(lambda: _ws_holder.get("loop"))
    try:
        _llm = orchestrator.components.get("llm")
        if _llm is not None and hasattr(_scheduler, "set_llm_provider"):
            _scheduler.set_llm_provider(_llm)
            logger.info("主动消息 LLM 决策已注入调度器")
    except Exception as e:  # noqa: BLE001
        logger.warning("set_llm_provider failed: %s", e)

    def _wechat_sender_factory():
        try:
            from wechat_direct.connector_registry import get_registry
        except Exception:  # noqa: BLE001
            return None
        registry = get_registry()
        if registry.online_count() == 0:
            return None

        async def _send(msg: str, session_key: str | None = None) -> None:
            # 2026-09-21 P1：session_key=`owner:peer@im.wechat` 时定向投递；
            # 禁止把 A 的主动消息广播给所有通道用户。
            from wechat_direct.connector_registry import get_registry

            registry = get_registry()
            sent_any = False
            failures: list[str] = []

            def _peers_for_owner(owner_id: int) -> list[str]:
                peers: list[str] = []
                if not user_mgr:
                    return peers
                for key in user_mgr.get_bound_wxids():
                    parsed = session_key_mod.parse(key)
                    if parsed.owner is not None and parsed.owner == owner_id:
                        peers.append(parsed.peer)
                return peers

            targets: list[tuple[int, str]] = []
            sk = str(session_key or "")
            # 判据唯一真源 `utils/session_key`：旧实现「含 `:` 且左段是数字」会把
            # **web 键 `N:web:hex`** 也当成微信（peer 变成 `web:hex`）→
            # 向不存在的 wxid 发送并静默失败。现先判定是否微信键。
            if sk and session_key_mod.is_wechat_key(sk):
                parsed = session_key_mod.parse(sk)
                if parsed.owner is not None and parsed.peer:
                    targets.append((parsed.owner, parsed.peer))
            if sk and not targets:
                # P0-4-3：给了 session_key 却解析不出 owner:peer → 拒发。
                # 旧实现让坏键掉进"全员兜底"分支，A 的私信变广播。
                raise RuntimeError(f"微信投递拒绝：session_key 无法解析为 owner:peer（{sk}）")
            if not targets:
                # 兼容旧广播路径（仅 session_key 为空的系统级消息）：
                # 仍只投递各 owner 自己绑定的 peer，不跨 owner
                for owner_id, _slot, conn in registry.all():
                    if not getattr(conn, "token", ""):
                        continue
                    for peer in _peers_for_owner(int(owner_id)):
                        targets.append((int(owner_id), peer))

            for owner_id, peer in targets:
                delivered = False
                for uid, _slot, conn in registry.all():
                    if int(uid) != int(owner_id):
                        continue
                    if getattr(conn, "token", "") and conn.send_text(msg, to_user=peer):
                        delivered = True
                        sent_any = True
                if not delivered:
                    failures.append(f"{owner_id}:{peer}")

            if not sent_any:
                raise RuntimeError(
                    f"微信投递失败：无任何用户通道送达 session={session_key} failures={failures[:5]}"
                )
            if failures:
                logger.warning("微信部分投递失败: %s", failures[:10])

        return _send

    _scheduler.register_channel("wechat", _wechat_sender_factory)
    logger.info("已向主动消息调度器注册 websocket/wechat 通道")

    # ── 提醒到期投递任务（每分钟轮询；豁免静默时段；定向投递到发起会话）──
    # 2026-09-20：修复「六点叫起床」事故——旧提醒链路只写库不触发（无轮询）、
    # 关键词裁决漏检意图、SQL UTC 与北京时间差 8 小时、投递目标缺失。
    def _install_reminder_delivery() -> None:
        from proactive.reminder_delivery import ReminderDeliveryTask

        sm = getattr(orchestrator.components.get("memory"), "structured_memory", None)
        if sm is None:
            logger.warning("提醒投递未装配：structured_memory 不可用")
            return
        registry_holder: dict[str, object] = {}

        def _wechat_send(owner_id: int, peer: str, text: str) -> bool:
            from wechat_direct.connector_registry import get_registry

            registry = registry_holder.get("r") or get_registry()
            registry_holder["r"] = registry
            for uid, _slot, conn in registry.all():
                if (
                    uid == owner_id
                    and getattr(conn, "token", "")
                    and conn.send_text(text, to_user=peer)
                ):
                    return True
            # 诊断留痕：通道空/token 空导致的投递失败必须可与 send 失败区分
            snapshot = [
                (uid, slot, bool(getattr(conn, "token", "")))
                for uid, slot, conn in registry.all()
            ]
            logger.warning(
                "[reminder] 微信定向投递未命中可用通道 owner=%s registry=%s",
                owner_id, snapshot,
            )
            return False

        async def _ws_send(session_key: str, text: str) -> bool:
            ws_server = _ws_holder.get("ws")
            if not isinstance(ws_server, WebSocketServer):
                return False
            try:
                # P0-6: 必须 await——旧实现同步调用只创建协程对象即 return True
                #（协程从未执行），使 web 提醒假送达。
                # 2026-09-22: 从 broadcast 收口为**定向**（按会话键），广播会把
                # A 的提醒推给所有打开控制台的连接。
                delivered = await ws_server.send_proactive_to_session(session_key, text)
                return delivered > 0
            except Exception as e:  # noqa: BLE001
                logger.warning("提醒 websocket 定向投递失败 session=%s: %s", session_key, e)
                return False

        def _character_resolver(session_key: str) -> str:
            # 2026-09-22：多用户各绑不同角色——文案口吻按会话归属解析，
            # 旧实现装配时取全局单值 current_character_name（绑错角色口吻）。
            # 2026-09-22 块C 二次根治：旧实现以 `if not char_id` 判断"未绑定"，
            # 但未绑定用户的 id 恰为内置默认角色 "default"（非空真值）→ 走
            # get_card("default") 取不到文件卡 → 静默回落全局 current_character_name
            # （A 的提醒用 B 的角色口吻）。现统一走 utils.character_resolver
            # 唯一 owner，解析失败由它兜底为内置角色，**不再回落全局单值**。
            from utils import character_resolver as _cr

            char_id = _cr.resolve_character_id(session_key, user_mgr)
            return _cr.display_name(char_id)

        persona = orchestrator.components.get("persona")
        character_name = getattr(persona, "current_character_name", "") or ""
        task = ReminderDeliveryTask(
            sm,
            llm=orchestrator.components.get("llm"),
            wechat_sender=_wechat_send,
            ws_sender=_ws_send,
            character_name=str(character_name),
            memory=orchestrator.components.get("memory"),
            character_resolver=_character_resolver,
        )
        _scheduler.register_reminder_task(task)
        logger.info("提醒到期投递任务已装配（每分钟轮询，豁免静默时段）")

    try:
        _install_reminder_delivery()
    except Exception as e:  # noqa: BLE001
        logger.warning("提醒投递装配失败（不影响其他通道）: %s", e)

# ── 自动恢复微信连接（每人独立通道） ──
# 2026-09-19：不再读全局 ~/.weixin_cow_credentials.json 作为用户通道真源。
# 只恢复 data/wechat_sessions/<user_id>/slotN/ 下已有凭证的通道；
# 遗留全局凭证若存在，一次性迁移到 admin（用户裁决：是）。
def _autostart_wechat_connector():
    """恢复 per-user 微信通道轮询；多 worker 时按 user 会话目录文件锁去重。"""
    try:
        from scripts.migrate_legacy_wechat_channel import migrate_legacy_if_needed
        from wechat_direct import channel_paths
        from wechat_direct.connector_registry import get_registry

        try:
            migrate_legacy_if_needed()
        except Exception as e:  # noqa: BLE001
            logger.warning("遗留微信凭证迁移检查失败（忽略）: %s", e)

        if channel_paths.count_sessions_with_credentials() == 0:
            logger.info("无用户微信通道凭证，跳过自动连接（需用户各自扫码）")
            return

        logger.info("检测到用户微信通道凭证，开始恢复...")
        restored = get_registry().restore_on_boot(user_manager=user_mgr)
        logger.info("微信通道自动恢复完成 count=%s", restored)
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
        # 好友自选角色偏好 → 缓存键 pref:owner:peer
        try:
            from api.database import WechatPeerPreference

            pref_result = await session.execute(select(WechatPeerPreference))
            for p in pref_result.scalars().all():
                key = f"pref:{p.owner_user_id}:{p.peer_wxid}"
                await user_mgr.upsert_binding(
                    key,
                    {
                        "wxid": p.peer_wxid,
                        "user_id": p.owner_user_id,
                        "character_card_id": p.character_card_id,
                    },
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("加载微信好友角色偏好失败（忽略）: %s", e)
        # 磁盘通道会话 → DB（复核修复：凭证在磁盘但表空）
        try:
            from scripts.migrate_legacy_wechat_channel import sync_disk_sessions_to_db

            synced = await sync_disk_sessions_to_db()
            if synced:
                logger.info("已同步 %d 条微信通道会话到数据库", synced)
        except Exception as e:  # noqa: BLE001
            logger.warning("同步微信通道会话到 DB 失败（忽略）: %s", e)
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
