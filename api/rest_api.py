from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from api.state import SafetyLogManager, ToolHistoryManager, TrainingStateManager
from observability.logging_setup import ring_buffer

logger = logging.getLogger("rest_api")

SENSITIVE_FIELDS = {"api_key", "secret", "token", "password", "encryption_key", "api_base"}


def _sanitize_config(config_dict: dict) -> dict:
    sanitized = {}
    for k, v in config_dict.items():
        if isinstance(v, dict):
            sanitized[k] = _sanitize_config(v)
        elif k.lower() in SENSITIVE_FIELDS or any(s in k.lower() for s in SENSITIVE_FIELDS):
            sanitized[k] = "****"
        else:
            sanitized[k] = v
    return sanitized

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    session_id: str = Field(default="", max_length=128)
    message_type: str = Field(default="text", pattern=r"^(text|image|voice|file)$")


class ChatResponse(BaseModel):
    reply: str
    trace_id: str = ""
    emotion: dict | None = None


class EmotionStateResponse(BaseModel):
    current_emotion: str = ""
    intensity: float = 0.0
    energy: float = 0.0
    affinity: float = 0.0


class CreateSessionRequest(BaseModel):
    user_id: str = "default"
    channel: str = "web"


class ConfigUpdateRequest(BaseModel):
    config: dict = Field(default_factory=dict, description="Partial config updates to merge")


class ProactiveConfigRequest(BaseModel):
    threshold: float | None = None
    max_daily: int | None = None
    min_interval_minutes: int | None = None
    cooldown_after_reply_minutes: int | None = None


class ToolToggleRequest(BaseModel):
    enabled: bool


