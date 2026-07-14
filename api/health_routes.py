"""
健康检查路由 — /api/health & /api/ready

设计要点：
- 不需要认证（无 verify_api_key_dep 依赖）
- /api/health  返回 200 + 简要状态（轻量，适合负载均衡探针）
- /api/ready   返回 200/503 + 就绪检查（含 orchestrator 组件状态）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.deps import deps
from api.runtime_config import is_production

logger = logging.getLogger("health_routes")

health_router = APIRouter(tags=["health"])


@health_router.get("/api/health")
async def health_check():
    """系统健康检查 — 轻量探针，不需要认证。

    返回 200 表示进程存活。如需组件级状态，使用 /api/ready。
    """
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": "unique-you-api",
        "version": "3.1.0",
        "environment": "production" if is_production() else "development",
    })


@health_router.get("/api/ready")
async def readiness_check():
    """准备就绪检查 — 包含 orchestrator 组件状态。

    返回 200 表示系统就绪可接受流量，503 表示未就绪。
    不需要认证。
    """
    checks: dict[str, bool] = {
        "auth_configured": True,
        "cors_configured": True,
    }

    # ── orchestrator 组件级检查 ──
    orch = deps.orch
    if orch is not None:
        checks["orchestrator"] = True
        components = getattr(orch, "components", {})
        if components:
            checks["llm"] = components.get("llm") is not None
            checks["memory"] = components.get("memory") is not None
            checks["emotion"] = components.get("emotion") is not None
            checks["persona"] = components.get("persona") is not None
        elif hasattr(orch, "_initialized"):
            checks["orchestrator_initialized"] = bool(getattr(orch, "_initialized", False))
    else:
        checks["orchestrator"] = False

    # ── 健康检查器（如果有） ──
    if deps.health is not None:
        try:
            health_data = deps.health.check()
            checks["health_checker"] = health_data.get("status") != "error"
        except Exception:
            checks["health_checker"] = False

    overall_ready = all(checks.values())
    status_code = 200 if overall_ready else 503

    return JSONResponse({
        "status": "ready" if overall_ready else "not_ready",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": "unique-you-api",
        "version": "3.1.0",
        "environment": "production" if is_production() else "development",
        "checks": {k: {"status": "ok" if v else "fail"} for k, v in checks.items()},
    }, status_code=status_code)
