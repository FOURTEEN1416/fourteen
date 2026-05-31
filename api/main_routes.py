"""
主路由模块 — 从 app_factory.py create_api_app() 闭包中抽取的 80+ 个路由

所有依赖通过 api.deps 全局单例获取，不再依赖函数闭包。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Security, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.deps import deps
from observability.logging_setup import ring_buffer

logger = logging.getLogger("main_routes")

router = APIRouter(tags=["main"])

# ── 常量 ────────────────────────────────────────────

SENSITIVE_FIELDS = {"api_key", "secret", "token", "password", "encryption_key", "api_base"}
MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
MAX_UPLOAD_SIZE = min(
    int(os.environ.get("MAX_UPLOAD_SIZE", str(50 * 1024 * 1024))),
    100 * 1024 * 1024,
)
MAX_RAG_UPLOAD_SIZE = min(
    int(os.environ.get("MAX_RAG_UPLOAD_SIZE", str(10 * 1024 * 1024))),
    50 * 1024 * 1024,
)

_wechat_lock = threading.Lock()

# ── 请求/响应模型 ─────────────────────────────────────


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


# ── 辅助函数 ──────────────────────────────────────────


def _sanitize_config(config_dict: dict) -> dict:
    """脱敏配置中的敏感字段"""
    sanitized = {}
    for k, v in config_dict.items():
        if isinstance(v, dict):
            sanitized[k] = _sanitize_config(v)
        elif k.lower() in SENSITIVE_FIELDS or any(s in k.lower() for s in SENSITIVE_FIELDS):
            sanitized[k] = "****"
        else:
            sanitized[k] = v
    return sanitized


# ═══════════════════════════════════════════════════════
# Chat / Session API
# ═══════════════════════════════════════════════════════


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized", headers={"X-Error-Code": "FEATURE_UNAVAILABLE"})
    try:
        result = await orch.process_message(req.message, req.session_id, req.message_type)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="LLM response timeout", headers={"X-Error-Code": "LLM_TIMEOUT"}) from None
    except ConnectionError:
        raise HTTPException(status_code=502, detail="Upstream connection error", headers={"X-Error-Code": "NETWORK_ERROR"}) from None
    return ChatResponse(
        reply=result.get("reply", ""),
        trace_id=result.get("trace_id", ""),
        emotion=result.get("emotion"),
    )


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not hasattr(orch, 'process_message_stream'):
        raise HTTPException(status_code=503, detail="Stream not available", headers={"X-Error-Code": "FEATURE_UNAVAILABLE"})

    async def event_generator():
        async for token in orch.process_message_stream(req.message, req.session_id, req.message_type):
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/health")
async def health():
    hc = deps.health
    if hc:
        return await hc.async_check()
    return {"status": "unknown"}


@router.get("/api/stats")
async def stats(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    sessions = deps.sessions
    stats_data: dict = {"status": "ok"}
    if orch:
        stats_data["has_orchestrator"] = True
        if orch._emotion:
            stats_data["emotion"] = orch._emotion.health_check()
        if orch._memory:
            stats_data["working_count"] = orch._memory.working.count()
        if sessions:
            stats_data["active_sessions"] = sessions.active_count
    return stats_data


@router.post("/api/session")
async def create_session(req: CreateSessionRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    sessions = deps.sessions
    if sessions:
        session_id = sessions.create_session(req.user_id, req.channel)
        if orch and orch._memory:
            orch._memory.working.start_session(session_id, req.channel)
        return {"session_id": session_id}
    return {"session_id": ""}


@router.get("/api/sessions")
async def list_sessions(_auth: bool = Security(verify_api_key_dep)):
    sessions = deps.sessions
    if sessions:
        return {
            "sessions": sessions.get_active_sessions(),
            "active_count": sessions.active_count,
        }
    return {"sessions": [], "active_count": 0}


@router.get("/api/chat/history")
async def chat_history(
    session_id: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    before: int = Query(default=0, ge=0, description="Timestamp to load messages before"),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if not orch or not orch._memory:
        return {"messages": []}
    if session_id:
        sm = getattr(orch._memory, "structured_memory", None) or getattr(orch._memory, "_sm", None)
        if sm and hasattr(sm, "get_connection"):
            try:
                with sm.get_connection() as conn:
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
    messages = orch._memory.working.get_recent(limit)
    return {"messages": messages, "session_id": session_id}


# ═══════════════════════════════════════════════════════
# Emotion / Persona API
# ═══════════════════════════════════════════════════════


@router.get("/api/emotion/state", response_model=EmotionStateResponse)
async def emotion_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._emotion:
        health_data = orch._emotion.health_check()
        return EmotionStateResponse(
            current_emotion=health_data.get("current_emotion", ""),
            intensity=health_data.get("intensity", 0.0),
            energy=health_data.get("energy", 0.0),
            affinity=health_data.get("affinity", 0.0),
        )
    return EmotionStateResponse()


@router.get("/api/emotion/trend")
async def emotion_trend(days: int = Query(default=7, ge=1, le=30), _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"trend": [], "days": days}
    trend = getattr(orch._emotion, '_emotion_history', [])
    return {"trend": trend[-days * 20:], "days": days}


@router.get("/api/persona/profile")
async def persona_profile(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._persona:
        return {
            "core_character": orch._persona.profile.core_character,
            "speaking_style": orch._persona.profile.speaking_style,
            "emotional_preference": orch._persona.profile.emotional_preference,
        }
    return {}


@router.get("/api/persona/evolution-log")
async def persona_evolution_log(limit: int = Query(default=50, ge=1, le=500), _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._persona:
        return {"log": orch._persona.get_evolution_log(limit)}
    return {"log": []}


# ═══════════════════════════════════════════════════════
# 用户心理画像 API
# ═══════════════════════════════════════════════════════


def _get_pe():
    orch = deps.orch
    if orch and hasattr(orch, 'components'):
        return orch.components.get("persona_extractor")
    return None


@router.get("/api/psych/profile")
async def psych_profile(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None:
        return {"user_id": "default", "status": "unavailable", "snapshots": 0}
    return pe.get_user_profile_summary()


@router.get("/api/psych/snapshots")
async def psych_snapshots(limit: int = Query(default=20, ge=1, le=200), _auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None:
        return {"snapshots": []}
    snaps = pe.bank.get_recent_snapshots(user_id=pe.user_id, limit=limit)
    return {"snapshots": [s.to_dict() for s in snaps]}


@router.delete("/api/psych/profile")
async def reset_psych_profile(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None:
        raise HTTPException(503, "PersonaExtractor未初始化")
    ok = pe.bank.clear_user(pe.user_id)
    return {"status": "reset" if ok else "failed"}


@router.get("/api/psych/mental-health")
async def psych_mental_health(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
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


@router.get("/api/psych/liwc")
async def psych_liwc(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None or not pe.liwc:
        return {"available": False}
    persona = pe.bank.get_persona(pe.user_id)
    if persona is None or not persona.liwc:
        return {"available": True, "data": None}
    return {"available": True, "data": persona.liwc}


@router.get("/api/memory/facts")
async def memory_facts(category: str | None = None, limit: int = Query(default=50), _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._memory:
        return {"facts": orch._memory.semantic.get_facts(category, limit=limit)}
    return {"facts": []}


@router.get("/api/tools")
async def tools_list(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._tools:
        return {"tools": orch._tools.registry.tool_names}
    return {"tools": []}


# ═══════════════════════════════════════════════════════
# Training / Clone Pipeline API
# ═══════════════════════════════════════════════════════


@router.get("/api/training/status")
async def training_status(_auth: bool = Security(verify_api_key_dep)):
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


@router.get("/api/proactive/state")
async def proactive_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._ase:
        return orch._ase.health_check()
    return {}


@router.get("/api/logs")
async def get_logs(limit: int = Query(default=100, le=200), level: str = Query(default="all"),
                   search: str = Query(default=""), _auth: bool = Security(verify_api_key_dep)):
    return {"logs": ring_buffer.get_recent(limit=limit, level=level, search=search)}


@router.get("/api/channels")
async def list_channels(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
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


@router.get("/api/config")
async def get_config(_auth: bool = Security(verify_api_key_dep)):
    cfg = deps.config
    if cfg:
        return _sanitize_config(cfg.config.model_dump())
    return {}


@router.post("/api/config")
async def save_config(req: ConfigUpdateRequest, _auth: bool = Security(verify_api_key_dep)):
    cfg = deps.config
    if not cfg:
        raise HTTPException(503, "Config manager not initialized")
    try:
        updated = cfg.save(req.config)
        return _sanitize_config(updated.model_dump())
    except (ValueError, TypeError, KeyError, AttributeError):
        logger.exception("Config save failed")
        raise HTTPException(400, "Invalid config") from None


@router.post("/api/proactive/config")
async def update_proactive_config(req: ProactiveConfigRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not orch._ase:
        raise HTTPException(503, "Proactive engine not initialized")
    ase = orch._ase
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


@router.post("/api/tools/{name}/toggle")
async def toggle_tool(name: str, req: ToolToggleRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not orch._tools or not orch._tools.registry:
        raise HTTPException(503, "Tool system not initialized")
    registry = orch._tools.registry
    tool = registry.get(name)
    if not tool:
        raise HTTPException(404, f"Tool not found: {name}")
    if req.enabled:
        registry.register(tool)
    else:
        registry.unregister(name)
    deps.tool_history_mgr.append({
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "tool": name, "action": "enable" if req.enabled else "disable",
    })
    logger.info("Tool '%s' toggled: enabled=%s", name, req.enabled)
    return {"status": "ok", "tool": name, "enabled": req.enabled}


@router.get("/api/tools/history")
async def tool_history(limit: int = Query(default=50, le=200), _auth: bool = Security(verify_api_key_dep)):
    return {"history": deps.tool_history_mgr.get_recent(limit)}


@router.post("/api/training/extract")
async def start_extraction(target: str = "", source: str = "wcf", _auth: bool = Security(verify_api_key_dep)):
    def _do_extract():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent / "data" / "training"),
            )
            result = adapter.extract(target=target, source=source)
            deps.training_mgr.update(
                status="extracted",
                extracted_turns=len(result) if isinstance(result, list) else result.get("turns", 0),
                progress=0.3,
                step_name="数据提取",
            )
        except Exception:
            logger.exception("Extraction failed")
            deps.training_mgr.update(status="error", error="internal_error")

    if not target.strip():
        raise HTTPException(status_code=400, detail="target is required")

    deps.training_mgr.update(status="extracting", start_time=time.time(), step_name="数据提取")
    deps.training_mgr.submit(_do_extract)
    return {"status": "started", "task": "extract", "target": target}


@router.get("/api/training/progress")
async def get_training_progress(_auth: bool = Security(verify_api_key_dep)):
    return deps.training_mgr.get_state()


@router.post("/api/training/clean")
async def start_cleaning(accept_score: int = 2, _auth: bool = Security(verify_api_key_dep)):
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
            deps.training_mgr.update(
                status="cleaned",
                cleaned_turns=cleaned_count,
                progress=0.6,
                step_name="数据清洗",
            )
        except Exception:
            logger.exception("Cleaning failed")
            deps.training_mgr.update(status="error", error="internal_error")

    deps.training_mgr.update(status="cleaning", start_time=time.time(), step_name="数据清洗")
    deps.training_mgr.submit(_do_clean)
    return {"status": "started", "task": "clean", "accept_score": accept_score}


@router.post("/api/training/train")
async def start_training(epochs: int = 3, lora_rank: int = 16, _auth: bool = Security(verify_api_key_dep)):
    def _do_train():
        try:
            from weclone_adapter import WeCloneAdapter
            adapter = WeCloneAdapter(
                data_dir=str(Path(__file__).parent.parent / "data" / "clone"),
                output_dir=str(Path(__file__).parent.parent / "data" / "training"),
            )

            def progress_callback(step, total, loss):
                deps.training_mgr.update(
                    current_step=step,
                    total_steps=total,
                    progress=step / total if total > 0 else 0,
                    loss=loss,
                )
                if deps.training_mgr.is_stopping:
                    raise InterruptedError("Training stopped by user")

            result = adapter.train(
                config_path="",
                progress_callback=progress_callback,
                epochs=epochs,
                lora_rank=lora_rank,
            )
            deps.training_mgr.update(
                status="done" if result.get("status") == "success" else "error",
                progress=1.0,
                step_name="模型训练",
            )
            if "lora_path" in result:
                deps.training_mgr.update(lora_path=result["lora_path"])
        except InterruptedError:
            deps.training_mgr.update(status="stopped")
        except Exception:
            logger.exception("Training failed")
            deps.training_mgr.update(status="error", error="internal_error")

    deps.training_mgr.update(status="training", start_time=time.time(), step_name="模型训练")
    deps.training_mgr.submit(_do_train)
    return {"status": "started", "task": "train", "epochs": epochs}


@router.post("/api/training/stop")
async def stop_training(_auth: bool = Security(verify_api_key_dep)):
    deps.training_mgr.stop()
    return {"status": "stopped"}


@router.post("/api/training/test")
async def test_clone(message: str, _auth: bool = Security(verify_api_key_dep)):
    try:
        from my_character.tone_mimic import ToneMimic
        chroma_path = str(Path(__file__).parent.parent / "data" / "chroma_db")
        mimic = ToneMimic(chroma_path=chroma_path)
        style_prompt = mimic.get_style_prompt()
        return {"message": message, "style_output": style_prompt, "status": "ok"}
    except (ImportError, OSError, ValueError):
        logger.exception("Test clone failed")
        return {"message": message, "style_output": "", "status": "error", "detail": "internal_error"}


@router.post("/api/training/apply")
async def apply_clone(_auth: bool = Security(verify_api_key_dep)):
    try:
        result_path = str(Path(__file__).parent.parent / "data" / "training")
        return {"status": "applied", "path": result_path}
    except (ValueError, OSError):
        logger.exception("Apply clone failed")
        raise HTTPException(status_code=500, detail="internal_error") from None


# ═══════════════════════════════════════════════════════
# WeChat Channel API
# ═══════════════════════════════════════════════════════


@router.post("/api/channels/wechat/connect")
async def manual_connect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        return {"status": "connected", "message": "微信已连接"}

    def _do_connect():
        try:
            from wechat_direct import WeChatConnector
            connector = WeChatConnector(deps.orch)
            connector.run()
        except Exception as e:
            logger.exception("微信连接失败: %s", e)

    thread = threading.Thread(target=_do_connect, daemon=True)
    thread.start()
    return {"status": "connecting", "message": "微信连接已触发，请看终端/页面二维码扫码登录"}


@router.post("/api/channels/wechat/disconnect")
async def manual_disconnect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn:
        conn.stop()
    return {"status": "disconnected", "message": "微信已断开"}


@router.get("/api/channels/wechat/connection-status")
async def get_wechat_connection_status(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        return {"status": "connected", "message": "已连接", "started_at": conn.started_at}
    return {"status": "idle", "message": "未连接"}


@router.get("/api/channels/wechat/status")
async def get_wechat_status(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        return {
            "connected": True,
            "uptime_seconds": time.time() - conn.started_at if conn.started_at else 0,
            "bot_id": conn.bot_id,
        }
    return {"connected": False, "uptime_seconds": 0}


@router.post("/api/channels/wechat/reconnect")
async def reconnect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        conn.stop()

    def _do_reconnect():
        import wechat_direct.wechat_connector as wc
        from wechat_direct import WeChatConnector
        time.sleep(1)
        wc._clear_credentials()
        new_conn = WeChatConnector(deps.gf)
        new_conn.run()

    thread = threading.Thread(target=_do_reconnect, daemon=True)
    thread.start()
    return {"status": "reconnecting"}


# ═══════════════════════════════════════════════════════
# 多用户管理 API
# ═══════════════════════════════════════════════════════


@router.get("/api/users")
async def list_users(_auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        return {"users": [], "total": 0}
    return {"users": gf.get_all_users(), "total": gf.active_user_count}


@router.get("/api/users/{user_id}")
async def get_user_detail(user_id: str, _auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    info = gf.get_user_info(user_id)
    if not info:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return info


@router.get("/api/users/{user_id}/chat")
async def get_user_chat_history(user_id: str, limit: int = Query(default=50, le=200), _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not orch._memory:
        return {"messages": [], "user_id": user_id}
    sm = getattr(orch._memory, "structured_memory", None) or getattr(orch._memory, "_sm", None)
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


@router.get("/api/users/{user_id}/emotion")
async def get_user_emotion(user_id: str, _auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    info = gf.get_user_info(user_id)
    if not info:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"user_id": user_id, "emotion": info.get("emotion", {})}


@router.post("/api/users/{user_id}/role")
async def set_user_role(user_id: str, card_id: str = Query(..., description="角色卡ID"), _auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.set_user_character(user_id, card_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "ok", "user_id": user_id, "character_card_id": card_id}


@router.post("/api/users/{user_id}/reset")
async def reset_user(user_id: str, _auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.reset_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "reset", "user_id": user_id}


@router.delete("/api/users/{user_id}")
async def remove_user(user_id: str, _auth: bool = Security(verify_api_key_dep)):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.remove_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "removed", "user_id": user_id}


# ═══════════════════════════════════════════════════════
# Clone Data Management API
# ═══════════════════════════════════════════════════════


@router.get("/api/clone/contacts")
async def list_clone_contacts(keyword: str = "", _auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword)
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/api/clone/datasets")
async def list_clone_datasets(_auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    datasets = mgr.list_datasets()
    return {"datasets": datasets, "total": len(datasets)}


@router.get("/api/clone/datasets/{person_id}")
async def get_clone_dataset_detail(
    person_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    only_user: bool = Query(default=False),
    _auth: bool = Security(verify_api_key_dep),
):
    mgr = deps.get_clone_mgr()
    return mgr.get_dataset_detail(
        person_id=person_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_user=only_user,
    )


@router.delete("/api/clone/datasets/{person_id}")
async def delete_clone_dataset(person_id: str, _auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_dataset(person_id)
    if not ok:
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    return {"status": "deleted", "person_id": person_id}


@router.delete("/api/clone/datasets/{person_id}/conversation")
async def delete_clone_conversation(
    person_id: str,
    index: int = Query(..., description="对话索引（从0开始）"),
    _auth: bool = Security(verify_api_key_dep),
):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_conversation(person_id, index)
    if not ok:
        raise HTTPException(404, "对话未找到或删除失败")
    return {"status": "deleted", "person_id": person_id, "index": index}


@router.post("/api/clone/datasets/{person_id}/conversations/batch-delete")
async def batch_delete_clone_conversations(
    person_id: str,
    indices: list[int] = Query(..., description="要删除的索引列表"),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
):
    mgr = deps.get_clone_mgr()
    deleted = mgr.batch_delete_conversations(person_id, indices)
    return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}


@router.get("/api/clone/stats")
async def get_clone_stats(_auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    return mgr.get_stats()


# ═══════════════════════════════════════════════════════
# Real-time Log Stream (SSE)
# ═══════════════════════════════════════════════════════


@router.get("/api/logs/stream")
async def stream_logs(_auth: bool = Security(verify_api_key_dep)):
    async def event_generator():
        queue = asyncio.Queue(maxsize=100)
        loop = asyncio.get_event_loop()

        log_queue_handler = logging.Handler()
        log_queue_handler.setLevel(logging.INFO)

        def emit(record):
            try:
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
# Dashboard Enhanced Stats
# ═══════════════════════════════════════════════════════


@router.get("/api/stats/dashboard")
async def get_dashboard_stats(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    sessions = deps.sessions
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
    if cache["data"] is not None and (now - cache["ts"]) < deps.WECHAT_STATUS_TTL:
        wechat_info = cache["data"]
    else:
        wechat_info = {"connected": False}
        try:
            wechat_info = await get_wechat_status()
        except Exception as e:
            logger.debug("Failed to get wechat status for dashboard: %s", e)
        cache["data"] = wechat_info
        cache["ts"] = now

    try:
        if orch:
            emotion = (orch.components.get("emotion") if hasattr(orch, 'components')
                       else getattr(orch, '_emotion', None))
            memory = (orch.components.get("memory") if hasattr(orch, 'components')
                      else getattr(orch, '_memory', None))
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
# Safety Dashboard API
# ═══════════════════════════════════════════════════════


@router.get("/api/safety/stats")
async def safety_stats(_auth: bool = Security(verify_api_key_dep)):
    sf = deps.get_safety()
    return deps.safety_log_mgr.get_stats(enabled=sf.enabled if sf else False)


@router.get("/api/safety/log")
async def safety_log(limit: int = Query(default=50, le=200), _auth: bool = Security(verify_api_key_dep)):
    return {"log": deps.safety_log_mgr.get_recent(limit)}


@router.post("/api/safety/config")
async def safety_config(enabled: bool = True, _auth: bool = Security(verify_api_key_dep)):
    sf = deps.get_safety()
    if sf:
        sf.enabled = enabled
        return {"status": "ok", "enabled": enabled}
    return {"status": "not_available"}


# ═══════════════════════════════════════════════════════
# RAG Knowledge Base API
# ═══════════════════════════════════════════════════════


@router.get("/api/rag/stats")
async def rag_stats(_auth: bool = Security(verify_api_key_dep)):
    rag = deps.get_rag()
    if rag:
        return rag.health_check()
    return {"available": False}


@router.post("/api/rag/search")
async def rag_search(query: str = "", top_k: int = Query(default=5, le=20), _auth: bool = Security(verify_api_key_dep)):
    rag = deps.get_rag()
    if not rag:
        raise HTTPException(503, "RAG引擎未初始化")
    results = rag.retrieve(query, top_k=top_k)
    return {"query": query, "results": results.get("results", []),
            "total_vector": results.get("total_vector", 0),
            "total_keyword": results.get("total_keyword", 0)}


@router.post("/api/rag/documents")
async def rag_upload_document(file: UploadFile = File(...), _auth: bool = Security(verify_api_key_dep)):  # noqa: B008
    rag = deps.get_rag()
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


# ═══════════════════════════════════════════════════════
# Voice / TTS API
# ═══════════════════════════════════════════════════════


@router.get("/api/voice/status")
async def voice_status(_auth: bool = Security(verify_api_key_dep)):
    tts = deps.get_tts()
    if tts:
        return tts.health_check()
    return {"enabled": False, "available_engines": []}


@router.post("/api/voice/synthesize")
async def voice_synthesize(text: str = Form(...), engine: str = Form(""), _auth: bool = Security(verify_api_key_dep)):
    tts = deps.get_tts()
    if not tts or not tts.enabled:
        raise HTTPException(503, "TTS未启用")
    if engine and engine in tts.available_engines:
        await tts.switch_engine(engine)
    audio = await tts.synthesize(text)
    if audio is None:
        raise HTTPException(500, "语音合成失败")
    return Response(content=audio, media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=tts.wav"})


# ═══════════════════════════════════════════════════════
# Plugin Management API
# ═══════════════════════════════════════════════════════


@router.get("/api/plugins")
async def list_plugins(_auth: bool = Security(verify_api_key_dep)):
    try:
        plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
        if plugin_path.exists():
            with open(plugin_path, encoding="utf-8") as f:
                data = json.load(f)
            return {"plugins": data.get("plugins", {})}
    except Exception as e:
        logger.debug("Failed to load plugins config: %s", e)
    return {"plugins": {}}


@router.post("/api/plugins/{name}/toggle")
async def toggle_plugin(name: str, enabled: bool = True, _auth: bool = Security(verify_api_key_dep)):
    plugin_path = Path(__file__).parent.parent / "plugins" / "plugins.json"
    data = {}
    if plugin_path.exists():
        with open(plugin_path, encoding="utf-8") as f:
            data = json.load(f)
    plugins = data.get("plugins", {})
    if name not in plugins:
        plugins[name] = {}
    plugins[name]["enabled"] = enabled
    plugins[name]["toggled_at"] = datetime.now(tz=timezone.utc).isoformat()
    data["plugins"] = plugins
    with open(plugin_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "name": name, "enabled": enabled}


# ═══════════════════════════════════════════════════════
# Multimodal File Upload
# ═══════════════════════════════════════════════════════


@router.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...), _auth: bool = Security(verify_api_key_dep)):  # noqa: B008
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)  # type: ignore[arg-type]
    dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
    with open(dest, "wb") as f:
        f.write(content)
    mime = file.content_type or "application/octet-stream"
    msg_type = "image" if mime.startswith("image/") else "voice" if mime.startswith("audio/") else "file"
    return {"status": "ok", "filename": safe_name, "size": len(content),
            "mime_type": mime, "message_type": msg_type,
            "url": f"/api/files/{dest.name}"}


@router.get("/api/files/{filename}")
async def serve_file(filename: str, _auth: bool = Security(verify_api_key_dep)):
    safe_name = os.path.basename(filename)
    file_path = (UPLOAD_DIR / safe_name).resolve()
    upload_dir_resolved = UPLOAD_DIR.resolve()
    if not str(file_path).startswith(str(upload_dir_resolved)):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")
    if not file_path.is_file():
        raise HTTPException(400, "Not a file")
    return FileResponse(file_path)


# ═══════════════════════════════════════════════════════
# Proactive History API
# ═══════════════════════════════════════════════════════


@router.get("/api/proactive/history")
async def proactive_history(limit: int = Query(default=50, le=200), _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._ase:
        ase = orch._ase
        messages = getattr(ase, '_sent_messages', []) if hasattr(ase, '_sent_messages') else []
        return {"history": messages[-limit:], "total": len(messages)}
    return {"history": [], "total": 0}


# ═══════════════════════════════════════════════════════
# Cache Statistics API
# ═══════════════════════════════════════════════════════


@router.get("/api/cache/stats")
async def cache_stats(_auth: bool = Security(verify_api_key_dep)):
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


@router.post("/api/cache/invalidate")
async def cache_invalidate(pattern: str = "*", _auth: bool = Security(verify_api_key_dep)):
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
        raise HTTPException(500, "Cache invalidation failed") from None


# ═══════════════════════════════════════════════════════
# Shisi Status API
# ═══════════════════════════════════════════════════════


@router.get("/api/routes")
async def list_routes(request: Request):
    routes = []
    for route in request.app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            routes.append({"path": route.path, "methods": list(route.methods)})
    return {"routes": routes, "total": len(routes)}
