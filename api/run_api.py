"""
十四 API-Only 启动入口

用法:
    uvicorn api.run_api:app --host 0.0.0.0 --port 8000 --reload

或直接运行:
    python api/run_api.py
"""
from __future__ import annotations

import atexit
import logging
import sys
from pathlib import Path

# 确保项目根在 sys.path
_project_root = Path(__file__).parent.parent.absolute()
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api.app_factory import create_api_app  # noqa: E402
from api.session_manager import SessionManager  # noqa: E402
from main import GirlfriendManager, OptimizedOrchestrator  # noqa: E402
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
    logger.warning("部分组件健康检查未通过（不影响启动）: %s",
                   {k: v for k, v in health.items() if not v})

# ── 创建女友管理器 ──
girlfriend_mgr = GirlfriendManager(orchestrator)

# ── 创建 FastAPI 应用（暴露 app 变量供 uvicorn 使用） ──
session_mgr = SessionManager()
app = create_api_app(
    orchestrator=orchestrator,
    health_checker=health_checker,
    config_manager=orchestrator.components.get("config"),
    session_manager=session_mgr,
    girlfriend_manager=girlfriend_mgr,
)

logger.info("✅ API 应用就绪 — %d 条路由", len(app.routes))

# ── 直接运行时启动 uvicorn ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=cfg.api.host, port=cfg.api.port, log_level="info",
                reload=False, access_log=True)
    logger.info("服务器已停止，退出")
