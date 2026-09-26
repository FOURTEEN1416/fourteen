"""
健康检查路由 — /api/health & /api/ready & /api/metrics

设计要点：
- 不需要认证（无 verify_api_key_dep 依赖）：/api/health 与 /api/ready
- /api/health  返回 200 + 简要状态（轻量 liveness，适合负载均衡探针）
- /api/ready   返回 200/503 + 就绪检查（共享健康枚举 + 必需/可降级清单 +
               有限超时的真实 DB / 记忆库 / 模型配置探测 + 关键路由组存在性）
- /api/metrics Prometheus 文本格式（机器认证），多 worker 聚合读取

W10 修复：旧 readiness 用 `health_data.get('status') != 'error'` 判健康，
而 HealthChecker 只产 healthy/degraded/unhealthy —— degraded 与 unhealthy 全被
放行。现统一走 observability.health.HealthStatus 共享枚举，并对必需组件
失联 fail-closed；可降级组件降级时返回 200 + status=degraded（不装全绿）。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse, Response

from api.auth import verify_api_key_dep
from api.deps import deps
from api.runtime_config import is_production

logger = logging.getLogger("health_routes")

health_router = APIRouter(tags=["health"])

_PROJECT_ROOT = Path(__file__).parent.parent
# 记忆库路径（模块常量，测试可重定向）
MEMORY_DB_PATH = _PROJECT_ROOT / "data" / "sqlite.db"
# 真实探测的统一限时（秒）——挂死的后端不得拖死 readiness
PROBE_TIMEOUT_SECONDS = 3.0

# 可降级清单：这些组件降级时 ready 仍 200（status=degraded），其余一律 fail-closed
DEGRADABLE_COMPONENTS = frozenset({
    "model_config",     # 平台级模型未配置（纯 BYOK 部署是合法状态）
    "memory_store",     # 记忆库尚未初始化（全新安装），但存在即必须可读
    "tone_mimic",       # 语气模拟属增强能力
    "scheduler",        # 非 master worker 无调度器属正常
    "ase_engine",       # 情感引擎降级不阻断基本对话
})

# 关键路由组存在性探测：(组名, method, path)
# 这些路由组挂载失败不进 route_mounts，只有对真实路由表探测才能发现漏挂。
REQUIRED_ROUTE_PROBES: tuple[tuple[str, str, str], ...] = (
    ("auth", "POST", "/api/auth/login"),
    ("agent_plane", "GET", "/api/agent-plane/events"),
    ("llm_providers", "GET", "/api/llm-providers"),
    ("mimo_voice", "GET", "/api/mimo/status"),
    ("admin", "GET", "/api/admin/users"),
    ("invites", "GET", "/api/admin/invites"),
)


def _flatten_routes(app) -> set[tuple[str, str]]:
    """收集 (METHOD, path)（兼容 FastAPI 0.139+ _IncludedRouter 包装）。"""
    out: set[tuple[str, str]] = set()
    stack = list(getattr(app, "routes", []))
    while stack:
        r = stack.pop()
        if hasattr(r, "path") and hasattr(r, "methods"):
            for m in r.methods:
                if m != "HEAD":
                    out.add((m, r.path))
        if hasattr(r, "original_router"):
            stack.extend(getattr(r.original_router, "routes", []))
    return out


# ────────────────────────── 真实探测（全部限时） ──────────────────────────


async def _probe_users_db() -> dict:
    """users 库真实读写通路（SELECT 1，经生产同款异步引擎）。"""
    from sqlalchemy import text

    import api.database as database_mod

    engine = database_mod._engine
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"connected": True}


async def _probe_memory_store() -> dict:
    """记忆库（data/sqlite.db）可用性：只读打开并验证 schema 可查。

    文件尚未初始化（全新安装）→ degraded；存在但读不了 → 抛异常 → fail。
    """
    if not MEMORY_DB_PATH.is_file():
        return {"connected": True, "degraded": True, "detail": "memory db not initialized yet"}

    def _q() -> None:
        con = sqlite3.connect(f"{MEMORY_DB_PATH.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        finally:
            con.close()

    await asyncio.to_thread(_q)
    return {"connected": True}


async def _probe_model_config() -> dict:
    """模型配置：平台级网关已配置即 ok；未配置 → degraded（纯 BYOK 合法）。"""
    orch = deps.orch
    llm = (getattr(orch, "components", {}) or {}).get("llm") if orch is not None else None
    if llm is None:
        return {"connected": True, "degraded": True, "detail": "llm component missing"}
    hc = getattr(llm, "health_check", None)
    if not callable(hc):
        return {"connected": True}
    info = hc()
    if isinstance(info, dict) and info.get("configured") is False:
        return {"connected": True, "degraded": True, "detail": "platform llm not configured (BYOK-only?)"}
    return {"connected": True}


async def _bounded_probe(checks: dict, details: dict, name: str, factory) -> None:
    try:
        result = await asyncio.wait_for(factory(), timeout=PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        checks[name] = "fail"
        details[name] = f"probe timeout after {PROBE_TIMEOUT_SECONDS}s"
        return
    except Exception as exc:  # noqa: BLE001 — 探测失败必须显性化为 fail
        checks[name] = "fail"
        details[name] = f"{type(exc).__name__}: {exc}"[:300]
        return
    if result.get("degraded"):
        checks[name] = "degraded" if name in DEGRADABLE_COMPONENTS else "fail"
        details[name] = str(result.get("detail", "degraded"))
    elif result.get("connected", result.get("available", False)):
        checks[name] = "ok"
    else:
        checks[name] = "fail"
        details[name] = str(result.get("error", "probe reported not connected"))[:300]


# ────────────────────────── 路由 ──────────────────────────


@health_router.get("/api/health")
async def health_check():
    """系统健康检查 — 轻量 liveness 探针，不需要认证。

    返回 200 表示进程存活。组件级就绪状态用 /api/ready（其失败不影响本端点语义）。
    """
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": "unique-you-api",
        "version": "3.1.0",
        "environment": "production" if is_production() else "development",
    })


@health_router.get("/api/ready")
async def readiness_check(request: Request):
    """准备就绪检查 — 共享健康枚举 + 必需/可降级清单 + 真实探测。

    - 200 + status=ready      全部必需组件 ok 且无可降级降级
    - 200 + status=degraded   仅可降级清单内组件降级（明确标注，不装全绿）
    - 503 + status=not_ready  任一必需组件 fail（fail-closed，不接流量）
    不需要认证。
    """
    checks: dict[str, str] = {}
    details: dict[str, str] = {}

    # ── orchestrator 组件级检查（非 None 即在位；真实探测见下） ──
    orch = deps.orch
    checks["orchestrator"] = "ok" if orch is not None else "fail"
    if orch is not None:
        components = getattr(orch, "components", {}) or {}
        if components:
            for comp in ("llm", "memory", "emotion", "persona"):
                checks[comp] = "ok" if components.get(comp) is not None else "fail"
        elif hasattr(orch, "_initialized"):
            checks["orchestrator_initialized"] = "ok" if getattr(orch, "_initialized", False) else "fail"

    # ── 真实探测（users DB / 记忆库 / 模型配置，全部限时并发） ──
    await asyncio.gather(
        _bounded_probe(checks, details, "db_users", _probe_users_db),
        _bounded_probe(checks, details, "memory_store", _probe_memory_store),
        _bounded_probe(checks, details, "model_config", _probe_model_config),
    )

    # ── 关键路由组存在性（对真实路由表探测，漏挂即 503） ──
    mounted = _flatten_routes(request.app)
    for group, method, path in REQUIRED_ROUTE_PROBES:
        checks[f"routes_{group}"] = "ok" if (method, path) in mounted else "fail"

    # 控制端核心能力必须全部注册。管理器暂不可用可由端点返回 503，
    # 但路由本身静默缺失（404）属于未就绪。
    for route_group, is_mounted in deps.route_mounts.items():
        checks[f"routes_{route_group}"] = "ok" if is_mounted else "fail"

    # ── 健康检查器（共享枚举；限时） ──
    if deps.health is not None:
        try:
            checker = deps.health
            probe = getattr(checker, "async_check", None)
            if not callable(probe):
                probe = checker.check
            health_data = await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
            _absorb_health_checker(checks, details, health_data)
        except asyncio.TimeoutError:
            checks["health_checker"] = "fail"
            details["health_checker"] = f"health check timeout after {PROBE_TIMEOUT_SECONDS}s"
        except Exception as exc:  # noqa: BLE001
            checks["health_checker"] = "fail"
            details["health_checker"] = f"{type(exc).__name__}: {exc}"[:300]

    # ── 汇总：fail → 503；degraded（仅可降级清单）→ 200 degraded；否则 ready ──
    has_fail = any(v == "fail" for v in checks.values())
    has_degraded = any(v == "degraded" for v in checks.values())
    status_code = 503 if has_fail else 200
    overall = "not_ready" if has_fail else ("degraded" if has_degraded else "ready")

    return JSONResponse({
        "status": overall,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "service": "unique-you-api",
        "version": "3.1.0",
        "environment": "production" if is_production() else "development",
        "checks": {
            k: ({"status": v, "detail": details[k]} if k in details else {"status": v})
            for k, v in checks.items()
        },
    }, status_code=status_code)


def _absorb_health_checker(checks: dict[str, str], details: dict[str, str], health_data: dict) -> None:
    """把 HealthChecker 结果并入 readiness 判定（共享枚举口径）。

    - healthy → ok
    - degraded → 逐个归因：可降级清单内的组件降级记 degraded；
      归因不了（无明细）或必需组件降级 → fail（fail-closed）
    - unhealthy / 未知值 → fail
    """
    from observability.health import HealthStatus, normalize_status

    overall = normalize_status(health_data.get("status"))
    component_results: dict = health_data.get("checks", {}) or {}
    if overall == HealthStatus.HEALTHY.value:
        checks["health_checker"] = "ok"
        return
    if overall != HealthStatus.DEGRADED.value:
        checks["health_checker"] = "fail"
        details["health_checker"] = f"health checker overall={overall}"
        return
    degraded_names = [
        name for name, result in component_results.items()
        if isinstance(result, dict) and (
            not result.get("connected", result.get("available", True)) or result.get("degraded")
        )
    ]
    unattributed = [n for n in degraded_names if n not in DEGRADABLE_COMPONENTS]
    if unattributed or not degraded_names:
        checks["health_checker"] = "fail"
        details["health_checker"] = f"required components degraded: {sorted(unattributed) or 'unattributed'}"
        return
    for n in degraded_names:
        checks[f"health_{n}"] = "degraded"
        details[f"health_{n}"] = str(component_results[n].get("error", "degraded"))[:200]


@health_router.get("/api/metrics")
async def prometheus_metrics(_auth: bool = Security(verify_api_key_dep)):
    """Prometheus 文本格式指标（多 worker 聚合）。

    需要认证（API Key / JWT）：内网 Prometheus 抓取时配置对应 header。
    多 worker 场景经由共享 multiproc 目录汇总，不争固定端口。
    """
    from observability.metrics import collect_metrics_text

    return Response(
        content=collect_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
