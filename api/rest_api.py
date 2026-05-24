from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Security, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

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
    emotion: Optional[dict] = None


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
    app = FastAPI(title="十四 AI虚拟伴侣系统API", version="2.0")

    cors_origins_env = os.environ.get("API_CORS_ORIGINS", "http://localhost:5173")
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    if cors_origins == ["*"]:
        logger.warning("CORS allows all origins - not recommended for production")
    _is_prod = os.environ.get("ENV", os.environ.get("APP_ENV", "")).lower() in ("prod", "production")
    if _is_prod and (cors_origins == ["*"] or not cors_origins):
        logger.warning("Production environment detected with permissive CORS - consider restricting origins")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    _api_key_enabled = os.environ.get("API_KEY_ENABLED", "false").lower() == "true"
    _api_key = os.environ.get("API_KEY", "")
    _api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def _verify_api_key(api_key: Optional[str] = Security(_api_key_header)):
        if not _api_key_enabled:
            return True
        import hmac
        if hmac.compare_digest(api_key or "", _api_key):
            return True
        raise HTTPException(401, "Invalid or missing API key")

    if HAS_SLOWAPI:
        limiter = Limiter(key_func=get_remote_address)  # type: ignore
        app.state.limiter = limiter

    _orch = orchestrator
    _health = health_checker
    _config = config_manager
    _sessions = session_manager
    _gf = girlfriend_manager  # 女友管理器（多用户核心）

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
        if not _orch:
            raise HTTPException(503, "Orchestrator not initialized")
        result = await _orch.process_message(req.message, req.session_id, req.message_type)
        return ChatResponse(
            reply=result.get("reply", ""),
            trace_id=result.get("trace_id", ""),
            emotion=result.get("emotion"),
        )

    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
        if not _orch or not hasattr(_orch, 'process_message_stream'):
            raise HTTPException(503, "Stream not available")

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
    async def stats():
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
    async def list_sessions():
        if _sessions:
            return {
                "sessions": _sessions.get_active_sessions(),
                "active_count": _sessions.active_count,
            }
        return {"sessions": [], "active_count": 0}

    @app.get("/api/chat/history")
    async def chat_history(session_id: str = "", limit: int = Query(default=20, ge=1, le=100)):
        if not _orch or not _orch._memory:
            return {"messages": []}
        messages = _orch._memory.working.get_recent(limit)
        return {"messages": messages, "session_id": session_id}

    @app.get("/api/emotion/state")
    async def emotion_state():
        if _orch and _orch._emotion:
            return _orch._emotion.health_check()
        return {}

    @app.get("/api/emotion/trend")
    async def emotion_trend(days: int = Query(default=7, ge=1, le=30)):
        if not _orch or not _orch._emotion:
            return {"trend": [], "days": days}
        trend = getattr(_orch._emotion, '_emotion_history', [])
        return {"trend": trend[-days * 20:], "days": days}

    @app.get("/api/persona/profile")
    async def persona_profile():
        if _orch and _orch._persona:
            return {
                "core_character": _orch._persona.profile.core_character,
                "speaking_style": _orch._persona.profile.speaking_style,
                "emotional_preference": _orch._persona.profile.emotional_preference,
            }
        return {}

    @app.get("/api/persona/evolution-log")
    async def persona_evolution_log(limit: int = Query(default=50, ge=1, le=500)):
        if _orch and _orch._persona:
            return {"log": _orch._persona.get_evolution_log(limit)}
        return {"log": []}

    # ═══ 用户心理画像（OCEAN+PAD人格分析） ═══

    @app.get("/api/psych/profile")
    async def psych_profile():
        """获取用户心理画像（OCEAN五大人格 + PAD情感 + 风格向量）"""
        pe = None
        if _orch:
            pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
        if pe is None:
            return {"user_id": "default", "status": "unavailable", "snapshots": 0}
        return pe.get_user_profile_summary()

    @app.get("/api/psych/snapshots")
    async def psych_snapshots(limit: int = Query(default=20, ge=1, le=200)):
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
    async def psych_mental_health():
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
    async def psych_liwc():
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
    async def memory_facts(category: Optional[str] = None, limit: int = Query(default=50)):
        if _orch and _orch._memory:
            return {"facts": _orch._memory.semantic.get_facts(category, limit=limit)}
        return {"facts": []}

    @app.get("/api/tools")
    async def tools_list():
        if _orch and _orch._tools:
            return {"tools": _orch._tools.registry.tool_names}
        return {"tools": []}

    @app.get("/api/training/status")
    async def training_status():
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
    async def proactive_state():
        if _orch and _orch._ase:
            return _orch._ase.health_check()
        return {}

    @app.get("/api/logs")
    async def get_logs(limit: int = Query(default=100, le=200), level: str = Query(default="all"),
                       search: str = Query(default=""), _auth: bool = Security(_verify_api_key)):
        return {"logs": ring_buffer.get_recent(limit=limit, level=level, search=search)}

    @app.get("/api/channels")
    async def list_channels():
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
        except ImportError:
            pass
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
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Config save failed: %s", e)
            raise HTTPException(400, f"Invalid config: {e}")

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

    _tool_history: list = []

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
        _tool_history.append({
            "timestamp": datetime.now().isoformat(),
            "tool": name, "action": "enable" if req.enabled else "disable",
        })
        logger.info("Tool '%s' toggled: enabled=%s", name, req.enabled)
        return {"status": "ok", "tool": name, "enabled": req.enabled}

    @app.get("/api/tools/history")
    async def tool_history(limit: int = Query(default=50, le=200)):
        return {"history": _tool_history[-limit:]}

    # ═══════════════════════════════════════════
    # Training / Clone Pipeline API
    # ═══════════════════════════════════════════

    # Global training state tracker
    _training_state: dict = {
        "status": "idle",  # idle, extracting, cleaning, training, testing, done, error
        "progress": 0.0,
        "current_step": 0,
        "total_steps": 0,
        "loss": None,
        "extracted_turns": 0,
        "cleaned_turns": 0,
        "error": None,
        "start_time": None,
        "eta_seconds": None,
    }
    _training_lock = threading.Lock()

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
                with _training_lock:
                    _training_state["status"] = "extracted"
                    _training_state["extracted_turns"] = len(result) if isinstance(result, list) else result.get("turns", 0)
                    _training_state["progress"] = 0.3
            except Exception as e:
                with _training_lock:
                    _training_state["status"] = "error"
                    _training_state["error"] = str(e)
                logger.error("Extraction failed: %s", e)

        if not target.strip():
            raise HTTPException(status_code=400, detail="target is required")

        thread = threading.Thread(target=_do_extract, daemon=True)
        thread.start()

        with _training_lock:
            _training_state["status"] = "extracting"
            _training_state["start_time"] = time.time()

        return {"status": "started", "task": "extract", "target": target}

    @app.get("/api/training/progress")
    async def get_training_progress(_auth: bool = Security(_verify_api_key)):
        """Get real-time training progress"""
        with _training_lock:
            return dict(_training_state)

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
                        with open(result_path, "r", encoding="utf-8") as f:
                            cleaned_data = json.load(f)
                        cleaned_count = len(cleaned_data) if isinstance(cleaned_data, list) else 0
                    except Exception:
                        pass
                with _training_lock:
                    _training_state["status"] = "cleaned"
                    _training_state["cleaned_turns"] = cleaned_count
                    _training_state["progress"] = 0.6
            except Exception as e:
                with _training_lock:
                    _training_state["status"] = "error"
                    _training_state["error"] = str(e)

        thread = threading.Thread(target=_do_clean, daemon=True)
        thread.start()

        with _training_lock:
            _training_state["status"] = "cleaning"
            _training_state["start_time"] = time.time()

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
                    with _training_lock:
                        _training_state["current_step"] = step
                        _training_state["total_steps"] = total
                        _training_state["progress"] = step / total if total > 0 else 0
                        _training_state["loss"] = loss

                result = adapter.train(
                    config_path="",
                    progress_callback=progress_callback,
                    epochs=epochs,
                    lora_rank=lora_rank,
                )
                with _training_lock:
                    _training_state["status"] = "done" if result.get("status") == "success" else "error"
                    _training_state["progress"] = 1.0
                    if "lora_path" in result:
                        _training_state["lora_path"] = result["lora_path"]
            except Exception as e:
                with _training_lock:
                    _training_state["status"] = "error"
                    _training_state["error"] = str(e)
                logger.error("Training failed: %s", e)

        thread = threading.Thread(target=_do_train, daemon=True)
        thread.start()

        with _training_lock:
            _training_state["status"] = "training"
            _training_state["start_time"] = time.time()

        return {"status": "started", "task": "train", "epochs": epochs}

    @app.post("/api/training/stop")
    async def stop_training(_auth: bool = Security(_verify_api_key)):
        """Stop running training"""
        with _training_lock:
            _training_state["status"] = "stopped"
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
        except (ImportError, OSError, ValueError) as e:
            return {"message": message, "style_output": "", "status": "error", "detail": str(e)}

    @app.post("/api/training/apply")
    async def apply_clone(_auth: bool = Security(_verify_api_key)):
        """Apply trained clone as active persona"""
        try:
            result_path = str(Path(__file__).parent.parent / "data" / "training")
            return {"status": "applied", "path": result_path}
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=500, detail=str(e))

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
    async def get_wechat_connection_status():
        """获取手动连接状态"""
        conn = _get_wechat_connector()
        if conn and conn.token:
            return {"status": "connected", "message": "已连接", "started_at": conn.started_at}
        return {"status": "idle", "message": "未连接"}

    @app.get("/api/channels/wechat/status")
    async def get_wechat_status():
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
    async def reconnect_wechat():
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
    async def list_users():
        """获取所有活跃用户列表"""
        if not _gf:
            return {"users": [], "total": 0}
        return {"users": _gf.get_all_users(), "total": _gf.active_user_count}

    @app.get("/api/users/{user_id}")
    async def get_user_detail(user_id: str):
        """获取某个用户详情"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        info = _gf.get_user_info(user_id)
        if not info:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return info

    @app.get("/api/users/{user_id}/chat")
    async def get_user_chat_history(user_id: str, limit: int = Query(default=50, le=200)):
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
    async def get_user_emotion(user_id: str):
        """获取某个用户的情感状态"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        info = _gf.get_user_info(user_id)
        if not info:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"user_id": user_id, "emotion": info.get("emotion", {})}

    @app.post("/api/users/{user_id}/role")
    async def set_user_role(user_id: str, card_id: str = Query(..., description="角色卡ID")):
        """给用户分配角色卡"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        ok = _gf.set_user_character(user_id, card_id)
        if not ok:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"status": "ok", "user_id": user_id, "character_card_id": card_id}

    @app.post("/api/users/{user_id}/reset")
    async def reset_user(user_id: str):
        """重置用户（记忆+情感归零）"""
        if not _gf:
            raise HTTPException(503, "女友管理器未初始化")
        ok = _gf.reset_user(user_id)
        if not ok:
            raise HTTPException(404, f"用户 {user_id} 未找到")
        return {"status": "reset", "user_id": user_id}

    @app.delete("/api/users/{user_id}")
    async def remove_user(user_id: str):
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
    async def stream_logs():
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
                except Exception:
                    pass

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
    async def get_dashboard_stats():
        """Enhanced dashboard stats — flat shape matching frontend DashboardStats type"""
        emotion_current = "-"
        affinity = 0
        energy = 0
        chats_today = 0
        facts_count = 0
        sys_status = "unknown"
        uptime = 0

        # Get training state
        with _training_lock:
            training_info = {
                "status": _training_state["status"],
                "progress": _training_state["progress"],
                "loss": _training_state["loss"],
                "extracted_turns": _training_state["extracted_turns"],
                "cleaned_turns": _training_state.get("cleaned_turns", 0),
            }

        # Get wechat status
        wechat_info = {"connected": False}
        try:
            wechat_info = await get_wechat_status()
        except Exception:
            pass

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
                    except Exception:
                        pass
        except Exception:
            pass

        # Get system stats
        if _health:
            try:
                health_data = _health.check()
                sys_status = health_data.get("status", "unknown")
                uptime = health_data.get("uptime_seconds", 0)
            except Exception:
                pass

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

    _safety_log: list = []

    def _get_safety():
        if _orch:
            return _orch.components.get("safety") if hasattr(_orch, 'components') else getattr(_orch, '_safety', None)
        return None

    @app.get("/api/safety/stats")
    async def safety_stats():
        sf = _get_safety()
        logs = _safety_log[-200:]
        categories = {}
        for entry in logs:
            cat = entry.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "enabled": sf.enabled if sf else False,
            "total_flagged": len(_safety_log),
            "recent_flagged": len(logs),
            "by_category": categories,
            "recent": logs[-20:],
        }

    @app.get("/api/safety/log")
    async def safety_log(limit: int = Query(default=50, le=200)):
        return {"log": _safety_log[-limit:]}

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
    async def rag_stats():
        rag = _get_rag()
        if rag:
            return rag.health_check()
        return {"available": False}

    @app.post("/api/rag/search")
    async def rag_search(query: str = "", top_k: int = Query(default=5, le=20)):
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
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("gbk", errors="replace")
        rag._sm.add_fact({"fact": text[:2000], "category": "upload",
                           "source": file.filename, "confidence": 1.0})
        return {"status": "indexed", "filename": file.filename, "size": len(content)}

    # ═══════════════════════════════════════════
    # Voice / TTS API
    # ═══════════════════════════════════════════

    def _get_tts():
        if _orch:
            return _orch.components.get("voice") if hasattr(_orch, 'components') else None
        return None

    @app.get("/api/voice/status")
    async def voice_status():
        tts = _get_tts()
        if tts:
            return tts.health_check()
        return {"enabled": False, "available_engines": []}

    @app.post("/api/voice/synthesize")
    async def voice_synthesize(text: str = Form(...), engine: str = Form("")):
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
    async def list_plugins():
        try:
            plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
            if plugin_path.exists():
                with open(plugin_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {"plugins": data.get("plugins", {})}
        except Exception:
            pass
        return {"plugins": {}}

    @app.post("/api/plugins/{name}/toggle")
    async def toggle_plugin(name: str, enabled: bool = True, _auth: bool = Security(_verify_api_key)):
        plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
        data = {}
        if plugin_path.exists():
            with open(plugin_path, "r", encoding="utf-8") as f:
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

    @app.post("/api/files/upload")
    async def upload_file(file: UploadFile = File(...)):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
        dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
        content = await file.read()
        with open(dest, "wb") as f:
            f.write(content)
        mime = file.content_type or "application/octet-stream"
        msg_type = "image" if mime.startswith("image/") else "voice" if mime.startswith("audio/") else "file"
        return {"status": "ok", "filename": safe_name, "size": len(content),
                "mime_type": mime, "message_type": msg_type,
                "url": f"/api/files/{dest.name}"}

    @app.get("/api/files/{filename}")
    async def serve_file(filename: str):
        file_path = UPLOAD_DIR / filename
        if not file_path.exists():
            raise HTTPException(404, "文件不存在")
        return FileResponse(file_path)

    # ═══════════════════════════════════════════
    # Proactive History API
    # ═══════════════════════════════════════════

    @app.get("/api/proactive/history")
    async def proactive_history(limit: int = Query(default=50, le=200)):
        if _orch and _orch._ase:
            ase = _orch._ase
            messages = getattr(ase, '_sent_messages', []) if hasattr(ase, '_sent_messages') else []
            return {"history": messages[-limit:], "total": len(messages)}
        return {"history": [], "total": 0}

    # ── 十四挂载 ──
    shisi_available = False
    shisi_error = None
    try:
        from shisi.api.registry import setup_shisi
        shisi_reg = setup_shisi(app, run_migrate=True)
        if orchestrator and hasattr(orchestrator, '_character_manager'):
            orchestrator._character_manager = shisi_reg.character_manager
        shisi_available = True
        logger.info("十四模块已挂载到REST API")
    except Exception as e:
        shisi_error = str(e)
        logger.error("十四模块挂载失败: %s", e)
        # Add health check endpoint to report shisi status
        @app.get("/api/shisi/status")
        async def shisi_status():
            return {"available": False, "error": shisi_error}

    # ── 微信二维码 API ──
    try:
        from api.qrcode_store import router as qrcode_router
        app.include_router(qrcode_router)
    except Exception as e:
        logger.warning("二维码API挂载失败: %s", e)

    return app
