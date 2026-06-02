"""
聊天与会话路由 — chat/session + wechat channels

来源：原 api.main_routes.py L112/130/168/180/191/682/701/709/717/729 共 10 端点

依赖：
- deps.orch / deps.sessions / deps.gf / deps.get_wechat_connector()
- ChatRequest/ChatResponse/CreateSessionRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.responses import StreamingResponse

from api.auth import verify_api_key_dep
from api.deps import deps
from api.main_routes import ChatRequest, ChatResponse, CreateSessionRequest

logger = logging.getLogger("api._chat_routes")

router = APIRouter(tags=["chat"])


# ═══════════════════════════════════════════════════════
# Chat / Session
# ═══════════════════════════════════════════════════════


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )
    try:
        result = await orch.process_message(
            req.message, req.session_id, req.message_type, req.character_id,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="LLM response timeout",
            headers={"X-Error-Code": "LLM_TIMEOUT"},
        ) from None
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Upstream connection error",
            headers={"X-Error-Code": "NETWORK_ERROR"},
        ) from None
    return ChatResponse(
        reply=result.get("reply", ""),
        trace_id=result.get("trace_id", ""),
        emotion=result.get("emotion"),
    )


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if not orch or not hasattr(orch, "process_message_stream"):
        raise HTTPException(
            status_code=503,
            detail="Stream not available",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    async def event_generator():
        async for token in orch.process_message_stream(
            req.message, req.session_id, req.message_type, req.character_id,
        ):
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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
                        params.append(str(before))
                    where_clause = " AND ".join(conditions)
                    params.append(str(limit))
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
# WeChat Channel Management
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
