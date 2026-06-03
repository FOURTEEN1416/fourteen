"""
REST API 应用工厂

精简版：仅负责创建 FastAPI 实例、配置中间件、挂载子路由。
业务路由按域拆分为 8 个子路由文件（共 71 端点），模型/常量/Helper 仍保留在 api.main_routes。
"""

from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api._chat_routes import router as chat_router
from api._clone_routes import router as clone_router
from api._misc_routes import router as misc_router
from api._personality_routes import router as personality_router
from api._safety_routes import router as safety_router
from api._tools_routes import router as tools_router
from api._training_routes import router as training_router
from api._users_routes import router as users_router
from api.auth import configure_auth, verify_api_key_dep
from api.deps import deps

logger = logging.getLogger("app_factory")

# ── 速率限制依赖（可选） ─────────────────────────────

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False

# ── 常量 ──────────────────────────────────────────────

MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB

# ── 应用工厂 ──────────────────────────────────────────


def create_api_app(
    orchestrator=None,
    health_checker=None,
    config_manager=None,
    session_manager=None,
    girlfriend_manager=None,
) -> FastAPI:
    _is_prod = os.environ.get("ENV", os.environ.get("APP_ENV", "")).lower() in ("prod", "production")

    # ── FastAPI 实例 ──
    app = FastAPI(    title="唯一的你 API", version="2.0", debug=not _is_prod)

    # ═══════════════════════════════════════════════════
    # 中间件
    # ═══════════════════════════════════════════════════

    # ── CORS ──
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

    # ── 安全响应头 ──
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

    # ── 请求体大小限制 ──
    @app.middleware("http")
    async def request_size_limiter(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body too large (max {MAX_REQUEST_SIZE // 1024 // 1024}MB)", "error_code": "REQUEST_TOO_LARGE"},
                    )
            except ValueError:
                pass
        return await call_next(request)

    # ── 速率限制 ──
    if HAS_SLOWAPI:
        limiter = Limiter(key_func=get_remote_address)
        app.state.limiter = limiter
    else:
        _setup_fallback_rate_limiter(app)

    # ═══════════════════════════════════════════════════
    # 异常处理器
    # ═══════════════════════════════════════════════════

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        status_code_map = {
            401: "AUTH_ERROR", 429: "RATE_LIMIT", 503: "FEATURE_UNAVAILABLE",
            404: "FEATURE_UNAVAILABLE", 504: "LLM_TIMEOUT", 502: "NETWORK_ERROR",
        }
        error_code = (exc.headers or {}).get("X-Error-Code") or status_code_map.get(exc.status_code, "UNKNOWN")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail, "error_code": error_code})

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        if isinstance(exc, ValueError):
            return JSONResponse(status_code=400, content={"detail": str(exc), "error_code": "VALIDATION_ERROR"})
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_code": "INTERNAL_ERROR"})

    # ═══════════════════════════════════════════════════
    # 认证
    # ═══════════════════════════════════════════════════

    _api_key_enabled = os.environ.get("API_KEY_ENABLED", "true" if _is_prod else "false").lower() == "true"
    if _is_prod and not os.environ.get("API_KEY"):
        logger.warning("Production environment detected without API_KEY set - authentication is enabled but no key configured")
    _api_key = os.environ.get("API_KEY", "")
    configure_auth(enabled=_api_key_enabled, api_key=_api_key)

    # ═══════════════════════════════════════════════════
    # 注入依赖
    # ═══════════════════════════════════════════════════

    deps.set_deps(
        orch=orchestrator,
        health=health_checker,
        config=config_manager,
        sessions=session_manager,
        gf=girlfriend_manager,
    )

    # ═══════════════════════════════════════════════════
    # 挂载主路由（已拆分为 8 个子路由，共 71 端点）
    # ═══════════════════════════════════════════════════

    app.include_router(misc_router)         # 10 端点: health/stats/memory/logs/config/channels/routes
    app.include_router(chat_router)         # 10 端点: chat/session + wechat channels
    app.include_router(personality_router)  #  9 端点: emotion/persona/psych
    app.include_router(users_router)        #  7 端点: users/*
    app.include_router(training_router)     # 11 端点: training/* + proactive/*
    app.include_router(tools_router)        #  5 端点: tools/* + plugins/*
    app.include_router(safety_router)       # 12 端点: safety/rag/voice/files/cache
    app.include_router(clone_router)        #  7 端点: clone/*
    logger.info("主路由已拆分为 8 个子路由 (71 端点)")

    # ── 用户认证 API ──
    try:
        from api.routers.auth_routes import router as auth_router
        app.include_router(auth_router)
        logger.info("用户认证API已挂载 (/api/auth)")
    except Exception as e:
        logger.warning("用户认证API挂载失败: %s", e)

    # ═══════════════════════════════════════════════════
    # shisi（十四）模块挂载
    # ═══════════════════════════════════════════════════

    shisi_reg = None
    try:
        from shisi.api.registry import setup_shisi
        shisi_reg = setup_shisi(app, run_migrate=True)
        if orchestrator and hasattr(orchestrator, '_character_manager'):
            orchestrator._character_manager = shisi_reg.character_manager
        deps.shisi_reg = shisi_reg
        logger.info("十四模块已挂载到REST API")

        @app.get("/api/shisi/status")
        async def shisi_status():
            modules = {}
            for attr in (
                "character_manager", "affinity_enhancer", "stage_engine",
                "sticker_manager", "favorite_manager", "forward_manager",
                "vital_engine", "voice_enhancer", "analytics_service",
                "wechat_handler", "proactive_messenger", "training_manager",
                "character_service",
            ):
                modules[attr] = getattr(shisi_reg, attr, None) is not None
            return {"available": True, "modules": modules}
    except Exception:
        logger.exception("十四模块挂载失败")

        @app.get("/api/shisi/status")
        async def shisi_status():
            return {"available": False, "error": "module_load_failed"}

    # ═══════════════════════════════════════════════════
    # 其他子路由挂载
    # ═══════════════════════════════════════════════════

    # ── 微信二维码 API ──
    try:
        from api.qrcode_store import router as qrcode_router
        app.include_router(qrcode_router)
    except Exception as e:
        logger.warning("二维码API挂载失败: %s", e)

    # ── 统一角色管理 API ──
    try:
        from api.routers.character_routes import router as character_router
        from api.routers.character_routes import set_dependencies as set_character_deps
        set_character_deps(orchestrator, girlfriend_manager, verify_api_key_dep)
        app.include_router(character_router)
        logger.info("统一角色管理API已挂载")
    except Exception as e:
        logger.warning("统一角色管理API挂载失败: %s", e)

    # ── 角色音色绑定 API ──
    try:
        from api.routers.voice_routes import router as voice_router
        from api.routers.voice_routes import set_dependencies as set_voice_deps
        set_voice_deps(orchestrator, verify_api_key_dep)
        app.include_router(voice_router)
        logger.info("角色音色绑定API已挂载")
    except Exception as e:
        logger.warning("角色音色绑定API挂载失败: %s", e)

    # ── MiMo TTS API ──
    try:
        from api.routers.mimo_voice_routes import router as mimo_voice_router
        app.include_router(mimo_voice_router)
        logger.info("MiMo TTS API已挂载")
    except Exception as e:
        logger.warning("MiMo TTS API挂载失败: %s", e)

    # ── 统一记忆 API（桥接 shisi FavoriteManager/ForwardManager） ──
    if shisi_reg is not None:
        try:
            from api.routers.memory_routes import router as memory_bridge_router
            from api.routers.memory_routes import set_dependencies as set_memory_deps
            _fav_mgr_local = shisi_reg.favorite_manager
            _fwd_mgr_local = shisi_reg.forward_manager
            set_memory_deps(verify_api_key_dep, fav_mgr=_fav_mgr_local, fwd_mgr=_fwd_mgr_local)
            app.include_router(memory_bridge_router)
            logger.info("统一记忆API已挂载")
        except Exception as e:
            logger.warning("统一记忆API挂载失败: %s", e)

    # ── 统一角色卡 API（桥接 shisi CharacterManager） ──
    if shisi_reg is not None:
        try:
            from api.routers.persona_card_routes import router as persona_card_router
            from api.routers.persona_card_routes import set_dependencies as set_pcard_deps
            _char_mgr_local = shisi_reg.character_manager
            set_pcard_deps(verify_api_key_dep, character_mgr=_char_mgr_local)
            app.include_router(persona_card_router)
            logger.info("统一角色卡API已挂载")
        except Exception as e:
            logger.warning("统一角色卡API挂载失败: %s", e)

    # ── 剧情线 API ──
    try:
        from api.routers.storyline_routes import router as storyline_router
        from api.routers.storyline_routes import set_dependencies as set_storyline_deps
        set_storyline_deps(verify_api_key_dep)
        app.include_router(storyline_router)

        from api.routers.knowledge_routes import router as knowledge_router
        from api.routers.knowledge_routes import set_dependencies as set_knowledge_deps
        set_knowledge_deps(verify_api_key_dep)
        app.include_router(knowledge_router)
        logger.info("剧情线API已挂载")
    except Exception as e:
        logger.warning("剧情线API挂载失败: %s", e)

    # ── 微信连接持久化 API ──
    try:
        from api.routers.wechat_routes import router as wechat_router
        from api.routers.wechat_routes import set_dependencies as set_wechat_deps
        set_wechat_deps(verify_api_key_dep)
        app.include_router(wechat_router)
        logger.info("微信连接持久化API已挂载")
    except Exception as e:
        logger.warning("微信连接持久化API挂载失败: %s", e)

    # ── 情绪参数编辑 API ──
    try:
        from api.routers.emotion_routes import router as emotion_params_router
        from api.routers.emotion_routes import set_dependencies as set_emotion_params_deps
        set_emotion_params_deps(verify_api_key_dep)
        app.include_router(emotion_params_router)
        logger.info("情绪参数编辑API已挂载")
    except Exception as e:
        logger.warning("情绪参数编辑API挂载失败: %s", e)

    # ── 管理员用户管理 API ──
    try:
        from api.routers.admin_routes import router as admin_router
        app.include_router(admin_router)
        logger.info("管理员用户管理API已挂载")
    except Exception as e:
        logger.warning("管理员用户管理API挂载失败: %s", e)

    # ── 邀请码 API（注册 + 管理） ──
    try:
        from api.routers.invite_routes import router as invite_router
        app.include_router(invite_router)
        logger.info("邀请码API已挂载 (/api/auth/register-invite + /api/admin/invites)")
    except Exception as e:
        logger.warning("邀请码API挂载失败: %s", e)

    return app


