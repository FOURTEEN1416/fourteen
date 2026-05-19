from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import AsyncGenerator, Optional

from observability.logging_setup import ring_buffer

from fastapi import FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

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
    message: str
    session_id: str = ""
    message_type: str = "text"


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
    app = FastAPI(title="AI女友系统API", version="2.0")

    cors_origins = os.environ.get("API_CORS_ORIGINS", "http://localhost:*").split(",")
    if cors_origins == ["*"]:
        logger.warning("CORS allows all origins - not recommended for production")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _api_key_enabled = os.environ.get("API_KEY_ENABLED", "false").lower() == "true"
    _api_key = os.environ.get("API_KEY", "")
    _api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def _verify_api_key(api_key: Optional[str] = Security(_api_key_header)):
        if not _api_key_enabled:
            return True
        if api_key == _api_key:
            return True
        raise HTTPException(401, "Invalid or missing API key")

    if HAS_SLOWAPI:
        limiter = Limiter(key_func=get_remote_address)
        app.state.limiter = limiter

    _orch = orchestrator
    _health = health_checker
    _config = config_manager
    _sessions = session_manager

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
        if not _orch:
            raise HTTPException(503, "Orchestrator not initialized")
        result = _orch.process_message(req.message, req.session_id, req.message_type)
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
            async for token in _orch.process_message_stream(req.message, req.session_id, req.message_type):
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
            stats_data["has_orchestrator"] = True
            if _orch._emotion:
                stats_data["emotion"] = _orch._emotion.health_check()
            if _orch._memory:
                stats_data["working_count"] = _orch._memory.working.count("")
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
            import clone_training
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
            {"id": "web", "name": "Web 控制台", "status": "connected", "desc": "当前浏览器 WebSocket", "meta": "在线"},
            {"id": "api", "name": "REST API", "status": "connected", "desc": "HTTP API 接口", "meta": "端口 8000"},
        ]
        # Add WeChat channel if session manager knows about it
        try:
            from wechatmsg_src.adapter import WeChatAdapter
            channels.append({"id": "wechat", "name": "个人微信", "status": "disconnected",
                             "desc": "CowAgent 扫码连接微信", "meta": ""})
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
    async def start_extraction(target: str = "", source: str = "wcf"):
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
                    _training_state["extracted_turns"] = result.get("turns", 0)
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
    async def get_training_progress():
        """Get real-time training progress"""
        with _training_lock:
            return dict(_training_state)

    @app.post("/api/training/clean")
    async def start_cleaning(accept_score: int = 2):
        """Start LLM Judge data cleaning"""
        def _do_clean():
            try:
                from clone_training.data_cleaner import DataCleaner
                from llm_provider import get_llm
                llm = get_llm()
                cleaner = DataCleaner(llm=llm, accept_score=accept_score)
                data_dir = Path(__file__).parent.parent / "data" / "training"
                json_files = sorted(data_dir.glob("*.json"))
                if not json_files:
                    raise FileNotFoundError("No dataset found")
                latest = str(json_files[-1])
                result_path = cleaner.score_from_dataset(latest)
                with _training_lock:
                    _training_state["status"] = "cleaned"
                    _training_state["cleaned_turns"] = len(cleaner.clean(cleaner.score_batch([])))
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
    async def start_training(epochs: int = 3, lora_rank: int = 16):
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
                    _training_state["status"] = "done" if result.get("success") else "error"
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
    async def stop_training():
        """Stop running training"""
        with _training_lock:
            _training_state["status"] = "stopped"
        return {"status": "stopped"}

    @app.post("/api/training/test")
    async def test_clone(message: str):
        """Test clone output"""
        try:
            from my_character.tone_mimic import ToneMimic
            chroma_path = str(Path(__file__).parent.parent / "data" / "chroma_db")
            mimic = ToneMimic(chroma_path=chroma_path)
            style_prompt = mimic.get_style_prompt()
            return {"message": message, "style_prompt": style_prompt, "status": "ok"}
        except Exception as e:
            return {"message": message, "style_prompt": "", "status": "error", "detail": str(e)}

    @app.post("/api/training/apply")
    async def apply_clone():
        """Apply trained clone as active persona"""
        try:
            result_path = str(Path(__file__).parent.parent / "data" / "training")
            return {"status": "applied", "path": result_path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ═══════════════════════════════════════════
    # WeChat Channel API
    # ═══════════════════════════════════════════

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
        """Enhanced dashboard stats"""
        stats = {
            "system": {"status": "unknown", "uptime": 0},
            "wechat": {"connected": False},
            "training": {"status": "idle", "progress": 0},
            "emotion": {"current": "-", "affinity": 0, "energy": 0},
            "memory": {"facts_count": 0, "chats_today": 0},
        }

        # Get training state
        with _training_lock:
            stats["training"] = {
                "status": _training_state["status"],
                "progress": _training_state["progress"],
                "loss": _training_state["loss"],
                "extracted_turns": _training_state["extracted_turns"],
            }

        # Get wechat status
        try:
            wc = await get_wechat_status()
            stats["wechat"] = wc
        except Exception:
            pass

        # Try to get emotion/memory stats from running components
        try:
            from cowagent_adapter._globals import bot_registry
            bot = bot_registry.get()
            if bot:
                if hasattr(bot, 'emotion') and bot.emotion:
                    es = bot.emotion.state
                    stats["emotion"] = {
                        "current": es.emotion.value if hasattr(es.emotion, 'value') else str(es.emotion),
                        "affinity": getattr(es, 'affinity', 0),
                        "energy": getattr(es, 'energy', 0),
                    }
                if hasattr(bot, 'memory') and bot.memory:
                    try:
                        structured = bot.memory.structured_memory
                        if structured:
                            stats["memory"]["chats_today"] = structured.count_chats_today()
                    except Exception:
                        pass
        except Exception:
            pass

        # Get system stats
        if _health:
            try:
                health_data = _health.check()
                stats["system"] = {
                    "status": health_data.get("status", "unknown"),
                    "uptime": health_data.get("uptime_seconds", 0),
                }
            except Exception:
                pass

        return stats

    return app
