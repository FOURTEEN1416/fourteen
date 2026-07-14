"""
杂项路由 — 系统查询/管理/看板/通道列表

来源：原 api.main_routes.py L144/152/191/344/387/395/431/442/935/985/1307 共 10 端点

依赖：
- deps.health / deps.training_mgr / deps.config / deps.sessions
- 共享常量/Helper/ConfigUpdateRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user, get_current_user_id, require_role
from api.database import User
from api.deps import deps
from api.main_routes import ConfigUpdateRequest, _sanitize_config
from observability.logging_setup import ring_buffer

logger = logging.getLogger("api.routers.misc_routes")

router = APIRouter(tags=["misc"])


# ═══════════════════════════════════════════════════════
# Stats / Dashboard
# 健康检查路由 (/api/health, /api/ready) 已迁移至 api/health_routes.py
# ═══════════════════════════════════════════════════════


@router.get("/api/stats")
async def stats(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    sessions = deps.sessions
    stats_data: dict[str, Any] = {"status": "ok"}
    if orch:
        stats_data["has_orchestrator"] = True
        if orch._emotion:
            stats_data["emotion"] = orch._emotion.health_check()
        if orch._memory:
            stats_data["working_count"] = orch._memory.working.count()
        if sessions:
            stats_data["active_sessions"] = sessions.active_count
    return stats_data


@router.get("/api/stats/dashboard")
async def get_dashboard_stats(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    emotion_current = "-"
    affinity = 0
    energy = 0
    chats_today = 0
    facts_count = 0
    sys_status = "unknown"
    uptime = 0

    training_state = deps.training_mgr.get_state()
    training_info = {
        "status": training_state["status"],
        "progress": training_state["progress"],
        "loss": training_state["loss"],
        "extracted_turns": training_state["extracted_turns"],
        "cleaned_turns": training_state.get("cleaned_turns", 0),
    }

    now = time.time()
    cache = deps.wechat_status_cache
    wechat_info: dict[str, Any] = {"connected": False}
    if cache["data"] is not None and (now - cache["ts"]) < deps.WECHAT_STATUS_TTL:
        wechat_info = cache["data"]
    else:
        try:
            from api._chat_routes import get_wechat_status
            result = await get_wechat_status()  # type: ignore[func-returns-value]
            if isinstance(result, dict):
                wechat_info = result
        except Exception as e:
            logger.debug("Failed to get wechat status for dashboard: %s", e)
        cache["data"] = wechat_info
        cache["ts"] = now

    try:
        if orch:
            emotion = (orch.components.get("emotion") if hasattr(orch, "components")
                       else getattr(orch, "_emotion", None))
            memory = (orch.components.get("memory") if hasattr(orch, "components")
                      else getattr(orch, "_memory", None))
            if emotion:
                es = emotion.state
                emotion_current = es.primary_emotion.value if hasattr(es.primary_emotion, "value") else str(es.primary_emotion)
                affinity = getattr(es, "affinity", 0)
                energy = getattr(es, "energy", 0)
            if memory:
                try:
                    structured = getattr(memory, "structured_memory", None)
                    if structured:
                        chats_today = structured.count_chats_today()
                        if hasattr(structured, "count_facts"):
                            facts_count = structured.count_facts()
                except Exception as e:
                    logger.debug("Failed to get memory stats for dashboard: %s", e)
    except Exception as e:
        logger.debug("Failed to get emotion/memory stats for dashboard: %s", e)

    if deps.health:
        try:
            health_data = deps.health.check()
            sys_status = health_data.get("status", "unknown")
            uptime = health_data.get("uptime_seconds", 0)
        except Exception as e:
            logger.debug("Failed to get health check for dashboard: %s", e)

    return {
        "today_chats": chats_today,
        "recent_memories": facts_count,
        "affinity": affinity,
        "energy": energy,
        "current_emotion": emotion_current,
        "system_status": sys_status,
        "uptime_seconds": uptime,
        "wechat_connected": wechat_info.get("connected", False),
        "wechat": wechat_info,
        "training": training_info,
    }


# ═══════════════════════════════════════════════════════
# Memory Facts
# ═══════════════════════════════════════════════════════


@router.get("/api/memory/facts")
async def memory_facts(
    category: str | None = None,
    limit: int = Query(default=50),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if orch and orch._memory:
        return {"facts": orch._memory.semantic.get_facts(category, limit=limit)}
    return {"facts": []}


# ═══════════════════════════════════════════════════════
# Logs (admin only)
# ═══════════════════════════════════════════════════════


def _logs_user_id(current_user: User) -> int | None:
    """管理员可查看全量日志，普通用户只能查看本账号相关日志。"""
    return None if current_user.role == "admin" else current_user.id


@router.get("/api/logs")
async def get_logs(
    limit: int = Query(default=100, le=200),
    level: str = Query(default="all"),
    search: str = Query(default=""),
    _auth: bool = Security(verify_api_key_dep),
    current_user: User = Security(get_current_user),
):
    # 非管理员仅返回本账号相关的日志条目（依赖 UserContextMiddleware 注入 user_id）
    return {
        "logs": ring_buffer.get_recent(
            limit=limit,
            level=level,
            search=search,
            user_id=_logs_user_id(current_user),
        )
    }


@router.get("/api/logs/stream")
async def stream_logs(
    _auth: bool = Security(verify_api_key_dep),
    current_user: User = Security(get_current_user),
):
    user_id = _logs_user_id(current_user)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_event_loop()

        log_queue_handler = logging.Handler()
        log_queue_handler.setLevel(logging.INFO)

        def emit(record):
            try:
                # 非管理员只推送属于本账号的日志
                if user_id is not None and getattr(record, "user_id", None) != user_id:
                    return
                msg = log_queue_handler.format(record)
                asyncio.run_coroutine_threadsafe(queue.put(msg), loop)
            except Exception as e:
                logger.debug("Failed to emit log to SSE queue: %s", e)

        log_queue_handler.emit = emit

        root_logger = logging.getLogger()
        root_logger.addHandler(log_queue_handler)

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield "data: {}\n\n"
        finally:
            root_logger.removeHandler(log_queue_handler)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════════════════
# Config (read for all authenticated users; write admin only)
# ═══════════════════════════════════════════════════════


@router.get("/api/config")
async def get_config(
    _auth: bool = Security(verify_api_key_dep),
    _user: int = Security(get_current_user_id),
):
    cfg = deps.config
    if cfg:
        return _sanitize_config(cfg.config.model_dump())
    return {}


@router.post("/api/config")
async def save_config(
    req: ConfigUpdateRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    cfg = deps.config
    if not cfg:
        raise HTTPException(503, "Config manager not initialized")
    try:
        updated = cfg.save(req.config)
        return _sanitize_config(updated.model_dump())
    except (ValueError, TypeError, KeyError, AttributeError):
        logger.exception("Config save failed")
        raise HTTPException(400, "Invalid config") from None


# ═══════════════════════════════════════════════════════
# Channels list (mixed web/api/wechat + active sessions)
# ═══════════════════════════════════════════════════════


@router.get("/api/channels")
async def list_channels(_auth: bool = Security(verify_api_key_dep)):
    sessions = deps.sessions
    channels = [
        {"id": "web", "name": "Web 控制台", "type": "web", "status": "connected", "desc": "当前浏览器 WebSocket", "meta": "在线"},
        {"id": "api", "name": "REST API", "type": "api", "status": "connected", "desc": "HTTP API 接口", "meta": "端口 8000"},
    ]
    try:
        from wechat_direct import get_connector
        conn = get_connector()
        if conn and conn.token:
            uptime = time.time() - conn.started_at if conn.started_at else 0
            channels.append({
                "id": "wechat", "name": "个人微信", "type": "wechat",
                "status": "connected", "desc": "直接微信连接", "meta": f"在线 {uptime:.0f}s",
            })
        else:
            channels.append({
                "id": "wechat", "name": "个人微信", "type": "wechat",
                "status": "disconnected", "desc": "直接微信连接", "meta": "",
            })
    except ImportError as e:
        logger.debug("wechat_direct module not available, skipping WeChat channel: %s", e)
    if sessions:
        active = sessions.get_active_sessions()
        for ses_id in active:
            ses_data = sessions.get_session(ses_id)
            if not ses_data:
                continue
            ch_type = ses_data.get("channel", ses_data.get("channel_type", "unknown"))
            if ch_type not in [c["id"] for c in channels]:
                channels.append({"id": ch_type, "name": ch_type.capitalize(),
                                 "status": "connected", "desc": f"活跃会话 {ses_id[:8]}...", "meta": "在线"})
    return {"channels": channels}


# ═══════════════════════════════════════════════════════
# Route introspection
# ═══════════════════════════════════════════════════════


@router.get("/api/routes")
async def list_routes(
    request: Request,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    routes = []
    for route in request.app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            routes.append({"path": route.path, "methods": list(route.methods)})
        elif hasattr(route, "original_router"):
            # FastAPI 0.139+ wraps included routers in _IncludedRouter
            for sub in route.original_router.routes:
                if hasattr(sub, "path") and hasattr(sub, "methods"):
                    routes.append({"path": sub.path, "methods": list(sub.methods)})
    return {"routes": routes, "total": len(routes)}
