"""
REST API 应用工厂

仅负责创建 FastAPI 实例、配置中间件、挂载子路由。
业务路由按域拆分挂载（2026-09-21 内省实测：**220 个 APIRoute / 186 条唯一路径**，
其中 105 GET / 79 POST / 16 PUT / 20 DELETE；`len(app.routes)=224` 另含 4 条框架路由
`/openapi.json` `/docs` `/docs/oauth2-redirect` `/redoc`）。详细端点分布见 CODE_GRAPH.md §4.2。

⚠️ 上述为 **2026-09-21 复测值**（09-20 口径为 215 / 181，其后 agent-plane 路由组 +5 端点）。改路由后请同步本节与 CODE_GRAPH.md §4.2。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth import configure_auth, verify_api_key_dep
from api.auth_jwt import verify_token
from api.deps import deps
from api.health_routes import health_router
from api.routers.chat_routes import router as chat_router
from api.routers.clone_routes import router as clone_router
from api.routers.misc_routes import router as misc_router
from api.routers.personality_routes import router as personality_router
from api.routers.safety_routes import router as safety_router
from api.routers.tools_routes import router as tools_router
from api.routers.training_routes import router as training_router
from api.routers.users_routes import router as users_router
from api.runtime_config import (
    UNSAFE_API_KEYS,
    is_explicit_dev,
    is_production,
    resolve_api_key_enabled,
)
from observability.logging_setup import _user_id

logger = logging.getLogger("app_factory")

# ── 速率限制（slowapi 可选） ────────────────────────────

try:
    from slowapi.errors import RateLimitExceeded

    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False

# ── 常量 ──────────────────────────────────────────────

MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB


class UserContextMiddleware:
    """从 Authorization header 提取当前用户 ID，写入日志上下文变量。

    让后续 ring_buffer / 安全日志能够按用户维度隔离，非管理员只能查看本账号信息。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("latin-1")
            user_id = None
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                try:
                    payload = verify_token(token)
                    user_id = int(payload.get("sub")) if payload.get("sub") else None
                except Exception:
                    user_id = None
            token_ctx = _user_id.set(user_id)
            try:
                await self.app(scope, receive, send)
            finally:
                _user_id.reset(token_ctx)
        else:
            await self.app(scope, receive, send)


# ── 应用工厂 ──────────────────────────────────────────