def create_api_app(orchestrator=None, health_checker=None, config_manager=None,
                   session_manager=None, girlfriend_manager=None) -> FastAPI:
    _is_prod = os.environ.get("ENV", os.environ.get("APP_ENV", "")).lower() in ("prod", "production")

    # P0: 生产环境强制关闭 debug
    app = FastAPI(title="十四 AI虚拟伴侣系统API", version="2.0", debug=not _is_prod)

    # P2: CORS 策略 - 生产环境强制限制来源
    cors_origins_env = os.environ.get("API_CORS_ORIGINS", "http://localhost:5173")
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    if _is_prod and (cors_origins == ["*"] or cors_origins == ["http://localhost:5173"]):
        logger.warning("Production environment detected with default CORS - set API_CORS_ORIGINS to restrict origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    )

    # P3: 安全响应头
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        if _is_prod:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        from fastapi.responses import JSONResponse
        status_code_map = {401: "AUTH_ERROR", 429: "RATE_LIMIT", 503: "FEATURE_UNAVAILABLE", 404: "FEATURE_UNAVAILABLE", 504: "LLM_TIMEOUT", 502: "NETWORK_ERROR"}
        error_code = (exc.headers or {}).get("X-Error-Code") or status_code_map.get(exc.status_code, "UNKNOWN")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail, "error_code": error_code})

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        from fastapi.responses import JSONResponse
        if isinstance(exc, ValueError):
            return JSONResponse(status_code=400, content={"detail": str(exc), "error_code": "VALIDATION_ERROR"})
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_code": "INTERNAL_ERROR"})

    # P0: 生产环境强制启用 API 认证
    _api_key_enabled = os.environ.get("API_KEY_ENABLED", "true" if _is_prod else "false").lower() == "true"
    if _is_prod and not os.environ.get("API_KEY"):
        logger.warning("Production environment detected without API_KEY set - authentication is enabled but no key configured")
    _api_key = os.environ.get("API_KEY", "")
    _api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def _verify_api_key(api_key: str | None = Security(_api_key_header)):
        if not _api_key_enabled:
            return True
        import hmac
        if hmac.compare_digest(api_key or "", _api_key):
            return True
        raise HTTPException(status_code=401, detail="Invalid or missing API key", headers={"X-Error-Code": "AUTH_ERROR"})

    # P2: 请求体大小限制 - 防止内存耗尽攻击
    MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB

    @app.middleware("http")
    async def request_size_limiter(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_SIZE:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body too large (max {MAX_REQUEST_SIZE // 1024 // 1024}MB)", "error_code": "REQUEST_TOO_LARGE"}
                    )
            except ValueError:
                pass
        return await call_next(request)

    # P2: 强制请求限流 - 使用内存限流器作为 SlowAPI 不可用时的回退
    if HAS_SLOWAPI:
        limiter = Limiter(key_func=get_remote_address)  # type: ignore
        app.state.limiter = limiter
    else:
        # 简易内存限流器回退方案 - 线程安全 + 自动清理
        import threading
        import time
        from collections import defaultdict
        _rate_limit_store: dict[str, list[float]] = defaultdict(list)
        _rate_limit_lock = threading.Lock()
        _rate_limit_last_cleanup = time.time()

        def _simple_rate_limit(request: Request, max_requests: int = 60, window_seconds: int = 60) -> bool:
            client_ip = request.client.host if request.client else "unknown"
            key = f"{client_ip}:{request.url.path}"
            now = time.time()

            with _rate_limit_lock:
                # 定期全局清理（每5分钟）防止内存无限增长
                global _rate_limit_last_cleanup
                if now - _rate_limit_last_cleanup > 300:  # 5 minutes
                    _cleanup_expired_records(now, window_seconds)
                    _rate_limit_last_cleanup = now

                # 清理该 key 的过期记录
                _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window_seconds]

                # 如果记录为空，删除该 key 以释放内存
                if not _rate_limit_store[key]:
                    if key in _rate_limit_store:
                        del _rate_limit_store[key]
                    return True

                if len(_rate_limit_store[key]) >= max_requests:
                    return False
                _rate_limit_store[key].append(now)
                return True

        def _cleanup_expired_records(now: float, window_seconds: int):
            """清理所有过期的限流记录，防止内存泄漏"""
            expired_keys = []
            for key, timestamps in _rate_limit_store.items():
                valid_timestamps = [t for t in timestamps if now - t < window_seconds]
                if valid_timestamps:
                    _rate_limit_store[key] = valid_timestamps
                else:
                    expired_keys.append(key)
            for key in expired_keys:
                del _rate_limit_store[key]

        @app.middleware("http")
        async def fallback_rate_limiter(request: Request, call_next):
            if not _simple_rate_limit(request):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=429, content={"detail": "Too many requests"})
            return await call_next(request)

    _orch = orchestrator
    _health = health_checker
    _config = config_manager
    _sessions = session_manager
    _gf = girlfriend_manager  # 女友管理器（多用户核心）

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
        if not _orch:
            raise HTTPException(status_code=503, detail="Orchestrator not initialized", headers={"X-Error-Code": "FEATURE_UNAVAILABLE"})
        try:
            result = await _orch.process_message(req.message, req.session_id, req.message_type)
        except TimeoutError:
            raise HTTPException(status_code=504, detail="LLM response timeout", headers={"X-Error-Code": "LLM_TIMEOUT"})
        except ConnectionError:
            raise HTTPException(status_code=502, detail="Upstream connection error", headers={"X-Error-Code": "NETWORK_ERROR"})
        return ChatResponse(
            reply=result.get("reply", ""),
            trace_id=result.get("trace_id", ""),
            emotion=result.get("emotion"),
        )

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
        if not _orch or not hasattr(_orch, 'process_message_stream'):
            raise HTTPException(status_code=503, detail="Stream not available", headers={"X-Error-Code": "FEATURE_UNAVAILABLE"})

        async def event_generator():
            async for token in _orch.process_message_stream(req.message, req.session_id, req.message_type):  # type: ignore
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.get("/api/health")
    async def health():
        if _health:
            return _health.check()
        return {"status": "unknown"}

    @app.get("/api/stats")
    async def stats(_auth: bool = Security(_verify_api_key)):
        stats_data = {"status": "ok"}
        if _orch:
            stats_data["has_orchestrator"] = True  # type: ignore
            if _orch._emotion:
                stats_data["emotion"] = _orch._emotion.health_check()
            if _orch._memory:
                stats_data["working_count"] = _orch._memory.working.count()
            if _sessions:
                stats_data["active_sessions"] = _sessions.active_count
        return stats_data

    @app.post("/api/session")
    async def create_session(req: CreateSessionRequest, _auth: bool = Security(_verify_api_key)):
        if _sessions:
            session_id = _sessions.create_session(req.user_id, req.channel)
            if _orch and _orch._memory:
                _orch._memory.working.start_session(session_id, req.channel)
            return {"session_id": session_id}
        return {"session_id": ""}

    @app.get("/api/sessions")
    async def list_sessions(_auth: bool = Security(_verify_api_key)):
        if _sessions:
            return {
                "sessions": _sessions.get_active_sessions(),
                "active_count": _sessions.active_count,
            }
        return {"sessions": [], "active_count": 0}

    @app.get("/api/chat/history")
    async def chat_history(
        session_id: str = "",
        limit: int = Query(default=20, ge=1, le=100),
        before: int = Query(default=0, ge=0, description="Timestamp to load messages before"),
        _auth: bool = Security(_verify_api_key)
    ):
        if not _orch or not _orch._memory:
            return {"messages": []}
        # P0: 使用 session_id 过滤聊天记录
        if session_id:
            # 从结构化记忆中按 session_id 查询
            sm = getattr(_orch._memory, "structured_memory", None) or getattr(_orch._memory, "_sm", None)
            if sm and hasattr(sm, "get_connection"):
                try:
                    with sm.get_connection() as conn:
                        # 构建查询条件
                        conditions = ["session_id = ?"]
                        params = [session_id]
                        if before > 0:
                            conditions.append("created_at < ?")
                            params.append(before)
                        where_clause = " AND ".join(conditions)
                        params.append(limit)

                        rows = conn.execute(
                            f"SELECT role, content, emotion_tag, created_at FROM chat_history "
                            f"WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
                            tuple(params),
                        ).fetchall()
                        messages = [dict(r) for r in rows][::-1]
                        return {"messages": messages, "session_id": session_id}
                except Exception as e:
                    logger.warning("Failed to query chat history by session_id: %s", e)
        # 如果没有 session_id 或查询失败，返回最近的记录
        messages = _orch._memory.working.get_recent(limit)
        return {"messages": messages, "session_id": session_id}

    @app.get("/api/emotion/state", response_model=EmotionStateResponse)
    async def emotion_state(_auth: bool = Security(_verify_api_key)):
        if _orch and _orch._emotion:
            health = _orch._emotion.health_check()
            # 统一响应格式
            return EmotionStateResponse(
                current_emotion=health.get("current_emotion", ""),
                intensity=health.get("intensity", 0.0),
                energy=health.get("energy", 0.0),
                affinity=health.get("affinity", 0.0)
            )
        return EmotionStateResponse()

    @app.get("/api/emotion/trend")
    async def emotion_trend(days: int = Query(default=7, ge=1, le=30), _auth: bool = Security(_verify_api_key)):
        if not _orch or not _orch._emotion:
            return {"trend": [], "days": days}
        trend = getattr(_orch._emotion, '_emotion_history', [])
        return {"trend": trend[-days * 20:], "days": days}

    @app.get("/api/persona/profile")
    async def persona_profile(_auth: bool = Security(_verify_api_key)):
        if _orch and _orch._persona:
            return {
                "core_character": _orch._persona.profile.core_character,
                "speaking_style": _orch._persona.profile.speaking_style,
                "emotional_preference": _orch._persona.profile.emotional_preference,
            }
        return {}

    @app.get("/api/persona/evolution-log")
    async def persona_evolution_log(limit: int = Query(default=50, ge=1, le=500), _auth: bool = Security(_verify_api_key)):
        if _orch and _orch._persona:
            return {"log": _orch._persona.get_evolution_log(limit)}
        return {"log": []}

    # ═══ 用户心理画像（OCEAN+PAD人格分析） ═══

    @app.get("/api/psych/profile")
    async def psych_profile(_auth: bool = Security(_verify_api_key)):
        """获取用户心理画像（OCEAN五大人格 + PAD情感 + 风格向量）"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None:
            return {"user_id": "default", "status": "unavailable", "snapshots": 0}
        return pe.get_user_profile_summary()

    @app.get("/api/psych/snapshots")
    async def psych_snapshots(limit: int = Query(default=20, ge=1, le=200), _auth: bool = Security(_verify_api_key)):
        """获取最近的人格检测快照历史"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None:
            return {"snapshots": []}
        snaps = pe.bank.get_recent_snapshots(user_id=pe.user_id, limit=limit)
        return {"snapshots": [s.to_dict() for s in snaps]}

    @app.delete("/api/psych/profile")
    async def reset_psych_profile(_auth: bool = Security(_verify_api_key)):
        """重置用户心理画像数据"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None:
            raise HTTPException(503, "PersonaExtractor未初始化")
        ok = pe.bank.clear_user(pe.user_id)
        return {"status": "reset" if ok else "failed"}

    @app.get("/api/psych/mental-health")
    async def psych_mental_health(_auth: bool = Security(_verify_api_key)):
        """获取心理健康筛查结果（抑郁/焦虑/自伤风险/认知扭曲）"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None:
            return {"available": False}
        persona = pe.bank.get_persona(pe.user_id)
        if persona is None:
            return {"available": True, "data": None}
        return {
            "available": True,
            "mental_health": persona.mental_health,
            "cognitive": persona.cognitive,
            "liwc": persona.liwc,
            "dark_triad": persona.dark_triad,
            "hexaco": persona.hexaco,
        }

    @app.get("/api/psych/liwc")
    async def psych_liwc(_auth: bool = Security(_verify_api_key)):
        """获取 LIWC 心理语言学分析"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None or not pe.liwc:
            return {"available": False}
        persona = pe.bank.get_persona(pe.user_id)
        if persona is None or not persona.liwc:
            return {"available": True, "data": None}
        return {"available": True, "data": persona.liwc}

    @app.get("/api/memory/facts")
    async def memory_facts(category: str | None = None, limit: int = Query(default=50), _auth: bool = Security(_verify_api_key)):
        if _orch and _orch._memory:
            return {"facts": _orch._memory.semantic.get_facts(category, limit=limit)}
        return {"facts": []}

    @app.get("/api/tools")
    async def tools_list(_auth: bool = Security(_verify_api_key)):
        if _orch and _orch._tools:
            return {"tools": _orch._tools.registry.tool_names}
        return {"tools": []}

    @app.get("/api/training/status")
    async def training_status(_auth: bool = Security(_verify_api_key)):
        """Check if training pipeline is available."""
        try:
            import clone_training  # noqa: F401
            available = True
            desc = "训练管线已就绪"
        except ImportError:
            available = False
            desc = "训练模块未安装"
        return {"available": available, "description": desc, "steps": [
            "data_extract", "style_analyze", "build_dataset", "lora_train", "export_model",
        ]}

    @app.get("/api/proactive/state")
    async def proactive_state(_auth: bool = Security(_verify_api_key)):
        if _orch and _orch._ase:
            return _orch._ase.health_check()
        return {}

    @app.get("/api/logs")
    async def get_logs(limit: int = Query(default=100, le=200), level: str = Query(default="all"),
                       search: str = Query(default=""), _auth: bool = Security(_verify_api_key)):
        return {"logs": ring_buffer.get_recent(limit=limit, level=level, search=search)}

    @app.get("/api/channels")
    async def list_channels(_auth: bool = Security(_verify_api_key)):
        channels = [
            {"id": "web", "name": "Web 控制台", "type": "web", "status": "connected", "desc": "当前浏览器 WebSocket", "meta": "在线"},
            {"id": "api", "name": "REST API", "type": "api", "status": "connected", "desc": "HTTP API 接口", "meta": "端口 8000"},
        ]
        # Add WeChat channel — 通过新连接器报告真实状态
        try:
            from wechat_direct import get_connector
            conn = get_connector()
            if conn and conn.token:
                uptime = time.time() - conn.started_at if conn.started_at else 0
                channels.append({
                    "id": "wechat",
                    "name": "个人微信",
                    "type": "wechat",
                    "status": "connected",
                    "desc": "直接微信连接",
                    "meta": f"在线 {uptime:.0f}s",
                })
            else:
                channels.append({
                    "id": "wechat",
                    "name": "个人微信",
                    "type": "wechat",
                    "status": "disconnected",
                    "desc": "直接微信连接",
                    "meta": "",
                })
        except ImportError as e:
            logger.debug("wechat_direct module not available, skipping WeChat channel: %s", e)
        # Add connected sessions as channels
        if _sessions:
            active = _sessions.get_active_sessions()
            for ses_id in active:
                ses_data = _sessions.get_session(ses_id)
                if not ses_data:
                    continue
                ch_type = ses_data.get("channel", ses_data.get("channel_type", "unknown"))
                if ch_type not in [c["id"] for c in channels]:
                    channels.append({"id": ch_type, "name": ch_type.capitalize(),
                                     "status": "connected", "desc": f"活跃会话 {ses_id[:8]}...", "meta": "在线"})
        return {"channels": channels}

    @app.get("/api/config")
    async def get_config(_auth: bool = Security(_verify_api_key)):
        if _config:
            return _sanitize_config(_config.config.model_dump())
        return {}

    @app.post("/api/config")
    async def save_config(req: ConfigUpdateRequest, _auth: bool = Security(_verify_api_key)):
        if not _config:
            raise HTTPException(503, "Config manager not initialized")
        try:
            updated = _config.save(req.config)
            return _sanitize_config(updated.model_dump())
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.exception("Config save failed")
            raise HTTPException(400, "Invalid config")

    @app.post("/api/proactive/config")
    async def update_proactive_config(req: ProactiveConfigRequest,
                                       _auth: bool = Security(_verify_api_key)):
        if not _orch or not _orch._ase:
            raise HTTPException(503, "Proactive engine not initialized")
        ase = _orch._ase
        if req.threshold is not None:
            ase._config["speak_threshold"] = req.threshold
        if req.max_daily is not None:
            ase._config["max_daily_messages"] = req.max_daily
        if req.min_interval_minutes is not None:
            ase._config["min_interval_minutes"] = req.min_interval_minutes
        if req.cooldown_after_reply_minutes is not None:
            ase._config["cooldown_after_reply"] = req.cooldown_after_reply_minutes
        logger.info("Proactive config updated: threshold=%s, max_daily=%s",
                     ase._config["speak_threshold"], ase._config["max_daily_messages"])
        return {"status": "ok", "config": ase._config}

    _tool_history_mgr = ToolHistoryManager(maxlen=1000)

    @app.post("/api/tools/{name}/toggle")
    async def toggle_tool(name: str, req: ToolToggleRequest,
                          _auth: bool = Security(_verify_api_key)):
        if not _orch or not _orch._tools or not _orch._tools.registry:
            raise HTTPException(503, "Tool system not initialized")
        registry = _orch._tools.registry
        tool = registry.get(name)
        if not tool:
            raise HTTPException(404, f"Tool not found: {name}")
        if req.enabled:
            registry.register(tool)
        else:
            registry.unregister(name)
        _tool_history_mgr.append({
            "timestamp": datetime.now().isoformat(),
            "tool": name, "action": "enable" if req.enabled else "disable",
        })
        logger.info("Tool '%s' toggled: enabled=%s", name, req.enabled)
        return {"status": "ok", "tool": name, "enabled": req.enabled}

    @app.get("/api/tools/history")
    async def tool_history(limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
        return {"history": _tool_history_mgr.get_recent(limit)}

    # ═══════════════════════════════════════════
    # Training / Clone Pipeline API
    # ═══════════════════════════════════════════

    _training_mgr = TrainingStateManager()

    _wechat_status_cache: dict = {"data": None, "ts": 0.0}
    _WECHAT_STATUS_TTL = 5.0

    @app.post("/api/training/extract")
    async def start_extraction(target: str = "", source: str = "wcf", _auth: bool = Security(_verify_api_key)):
        """Start data extraction from WeChat records"""
        def _do_extract():
            try:
                from weclone_adapter import WeCloneAdapter
                adapter = WeCloneAdapter(
                    data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                    output_dir=str(Path(__file__).parent.parent / "data" / "training"),
                )
                result = adapter.extract(target=target, source=source)
                _training_mgr.update(
                    status="extracted",
                    extracted_turns=len(result) if isinstance(result, list) else result.get("turns", 0),
                    progress=0.3,
                    step_name="数据提取",
                )
            except Exception:
                logger.exception("Extraction failed")
                _training_mgr.update(status="error", error="internal_error")

        if not target.strip():
            raise HTTPException(status_code=400, detail="target is required")

        _training_mgr.update(status="extracting", start_time=time.time(), step_name="数据提取")
        _training_mgr.submit(_do_extract)

        return {"status": "started", "task": "extract", "target": target}

    @app.get("/api/training/progress")
    async def get_training_progress(_auth: bool = Security(_verify_api_key)):
        """Get real-time training progress"""
        return _training_mgr.get_state()

    @app.post("/api/training/clean")
    async def start_cleaning(accept_score: int = 2, _auth: bool = Security(_verify_api_key)):
        """Start LLM Judge data cleaning"""
        def _do_clean():
            try:
                from clone_training.data_cleaner import DataCleaner
                from llm_provider import get_llm
                llm = get_llm()
                cleaner = DataCleaner(llm=llm, accept_score=accept_score)
                data_dir = Path(__file__).parent.parent / "data" / "training"
                json_files = sorted(data_dir.glob("*.jsonl"))
                if not json_files:
                    raise FileNotFoundError("No dataset found")
                latest = str(json_files[-1])
                result_path = cleaner.score_from_dataset(latest)
                cleaned_count = 0
                if result_path:
                    try:
                        with open(result_path, encoding="utf-8") as f:
                            cleaned_data = json.load(f)
                        cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
                    except Exception as e:
                        logger.debug("Failed to read cleaned data result: %s", e)
                _training_mgr.update(
                    status="cleaned",
                    cleaned_turns=cleaned_count,
                    progress=0.6,
                    step_name="数据清洗",
                )
            except Exception:
                logger.exception("Cleaning failed")
                _training_mgr.update(status="error", error="internal_error")

        _training_mgr.update(status="cleaning", start_time=time.time(), step_name="数据清洗")
        _training_mgr.submit(_do_clean)

        return {"status": "started", "task": "clean", "accept_score": accept_score}

    @app.post("/api/training/train")
    async def start_training(epochs: int = 3, lora_rank: int = 16, _auth: bool = Security(_verify_api_key)):
        """Start LoRA training"""
        def _do_train():
            try:
                from weclone_adapter import WeCloneAdapter
                adapter = WeCloneAdapter(
                    data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                    output_dir=str(Path(__file__).parent.parent / "data" / "training"),
                )

                def progress_callback(step, total, loss):
                    _training_mgr.update(
                        current_step=step,
                        total_steps=total,
                        progress=step / total if total > 0 else 0,
                        loss=loss,
                    )
                    if _training_mgr.is_stopping:
                        raise InterruptedError("Training stopped by user")

                result = adapter.train(
                    config_path="",
                    progress_callback=progress_callback,
                    epochs=epochs,
                    lora_rank=lora_rank,
                )
                _training_mgr.update(
                    status="done" if result.get("status") == "success" else "error",
                    progress=1.0,
                    step_name="模型训练",
                )
                if "lora_path" in result:
                    _training_mgr.update(lora_path=result["lora_path"])
            except InterruptedError:
                _training_mgr.update(status="stopped")
            except Exception:
                logger.exception("Training failed")
                _training_mgr.update(status="error", error="internal_error")

        _training_mgr.update(status="training", start_time=time.time(), step_name="模型训练")
        _training_mgr.submit(_do_train)

        return {"status": "started", "task": "train", "epochs": epochs}

    @app.post("/api/training/stop")
    async def stop_training(_auth: bool = Security(_verify_api_key)):
        """Stop running training — truly terminates background thread"""
        _training_mgr.stop()
        return {"status": "stopped"}

    @app.post("/api/training/test")
    async def test_clone(message: str, _auth: bool = Security(_verify_api_key)):
        """Test clone output"""
        try:
            from my_character.tone_mimic import ToneMimic
            chroma_path = str(Path(__file__).parent.parent / "data" / "chroma_db")
            mimic = ToneMimic(chroma_path=chroma_path)
            style_prompt = mimic.get_style_prompt()
            return {"message": message, "style_output": style_prompt, "status": "ok"}
        except (ImportError, OSError, ValueError):
            logger.exception("Test clone failed")
            return {"message": message, "style_output": "", "status": "error", "detail": "internal_error"}

    @app.post("/api/training/apply")
    async def apply_clone(_auth: bool = Security(_verify_api_key)):
        """Apply trained clone as active persona"""
        try:
            result_path = str(Path(__file__).parent.parent / "data" / "training")
            return {"status": "applied", "path": result_path}
        except (ValueError, OSError):
            logger.exception("Apply clone failed")
            raise HTTPException(status_code=500, detail="internal_error")

    # ═══════════════════════════════════════════
    # WeChat Channel API — 手动连接控制
    # ═══════════════════════════════════════════

    def _get_wechat_connector():
        from wechat_direct import get_connector
        return get_connector()

    _wechat_lock = threading.Lock()

    @app.post("/api/channels/wechat/connect")
    async def manual_connect_wechat(_auth: bool = Security(_verify_api_key)):
        """手动启动微信连接（扫码登录）"""
        conn = _get_wechat_connector()
        if conn and conn.token:
            return {"status": "connected", "message": "微信已连接"}

        def _do_connect():
            try:
                from wechat_direct import WeChatConnector
                connector = WeChatConnector(_orch)
                connector.run()
            except Exception as e:
                logger.exception("微信连接失败: %s", e)

        thread = threading.Thread(target=_do_connect, daemon=True)
        thread.start()

        return {"status": "connecting", "message": "微信连接已触发，请看终端/页面二维码扫码登录"}

    @app.post("/api/channels/wechat/disconnect")
    async def manual_disconnect_wechat(_auth: bool = Security(_verify_api_key)):
        """手动断开微信连接"""
        conn = _get_wechat_connector()
        if conn:
            conn.stop()
        return {"status": "disconnected", "message": "微信已断开"}

    @app.get("/api/channels/wechat/connection-status")
    async def get_wechat_connection_status(_auth: bool = Security(_verify_api_key)):
        """获取手动连接状态"""
        conn = _get_wechat_connector()
        if conn and conn.token:
            return {"status": "connected", "message": "已连接", "started_at": conn.started_at}
        return {"status": "idle", "message": "未连接"}

    @app.get("/api/channels/wechat/status")
    async def get_wechat_status(_auth: bool = Security(_verify_api_key)):
        """获取微信连接详细信息"""
        conn = _get_wechat_connector()
        if conn and conn.token:
            return {
                "connected": True,
                "uptime_seconds": time.time() - conn.started_at if conn.started_at else 0,
                "bot_id": conn.bot_id,
            }
        return {"connected": False, "uptime_seconds": 0}

    @app.post("/api/channels/wechat/reconnect")
    async def reconnect_wechat(_auth: bool = Security(_verify_api_key)):
        """触发微信重连"""
        conn = _get_wechat_connector()
        if conn and conn.token:
            conn.stop()
        def _do_reconnect():
            import wechat_direct.connector as wc
            from wechat_direct import WeChatConnector
            time.sleep(1)
            wc._clear_credentials()
            new_conn = WeChatConnector(_gf)
            new_conn.run()
        thread = threading.Thread(target=_do_reconnect, daemon=True)
        thread.start()
        return {"status": "reconnecting"}

    # ═══════════════════════════════════════════
    # 多用户管理 API（女友管理器）
    # ═══════════════════════════════════════════

    @app.get("/api/users")
    async def list_users(_auth: bool = Security(_verify_api_key)):
        """获取所有活跃用户列表"""
        if not _gf:
            return {"users": [], "total": 0}
        return {"users": _gf.get_all_users(), "total": _gf.active_user_count}

    @app.get("/api/users/{user_id}")
    async def get_user_detail(user_id: str, _auth: bool = Security(_verify_api_key)):
        """获取某个用户详情"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        info = _gf.get_user_info(user_id)
        if not info:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return info

    @app.get("/api/users/{user_id}/chat")
    async def get_user_chat_history(user_id: str, limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
        """获取某个用户的聊天记录（按 user_id 即 session_id 过滤）"""
        if not _orch or not _orch._memory:
            return {"messages": [], "user_id": user_id}
        # 结构化记忆存了正确的 session_id，直接查 chat_history 表
        sm = getattr(_orch._memory, "structured_memory", None) or getattr(_orch._memory, "_sm", None)
        if sm and hasattr(sm, "get_connection"):
            with sm.get_connection() as conn:
                rows = conn.execute(
                    "SELECT role, content, emotion_tag, created_at FROM chat_history "
                    "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
                messages = [dict(r) for r in rows][::-1]
        else:
            messages = []
        return {"messages": messages, "user_id": user_id}

    @app.get("/api/users/{user_id}/emotion")
    async def get_user_emotion(user_id: str, _auth: bool = Security(_verify_api_key)):
        """获取某个用户的情感状态"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        info = _gf.get_user_info(user_id)
        if not info:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"user_id": user_id, "emotion": info.get("emotion", {})}

    @app.post("/api/users/{user_id}/role")
    async def set_user_role(user_id: str, card_id: str = Query(..., description="角色卡ID"), _auth: bool = Security(_verify_api_key)):
        """给用户分配角色卡"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        ok = _gf.set_user_character(user_id, card_id)
        if not ok:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"status": "ok", "user_id": user_id, "character_card_id": card_id}

    @app.post("/api/users/{user_id}/reset")
    async def reset_user(user_id: str, _auth: bool = Security(_verify_api_key)):
        """重置用户（记忆+情感归零）"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        ok = _gf.reset_user(user_id)
        if not ok:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"status": "reset", "user_id": user_id}

    @app.delete("/api/users/{user_id}")
    async def remove_user(user_id: str, _auth: bool = Security(_verify_api_key)):
        """移除用户"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        ok = _gf.remove_user(user_id)
        if not ok:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"status": "removed", "user_id": user_id}

    # ═══════════════════════════════════════════
    # Clone Data Management API（需求3+4）
    # ═══════════════════════════════════════════

    _clone_mgr = None  # lazy init

    def _get_clone_mgr():
        nonlocal _clone_mgr
        if _clone_mgr is None:
            from api.clone_manager import CloneDataManager
            _clone_mgr = CloneDataManager()
        return _clone_mgr

    @app.get("/api/clone/contacts")
    async def list_clone_contacts(keyword: str = "", _auth: bool = Security(_verify_api_key)):
        """获取可克隆的联系人列表（需求4）"""
        mgr = _get_clone_mgr()
        contacts = mgr.get_contacts(keyword=keyword)
        return {"contacts": contacts, "total": len(contacts)}

    @app.get("/api/clone/datasets")
    async def list_clone_datasets(_auth: bool = Security(_verify_api_key)):
        """列出所有已提取的克隆数据集（需求3）"""
        mgr = _get_clone_mgr()
        datasets = mgr.list_datasets()
        return {"datasets": datasets, "total": len(datasets)}

    @app.get("/api/clone/datasets/{person_id}")
    async def get_clone_dataset_detail(
        person_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
        keyword: str = Query(default=""),
        date_from: str = Query(default=""),
        date_to: str = Query(default=""),
        only_user: bool = Query(default=False),
        _auth: bool = Security(_verify_api_key),
    ):
        """查看某人物的聊天记录详情"""
        mgr = _get_clone_mgr()
        return mgr.get_dataset_detail(
            person_id=person_id,
            page=page,
            page_size=page_size,
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
            only_user=only_user,
        )

    @app.delete("/api/clone/datasets/{person_id}")
    async def delete_clone_dataset(person_id: str, _auth: bool = Security(_verify_api_key)):
        """删除某人物的整个数据集"""
        mgr = _get_clone_mgr()
        ok = mgr.delete_dataset(person_id)
        if not ok:
            raise HTTPException(404, f"数据集 {person_id} 未找到")
        return {"status": "deleted", "person_id": person_id}

    @app.delete("/api/clone/datasets/{person_id}/conversation")
    async def delete_clone_conversation(
        person_id: str,
        index: int = Query(..., description="对话索引（从0开始）"),
        _auth: bool = Security(_verify_api_key),
    ):
        """删除单条对话"""
        mgr = _get_clone_mgr()
        ok = mgr.delete_conversation(person_id, index)
        if not ok:
            raise HTTPException(404, "对话未找到或删除失败")
        return {"status": "deleted", "person_id": person_id, "index": index}

    @app.post("/api/clone/datasets/{person_id}/conversations/batch-delete")
    async def batch_delete_clone_conversations(
        person_id: str,
        indices: list[int] = Query(..., description="要删除的索引列表"),
        _auth: bool = Security(_verify_api_key),
    ):
        """批量删除多条对话"""
        mgr = _get_clone_mgr()
        deleted = mgr.batch_delete_conversations(person_id, indices)
        return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}

    @app.get("/api/clone/stats")
    async def get_clone_stats(_auth: bool = Security(_verify_api_key)):
        """克隆数据全局统计"""
        mgr = _get_clone_mgr()
        return mgr.get_stats()

    # ═══════════════════════════════════════════
    # Real-time Log Stream (SSE)
    # ═══════════════════════════════════════════

    @app.get("/api/logs/stream")
    async def stream_logs(_auth: bool = Security(_verify_api_key)):
        """SSE endpoint for real-time log streaming"""
        async def event_generator():
            queue = asyncio.Queue(maxsize=100)
            loop = asyncio.get_event_loop()

            log_queue_handler = logging.Handler()
            log_queue_handler.setLevel(logging.INFO)

            def emit(record):
                try:
                    msg = log_queue_handler.format(record)
                    asyncio.run_coroutine_threadsafe(
                        queue.put(msg), loop
                    )
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
                        yield "data: {}\n\n"  # Keepalive
            finally:
                root_logger.removeHandler(log_queue_handler)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ═══════════════════════════════════════════
    # Dashboard Enhanced Stats
    # ═══════════════════════════════════════════

    @app.get("/api/stats/dashboard")
    async def get_dashboard_stats(_auth: bool = Security(_verify_api_key)):
        """Enhanced dashboard stats — flat shape matching frontend DashboardStats type"""
        emotion_current = "-"
        affinity = 0
        energy = 0
        chats_today = 0
        facts_count = 0
        sys_status = "unknown"
        uptime = 0

        training_state = _training_mgr.get_state()
        training_info = {
            "status": training_state["status"],
            "progress": training_state["progress"],
            "loss": training_state["loss"],
            "extracted_turns": training_state["extracted_turns"],
            "cleaned_turns": training_state.get("cleaned_turns", 0),
        }

        # Get wechat status (with TTL cache)
        now = time.time()
        if _wechat_status_cache["data"] is not None and (now - _wechat_status_cache["ts"]) < _WECHAT_STATUS_TTL:
            wechat_info = _wechat_status_cache["data"]
        else:
            wechat_info = {"connected": False}
            try:
                wechat_info = await get_wechat_status()
            except Exception as e:
                logger.debug("Failed to get wechat status for dashboard: %s", e)
            _wechat_status_cache["data"] = wechat_info
            _wechat_status_cache["ts"] = now

        # Get emotion/memory stats from running components
        try:
            if _orch:
                emotion = _orch.components.get("emotion") if hasattr(_orch, 'components') else getattr(_orch, '_emotion', None)
                memory = _orch.components.get("memory") if hasattr(_orch, 'components') else getattr(_orch, '_memory', None)
                if emotion:
                    es = emotion.state
                    emotion_current = es.primary_emotion.value if hasattr(es.primary_emotion, 'value') else str(es.primary_emotion)
                    affinity = getattr(es, 'affinity', 0)
                    energy = getattr(es, 'energy', 0)
                if memory:
                    try:
                        structured = getattr(memory, 'structured_memory', None)
                        if structured:
                            chats_today = structured.count_chats_today()
                            if hasattr(structured, 'count_facts'):
                                facts_count = structured.count_facts()
                    except Exception as e:
                        logger.debug("Failed to get memory stats for dashboard: %s", e)
        except Exception as e:
            logger.debug("Failed to get emotion/memory stats for dashboard: %s", e)

        # Get system stats
        if _health:
            try:
                health_data = _health.check()
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

    # ═══════════════════════════════════════════
    # Safety Dashboard API
    # ═══════════════════════════════════════════

    _safety_log_mgr = SafetyLogManager(maxlen=2000)

    def _get_safety():
        if _orch:
            return _orch.components.get("safety") if hasattr(_orch, 'components') else getattr(_orch, '_safety', None)
        return None

    @app.get("/api/safety/stats")
    async def safety_stats(_auth: bool = Security(_verify_api_key)):
        sf = _get_safety()
        return _safety_log_mgr.get_stats(enabled=sf.enabled if sf else False)

    @app.get("/api/safety/log")
    async def safety_log(limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
        return {"log": _safety_log_mgr.get_recent(limit)}

    @app.post("/api/safety/config")
    async def safety_config(enabled: bool = True, _auth: bool = Security(_verify_api_key)):
        sf = _get_safety()
        if sf:
            sf.enabled = enabled
            return {"status": "ok", "enabled": enabled}
        return {"status": "not_available"}

    # ═══════════════════════════════════════════
    # RAG Knowledge Base API
    # ═══════════════════════════════════════════

    def _get_rag():
        if _orch:
            return _orch.components.get("rag") if hasattr(_orch, 'components') else getattr(_orch, '_rag', None)
        return None

    @app.get("/api/rag/stats")
    async def rag_stats(_auth: bool = Security(_verify_api_key)):
        rag = _get_rag()
        if rag:
            return rag.health_check()
        return {"available": False}

    @app.post("/api/rag/search")
    async def rag_search(query: str = "", top_k: int = Query(default=5, le=20), _auth: bool = Security(_verify_api_key)):
        rag = _get_rag()
        if not rag:
            raise HTTPException(503, "RAG引擎未初始化")
        results = rag.retrieve(query, top_k=top_k)
        return {"query": query, "results": results.get("results", []),
                "total_vector": results.get("total_vector", 0),
                "total_keyword": results.get("total_keyword", 0)}

    @app.post("/api/rag/documents")
    async def rag_upload_document(file: UploadFile = File(...), _auth: bool = Security(_verify_api_key)):
        rag = _get_rag()
        if not rag:
            raise HTTPException(503, "RAG引擎未初始化")
        content = await file.read()
        if len(content) > MAX_RAG_UPLOAD_SIZE:
            raise HTTPException(413, f"文档大小超过限制 ({MAX_RAG_UPLOAD_SIZE // 1024 // 1024}MB)")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("gbk", errors="replace")
        chunk_size = 2000
        chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)] if len(text) > chunk_size else [text]
        for idx, chunk in enumerate(chunks):
            rag._sm.add_fact({"fact": chunk, "category": "upload",
                               "source": file.filename, "confidence": 1.0,
                               "chunk_index": idx, "total_chunks": len(chunks)})
        return {"status": "indexed", "filename": file.filename, "size": len(content), "chunks": len(chunks)}

    # ═══════════════════════════════════════════
    # Voice / TTS API
    # ═══════════════════════════════════════════

    def _get_tts():
        if _orch:
            return _orch.components.get("voice") if hasattr(_orch, 'components') else None
        return None

    @app.get("/api/voice/status")
    async def voice_status(_auth: bool = Security(_verify_api_key)):
        tts = _get_tts()
        if tts:
            return tts.health_check()
        return {"enabled": False, "available_engines": []}

    @app.post("/api/voice/synthesize")
    async def voice_synthesize(text: str = Form(...), engine: str = Form(""), _auth: bool = Security(_verify_api_key)):
        tts = _get_tts()
        if not tts or not tts.enabled:
            raise HTTPException(503, "TTS未启用")
        if engine and engine in tts.available_engines:
            await tts.switch_engine(engine)
        audio = await tts.synthesize(text)
        if audio is None:
            raise HTTPException(500, "语音合成失败")
        return Response(content=audio, media_type="audio/wav",
                        headers={"Content-Disposition": "inline; filename=tts.wav"})

    # ═══════════════════════════════════════════
    # Plugin Management API
    # ═══════════════════════════════════════════

    @app.get("/api/plugins")
    async def list_plugins(_auth: bool = Security(_verify_api_key)):
        try:
            plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
            if plugin_path.exists():
                with open(plugin_path, encoding="utf-8") as f:
                    data = json.load(f)
                return {"plugins": data.get("plugins", {})}
        except Exception as e:
            logger.debug("Failed to load plugins config: %s", e)
        return {"plugins": {}}

    @app.post("/api/plugins/{name}/toggle")
    async def toggle_plugin(name: str, enabled: bool = True, _auth: bool = Security(_verify_api_key)):
        plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
        data = {}
        if plugin_path.exists():
            with open(plugin_path, encoding="utf-8") as f:
                data = json.load(f)
        plugins = data.get("plugins", {})
        if name not in plugins:
            plugins[name] = {}
        plugins[name]["enabled"] = enabled
        plugins[name]["toggled_at"] = datetime.now().isoformat()
        data["plugins"] = plugins
        with open(plugin_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok", "name": name, "enabled": enabled}

    # ═══════════════════════════════════════════
    # Multimodal File Upload
    # ═══════════════════════════════════════════

    UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
    # 文件上传大小限制：默认50MB，硬编码上限100MB
    MAX_UPLOAD_SIZE = min(
        int(os.environ.get("MAX_UPLOAD_SIZE", str(50 * 1024 * 1024))),
        100 * 1024 * 1024  # 硬编码上限 100MB
    )
    MAX_RAG_UPLOAD_SIZE = min(
        int(os.environ.get("MAX_RAG_UPLOAD_SIZE", str(10 * 1024 * 1024))),
        50 * 1024 * 1024  # 硬编码上限 50MB
    )

    @app.post("/api/files/upload")
    async def upload_file(file: UploadFile = File(...), _auth: bool = Security(_verify_api_key)):
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(413, f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
        dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
        with open(dest, "wb") as f:
            f.write(content)
        mime = file.content_type or "application/octet-stream"
        msg_type = "image" if mime.startswith("image/") else "voice" if mime.startswith("audio/") else "file"
        return {"status": "ok", "filename": safe_name, "size": len(content),
                "mime_type": mime, "message_type": msg_type,
                "url": f"/api/files/{dest.name}"}

    @app.get("/api/files/{filename}")
    async def serve_file(filename: str, _auth: bool = Security(_verify_api_key)):
        # P0: 路径遍历防护 - 规范化路径并验证在允许目录内
        safe_name = os.path.basename(filename)  # 去除所有路径分隔符
        file_path = (UPLOAD_DIR / safe_name).resolve()
        upload_dir_resolved = UPLOAD_DIR.resolve()
        if not str(file_path).startswith(str(upload_dir_resolved)):
            raise HTTPException(403, "Access denied")
        if not file_path.exists():
            raise HTTPException(404, "文件不存在")
        if not file_path.is_file():
            raise HTTPException(400, "Not a file")
        return FileResponse(file_path)

    # ═══════════════════════════════════════════
    # Proactive History API
    # ═══════════════════════════════════════════

    @app.get("/api/proactive/history")
    async def proactive_history(limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
        if _orch and _orch._ase:
            ase = _orch._ase
            messages = getattr(ase, '_sent_messages', []) if hasattr(ase, '_sent_messages') else []
            return {"history": messages[-limit:], "total": len(messages)}
        return {"history": [], "total": 0}

    # ── 十四挂载 ──
    try:
        from shisi.api.registry import setup_shisi
        shisi_reg = setup_shisi(app, run_migrate=True)
        if orchestrator and hasattr(orchestrator, '_character_manager'):
            orchestrator._character_manager = shisi_reg.character_manager
        logger.info("十四模块已挂载到REST API")

        @app.get("/api/shisi/status")
        async def shisi_status():
            """返回所有模块可用状态"""
            modules = {}
            for attr in ("character_manager", "affinity_enhancer", "stage_engine",
                         "sticker_manager", "favorite_manager", "forward_manager",
                         "vital_engine", "voice_enhancer", "analytics_service",
                         "wechat_handler", "proactive_messenger", "training_manager",
                         "character_service"):
                modules[attr] = getattr(shisi_reg, attr, None) is not None
            return {"available": True, "modules": modules}
    except Exception:
        logger.exception("十四模块挂载失败")
        @app.get("/api/shisi/status")
        async def shisi_status():
            return {"available": False, "error": "module_load_failed"}

    # ═══ LLM缓存统计API ═══
    @app.get("/api/cache/stats")
    async def cache_stats(_auth: bool = Security(_verify_api_key)):
        """获取LLM缓存统计信息"""
        try:
            from cache.llm_cache import LLMCache
            cache = LLMCache()
            return {
                "available": cache.enabled,
                "stats": cache.get_stats(),
                "health": cache.health_check(),
            }
        except Exception:
            logger.exception("Cache stats query failed")
            return {"available": False, "error": "internal_error"}

    @app.post("/api/cache/invalidate")
    async def cache_invalidate(pattern: str = "*", _auth: bool = Security(_verify_api_key)):
        """使LLM缓存失效"""
        try:
            from cache.llm_cache import LLMCache
            cache = LLMCache()
            if not cache.enabled:
                raise HTTPException(503, "Cache not enabled")
            deleted = cache.invalidate(pattern)
            return {"status": "ok", "deleted_keys": deleted}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Cache invalidation failed")
            raise HTTPException(500, "Cache invalidation failed")

    # ── 微信二维码 API ──
    try:
        from api.qrcode_store import router as qrcode_router
        app.include_router(qrcode_router)
    except Exception as e:
        logger.warning("二维码API挂载失败: %s", e)

    @app.get("/api/routes")
    async def list_routes():
        """返回所有已注册路由的路径和方法列表"""
        routes = []
        for route in app.routes:
            if hasattr(route, 'path') and hasattr(route, 'methods'):
                routes.append({"path": route.path, "methods": list(route.methods)})
        return {"routes": routes, "total": len(routes)}

    return app