# ── 回退速率限制器（无 slowapi 时使用） ────────────────


def _setup_fallback_rate_limiter(app: FastAPI) -> None:
    from collections import defaultdict

    _rate_limit_store: dict[str, list[float]] = defaultdict(list)
    _rate_limit_lock = threading.Lock()
    _rate_limit_last_cleanup: list[float] = [time.time()]

    def _cleanup_expired_records(now: float, window_seconds: int):
        expired_keys = []
        for key, timestamps in _rate_limit_store.items():
            valid_timestamps = [t for t in timestamps if now - t < window_seconds]
            if valid_timestamps:
                _rate_limit_store[key] = valid_timestamps
            else:
                expired_keys.append(key)
        for key in expired_keys:
            del _rate_limit_store[key]

    def _simple_rate_limit(request: Request, max_requests: int = 60, window_seconds: int = 60) -> bool:
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        now = time.time()

        with _rate_limit_lock:
            if now - _rate_limit_last_cleanup[0] > 300:
                _cleanup_expired_records(now, window_seconds)
                _rate_limit_last_cleanup[0] = now

            _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window_seconds]

            if not _rate_limit_store[key]:
                if key in _rate_limit_store:
                    del _rate_limit_store[key]
                return True

            if len(_rate_limit_store[key]) >= max_requests:
                return False
            _rate_limit_store[key].append(now)
            return True

    @app.middleware("http")
    async def fallback_rate_limiter(request: Request, call_next):
        if not _simple_rate_limit(request):
            return JSONResponse(status_code=429, content={"detail": "Too many requests"})
        return await call_next(request)