def create_api_app(
    orchestrator=None,
    health_checker=None,
    config_manager=None,
    session_manager=None,
    user_manager=None,
    lifespan=None,
) -> FastAPI:
    _is_prod = is_production()

    # ── Sentry 错误监控（生产环境自动启用） ──
    if _is_prod:
        try:
            from observability.sentry import init_sentry
            init_sentry()
        except Exception:
            pass  # Sentry 不可用不影响启动

    # ── FastAPI 实例 ──
    app = FastAPI(title="唯一的你 API", version="3.1.0", debug=not _is_prod, lifespan=lifespan)

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

    # ── 用户上下文中间件（用于日志/安全日志按用户隔离） ──
    app.add_middleware(UserContextMiddleware)

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
    _rate_limit_enabled = os.environ.get(
        "RATE_LIMIT_ENABLED", "true" if _is_prod else "false"
    ).lower() == "true"
    _rate_limit_per_minute = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60"))

    # SlowAPI 的 SlowAPIMiddleware 与 FastAPI 0.139+ 的 _IncludedRouter 不兼容，
    # 因此无论 slowapi 是否安装，都使用自定义回退限流器作为实际执行机制。
    # slowapi 的异常处理器仍注册，以支持 per-route @limiter.limit() 装饰器。
    if HAS_SLOWAPI:
        async def _rate_limit_exceeded_handler(
            request: Request, exc: RateLimitExceeded
        ) -> JSONResponse:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded: {exc.detail}",
                    "error_code": "RATE_LIMIT",
                },
            )

        app.add_exception_handler(
            RateLimitExceeded,
            cast(Any, _rate_limit_exceeded_handler),
        )

    if _rate_limit_enabled:
        _setup_fallback_rate_limiter(app, max_requests=_rate_limit_per_minute)

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

    # 口径唯一真源（审查：此前三处各写一份，app_factory 与 main 曾分叉）
    _api_key_enabled = resolve_api_key_enabled()
    _api_key = os.environ.get("API_KEY", "").strip()
    _explicit_dev = is_explicit_dev()
    if _api_key_enabled and _api_key in UNSAFE_API_KEYS:
        if _explicit_dev:
            logger.warning(
                "API_KEY 为公开占位符/弱默认（仅显式 dev 允许）。生产请替换为强随机密钥。"
            )
        else:
            # 审查 F-high-1：公开仓 .env.example 占位符不得在非 dev 启用认证时静默生效
            raise RuntimeError(
                "API auth is enabled but API_KEY is missing or is a public placeholder/weak default "
                "(e.g. CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN). "
                f"production={_is_prod} explicit_dev={_explicit_dev}. "
                "Set a strong random API_KEY (openssl rand -base64 32) "
                "or run with AI_GF_ENV=dev for local development."
            )
    if _is_prod and not os.environ.get("API_KEY"):
        logger.warning("Production environment detected without API_KEY set - authentication is enabled but no key configured")
    configure_auth(enabled=_api_key_enabled, api_key=_api_key)

    # ═══════════════════════════════════════════════════
    # 注入依赖
    # ═══════════════════════════════════════════════════

    deps.set_deps(
        orch=orchestrator,
        health=health_checker,
        config=config_manager,
        sessions=session_manager,
        gf=user_manager,
    )
    deps.shisi_reg = None
    deps.route_mounts = {}

    # ═══════════════════════════════════════════════════
    # 挂载健康检查路由（无需认证，优先于其他路由注册）
    # ═══════════════════════════════════════════════════

    app.include_router(health_router)       # 2 端点: /api/health + /api/ready（无认证）
    logger.info("健康检查路由已挂载 (/api/health, /api/ready)")

    # ═══════════════════════════════════════════════════
    # 挂载主路由（已拆分为 8 个子路由，共 83 端点；2026-09-17 内省实测）
    # ═══════════════════════════════════════════════════

    app.include_router(misc_router)         # 16 端点: health/stats/memory/logs/config/channels/routes
    app.include_router(chat_router)         # 11 端点: chat/session + wechat channels
    app.include_router(personality_router)  # 10 端点: emotion/persona/psych
    app.include_router(users_router)        #  7 端点: users/*
    app.include_router(training_router)     # 13 端点: training/* + proactive/*（含手动控制）
    app.include_router(tools_router)        #  6 端点: tools/* + plugins/*
    app.include_router(safety_router)       # 12 端点: safety/rag/voice/files/cache
    app.include_router(clone_router)        #  8 端点: clone/*
    try:
        from api.routers.agent_plane_routes import router as agent_plane_router

        app.include_router(agent_plane_router)
        logger.info("Agent Plane 路由已挂载 (/api/agent-plane/*)")
    except Exception as e:  # noqa: BLE001
        logger.warning("Agent Plane 路由挂载失败: %s", e)
    logger.info("主路由已拆分为 8 个子路由 (83 端点)")

    # ── 用户认证 API ──
    # 2026-08-31：去 try 静默吞——认证路由消失=登录全挂，必须 fail-fast 而非降级
    # （CI 曾因此炸 test_consent_route_is_mounted：静默少挂路由，测试才暴露）
    from api.routers.auth_routes import router as auth_router  # noqa: E402

    app.include_router(auth_router)
    logger.info("用户认证API已挂载 (/api/auth)")

    # ═══════════════════════════════════════════════════
    # shisi（十四）模块挂载
    # ═══════════════════════════════════════════════════

    shisi_reg = None
    try:
        from shisi.api.registry import setup_shisi
        # 从 orchestrator 获取 memory_service，复用其 favorite/forward 管理器
        _mem_service = None
        if orchestrator is not None:
            _mem_service = getattr(orchestrator, "components", {}).get("memory") if hasattr(orchestrator, "components") else getattr(orchestrator, "_memory", None)
        shisi_reg = setup_shisi(app, run_migrate=True, memory_service=_mem_service)
        if orchestrator is not None and hasattr(orchestrator, "components"):
            orchestrator.components["character_manager"] = shisi_reg.character_manager
        deps.shisi_reg = shisi_reg
        logger.info("十四模块已挂载到REST API")

        # 2026-09-18 裁决：纳入认证。本端点暴露内部模块的初始化状态（架构侦察
        # 信息），属控制面而非探活面——探活职责由无认证的 /api/health 承担（含
        # version/environment，且有测试契约保护）。前端与测试对本端点零消费。
        @app.get("/api/shisi/status", dependencies=[Security(verify_api_key_dep)])
        async def shisi_status():
            modules = {}
            for attr in (
                "character_manager", "affinity_enhancer", "stage_engine",
                "sticker_manager", "favorite_manager", "forward_manager",
                "vital_engine", "voice_enhancer", "analytics_service",
                "training_manager",
            ):
                modules[attr] = getattr(shisi_reg, attr, None) is not None
            return {"available": True, "modules": modules}
    except Exception:
        logger.exception("十四模块挂载失败")

        # 失败分支同样纳入认证（理由同上，保持两条注册路径口径一致）
        @app.get("/api/shisi/status", dependencies=[Security(verify_api_key_dep)])
        async def shisi_status():
            return {"available": False, "error": "module_load_failed"}

    # ═══════════════════════════════════════════════════
    # 其他子路由挂载
    # ═══════════════════════════════════════════════════

    # ── 微信二维码 API（admin 兼容）──
    try:
        from api.qrcode_store import router as qrcode_router
        app.include_router(qrcode_router)
    except Exception as e:
        logger.warning("二维码API挂载失败: %s", e)

    # ── 每人独立微信通道 API（2026-09-19）──
    try:
        from api.routers.wechat_channel_routes import (
            admin_router as wechat_admin_router,
        )
        from api.routers.wechat_channel_routes import (
            router as wechat_channel_router,
        )
        app.include_router(wechat_channel_router)
        app.include_router(wechat_admin_router)
        deps.route_mounts["wechat_channel"] = True
        logger.info("每人独立微信通道 API已挂载")
    except Exception as e:
        deps.route_mounts["wechat_channel"] = False
        logger.warning("微信通道API挂载失败: %s", e)

    def _mount_required_router(name: str, router) -> None:
        """挂载控制端关键路由，并记录到 readiness 能力矩阵。"""
        app.include_router(router)
        deps.route_mounts[name] = True
        logger.info("%s API已挂载", name)

    # ── 统一角色管理 API ──
    try:
        from api.routers.character_routes import router as character_router
        _mount_required_router("characters", character_router)
    except Exception as e:
        deps.route_mounts["characters"] = False
        logger.warning("统一角色管理API挂载失败: %s", e)

    # ── 角色音色绑定 API ──
    try:
        from api.routers.voice_routes import router as voice_router
        _mount_required_router("character_voice", voice_router)
    except Exception as e:
        deps.route_mounts["character_voice"] = False
        logger.warning("角色音色绑定API挂载失败: %s", e)

    # ── MiMo TTS API ──
    try:
        from api.routers.mimo_voice_routes import router as mimo_voice_router
        app.include_router(mimo_voice_router)
        logger.info("MiMo TTS API已挂载")
    except Exception as e:
        logger.warning("MiMo TTS API挂载失败: %s", e)

    # ── 统一记忆 API（桥接 shisi FavoriteManager/ForwardManager） ──
    try:
        from api.routers.memory_routes import router as memory_bridge_router
        _mount_required_router("character_memory", memory_bridge_router)
    except Exception as e:
        deps.route_mounts["character_memory"] = False
        logger.warning("统一记忆API挂载失败: %s", e)

    # ── 统一角色卡 API（桥接 shisi CharacterManager） ──
    try:
        from api.routers.persona_card_routes import router as persona_card_router
        _mount_required_router("persona_card", persona_card_router)
    except Exception as e:
        deps.route_mounts["persona_card"] = False
        logger.warning("统一角色卡API挂载失败: %s", e)

    # ── 剧情线 API ──
    try:
        from api.routers.storyline_routes import router as storyline_router
        _mount_required_router("storyline", storyline_router)

        from api.routers.knowledge_routes import router as knowledge_router
        _mount_required_router("knowledge", knowledge_router)
    except Exception as e:
        deps.route_mounts.setdefault("storyline", False)
        deps.route_mounts.setdefault("knowledge", False)
        logger.warning("剧情线API挂载失败: %s", e)

    # ── 微信连接持久化 API ──
    try:
        from api.routers.wechat_routes import router as wechat_router
        _mount_required_router("wechat", wechat_router)
    except Exception as e:
        deps.route_mounts["wechat"] = False
        logger.warning("微信连接持久化API挂载失败: %s", e)

    # ── 情绪参数编辑 API ──
    try:
        from api.routers.emotion_routes import router as emotion_params_router
        _mount_required_router("emotion_params", emotion_params_router)
    except Exception as e:
        deps.route_mounts["emotion_params"] = False
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

    # ── LLM 供应商管理 API（含申请教程） ──
    try:
        from api.routers.llm_providers_routes import router as llm_providers_router
        app.include_router(llm_providers_router)
        logger.info("LLM供应商管理API已挂载 (/api/llm-providers)")
    except Exception as e:
        logger.warning("LLM供应商管理API挂载失败: %s", e)

    return app


