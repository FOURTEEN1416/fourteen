from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
                   session_manager=None) -> FastAPI:
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
        # Add WeChat channel — 通过 adapter 报告真实连接状态
        try:
            from wechatmsg_src.adapter import WeChatAdapter
            status = WeChatAdapter.get_status()
            channels.append({
                "id": "wechat",
                "name": "个人微信",
                "type": "wechat",
                "status": "connected" if status["connected"] else "disconnected",
                "desc": "CowAgent 扫码连接微信",
                "meta": f"在线 {status['uptime_seconds']}s" if status["connected"] else "",
            })
        except ImportError:
            pass
        # Add connected sessions as channels
        if _sessions:
            active = _sessions.get_active_sessions()
            for ses in active:
                ch_type = ses.get("channel", ses.get("channel_type", "unknown"))
                if ch_type not in [c["id"] for c in channels]:
                    channels.append({"id": ch_type, "name": ch_type.capitalize(),
                                     "status": "connected", "desc": f"活跃会话 {ses.get('session_id','')[:8]}...", "meta": "在线"})
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
        except Exception as e:
            logger.error("Config save failed: %s", e)
            raise HTTPException(400, f"Invalid config: {e}")

    @app.post("/api/proactive/config")
    async def update_proactive_config(req: ProactiveConfigRequest,
                                       _auth: bool = Security(_verify_api_key)):
        if not _orch or not _orch._ase:
            raise HTTPException(503, "Proactive engine not initialized")
        ase = _orch._ase
        if req.threshold is not None:
            ase.config["speak_threshold"] = req.threshold
        if req.max_daily is not None:
            ase.config["max_daily_messages"] = req.max_daily
        if req.min_interval_minutes is not None:
            ase.config["min_interval_minutes"] = req.min_interval_minutes
        if req.cooldown_after_reply_minutes is not None:
            ase.config["cooldown_after_reply"] = req.cooldown_after_reply_minutes
        logger.info("Proactive config updated: threshold=%s, max_daily=%s",
                     ase.config["speak_threshold"], ase.config["max_daily_messages"])
        return {"status": "ok", "config": ase.config}

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
            registry.register(tool)  # re-register (no-op if already registered)
        else:
            registry.unregister(name)
        logger.info("Tool '%s' toggled: enabled=%s", name, req.enabled)
        return {"status": "ok", "tool": name, "enabled": req.enabled}

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
        except Exception as e:
            return {"message": message, "style_output": "", "status": "error", "detail": str(e)}

    @app.post("/api/training/apply")
    async def apply_clone(_auth: bool = Security(_verify_api_key)):
        """Apply trained clone as active persona"""
        try:
            result_path = str(Path(__file__).parent.parent / "data" / "training")
            return {"status": "applied", "path": result_path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ═══════════════════════════════════════════
    # WeChat Channel API — 手动连接控制
    # ═══════════════════════════════════════════

    # 微信连接状态（API 手动控制）
    # 内部引用存在 <_wechat_connection> 字典中，无需 module-level global
    _wechat_connection: dict = {
        "status": "idle",  # idle, connecting, connected, disconnected, error
        "message": "",
        "qr_code": None,
        "pid": None,
        "started_at": None,
        "_process_mgr": None,   # _CowAgentProcess 实例（内部使用，不输出）
        "_heartbeat_mgr": None,  # _HeartbeatManager 实例
    }
    _wechat_lock = threading.Lock()

    @app.post("/api/channels/wechat/connect")
    async def manual_connect_wechat(_auth: bool = Security(_verify_api_key)):
        """手动启动微信连接（扫码登录）"""
        with _wechat_lock:
            if _wechat_connection["status"] == "connecting":
                return {"status": "connecting", "message": "正在连接中，请稍候..."}

            if _wechat_connection["status"] == "connected":
                return {"status": "connected", "message": "微信已连接"}

            _wechat_connection["status"] = "connecting"
            _wechat_connection["message"] = "正在启动 CowAgent 子进程..."

        def _do_connect():
            try:
                from cowagent_adapter.init import initialize_wechat_channel

                cowagent_cfg = str(Path(__file__).parent.parent / "cowagent_src" / "config.json")

                result = initialize_wechat_channel(
                    orchestrator=_orch,
                    enable_heartbeat=True,
                    heartbeat_interval=30,
                    heartbeat_max_missed=3,
                    cowagent_config=cowagent_cfg,
                    auto_restart=True,
                )

                with _wechat_lock:
                    if result.get("ok"):
                        _wechat_connection["status"] = "connected"
                        _wechat_connection["pid"] = result.get("pid")
                        _wechat_connection["started_at"] = time.time()
                        _wechat_connection["message"] = f"微信通道已启动 (PID={result.get('pid')})"
                        _wechat_connection["_process_mgr"] = result.get("process")
                        _wechat_connection["_heartbeat_mgr"] = result.get("heartbeat")
                    else:
                        _wechat_connection["status"] = "error"
                        _wechat_connection["message"] = result.get("message", "连接失败")

            except Exception as e:
                with _wechat_lock:
                    _wechat_connection["status"] = "error"
                    _wechat_connection["message"] = str(e)
                logger.exception("手动微信连接失败")

        thread = threading.Thread(target=_do_connect, daemon=True)
        thread.start()

        return {"status": "connecting", "message": "微信连接已触发，请查看终端二维码扫码登录"}

    @app.post("/api/channels/wechat/disconnect")
    async def manual_disconnect_wechat(_auth: bool = Security(_verify_api_key)):
        """手动断开微信连接"""
        with _wechat_lock:
            proc_mgr = _wechat_connection.get("_process_mgr")
            hb_mgr = _wechat_connection.get("_heartbeat_mgr")
            if hb_mgr:
                try:
                    hb_mgr.stop()
                except Exception:
                    pass
            if proc_mgr:
                try:
                    proc_mgr.stop()
                except Exception:
                    pass
            _wechat_connection["status"] = "disconnected"
            _wechat_connection["message"] = "微信连接已断开"
            _wechat_connection["pid"] = None
            _wechat_connection["started_at"] = None
            _wechat_connection["_process_mgr"] = None
            _wechat_connection["_heartbeat_mgr"] = None
        return {"status": "disconnected", "message": "微信已断开"}

    @app.get("/api/channels/wechat/connection-status")
    async def get_wechat_connection_status():
        """获取手动连接状态（过滤内部字段）"""
        with _wechat_lock:
            return {
                "status": _wechat_connection["status"],
                "message": _wechat_connection["message"],
                "qr_code": _wechat_connection.get("qr_code"),
                "pid": _wechat_connection.get("pid"),
                "started_at": _wechat_connection.get("started_at"),
            }

    @app.get("/api/channels/wechat/status")
    async def get_wechat_status():
        """Get detailed WeChat connection status"""
        status = {
            "connected": False,
            "uptime_seconds": 0,
            "reconnect_attempts": 0,
            "missed_heartbeats": 0,
            "messages_today": 0,
            "last_activity": "",
        }

        try:
            from cowagent_adapter._globals import bot_registry
            bot = bot_registry.get()
            if bot:
                hb = getattr(bot, '_heartbeat', None)
                if hb:
                    hb_status = hb.get_status()
                    status["connected"] = hb_status["connected"]
                    status["uptime_seconds"] = hb_status["uptime_seconds"]
                    status["reconnect_attempts"] = hb_status["reconnect_attempts"]
                    status["missed_heartbeats"] = hb_status["missed_heartbeats"]

                health = bot.health_check()
                status["components_ok"] = all(health.values()) if isinstance(health, dict) else False
        except Exception as e:
            logger.debug("Could not get wechat status: %s", e)

        return status

    @app.post("/api/channels/wechat/reconnect")
    async def reconnect_wechat():
        """Trigger WeChat reconnection"""
        try:
            from cowagent_adapter._globals import bot_registry
            bot = bot_registry.get()
            if bot:
                hb = getattr(bot, '_heartbeat', None)
                if hb and hasattr(hb, '_try_reconnect'):
                    hb._try_reconnect(None)
                    return {"status": "reconnecting"}
            return {"status": "no_heartbeat", "message": "Heartbeat not available"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

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
            from cowagent_adapter._globals import bot_registry
            bot = bot_registry.get()
            if bot:
                if hasattr(bot, 'emotion') and bot.emotion:
                    es = bot.emotion.state
                    emotion_current = es.emotion.value if hasattr(es.emotion, 'value') else str(es.emotion)
                    affinity = getattr(es, 'affinity', 0)
                    energy = getattr(es, 'energy', 0)
                if hasattr(bot, 'memory') and bot.memory:
                    try:
                        structured = bot.memory.structured_memory
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

    try:
        from shisi.api.registry import setup_shisi
        shisi_reg = setup_shisi(app, run_migrate=True)
        if orchestrator and hasattr(orchestrator, '_character_manager'):
            orchestrator._character_manager = shisi_reg.character_manager
        logger.info("十四模块已挂载到REST API")
    except Exception as e:
        logger.warning("十四模块挂载失败: %s", e)

    return app
