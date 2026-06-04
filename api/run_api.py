"""
唯一的你 API-Only 启动入口

用法:
    uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload

或直接运行:
    python api/run_api.py
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
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
from api.database import WechatBinding, _async_session, init_db  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from main import UserManager, OptimizedOrchestrator  # noqa: E402
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