# ── 回退速率限制器（无 slowapi 时使用） ────────────────


def _setup_fallback_rate_limiter(app: FastAPI, max_requests: int = 60) -> None:
    from collections import defaultdict

    _rate_limit_store: dict[str, list[float]] = defaultdict(list)
    # 每个 key 的最近一次请求时间。用于把清理从 O(键数 × 窗口内记录数) 降到 O(键数)：
    # 旧实现每次清理都要遍历并重建所有 key 的时间戳列表，而清理持有 _rate_limit_lock，
    # 该锁在**每个请求**的中间件里都会被获取 —— 高基数（扫描/爬虫）下会形成一次
    # 全站请求停顿。改为按 last_seen 直接判定空闲 key，不再重建列表。
    _rate_limit_last_seen: dict[str, float] = {}
    _rate_limit_lock = threading.Lock()
    _rate_limit_last_cleanup: list[float] = [time.time()]
    _max_requests = max_requests
    # 硬上限：清理间隔内 key 数超过此值时立即触发一次清理，防止内存被高基数 key 撑爆
    _max_keys = 50_000

    def _cleanup_expired_records(now: float, window_seconds: int):
        idle_keys = [
            key for key, seen in _rate_limit_last_seen.items()
            if now - seen >= window_seconds
        ]
        for key in idle_keys:
            _rate_limit_last_seen.pop(key, None)
            _rate_limit_store.pop(key, None)

    def _simple_rate_limit(request: Request, window_seconds: int = 60) -> bool:
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        now = time.time()

        with _rate_limit_lock:
            if (now - _rate_limit_last_cleanup[0] > 300
                    or len(_rate_limit_last_seen) > _max_keys):
                _cleanup_expired_records(now, window_seconds)
                _rate_limit_last_cleanup[0] = now

            _rate_limit_last_seen[key] = now
            _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window_seconds]

            if len(_rate_limit_store[key]) >= _max_requests:
                return False
            _rate_limit_store[key].append(now)
            return True

    @app.middleware("http")
    async def fallback_rate_limiter(request: Request, call_next):
        if not _simple_rate_limit(request):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests", "error_code": "RATE_LIMIT"},
            )
        return await call_next(request)
