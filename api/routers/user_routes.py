from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Security
from pydantic import BaseModel

logger = logging.getLogger("rest_api.user")

router = APIRouter(prefix="/api", tags=["user"])

_orch = None
_gf = None
_sessions = None
_verify_api_key = None


class CreateSessionRequest(BaseModel):
    user_id: str = "default"
    channel: str = "web"


def set_dependencies(orch, gf, sessions, verify_api_key):
    global _orch, _gf, _sessions, _verify_api_key
    _orch = orch
    _gf = gf
    _sessions = sessions
    _verify_api_key = verify_api_key


@router.get("/users")
async def list_users(_auth: bool = Security(_verify_api_key)):
    if not _gf:
        return {"users": [], "total": 0}
    return {"users": _gf.get_all_users(), "total": _gf.active_user_count}


@router.get("/users/{user_id}")
async def get_user_detail(user_id: str, _auth: bool = Security(_verify_api_key)):
    if not _gf:
        raise HTTPException(503, "女友管理器未初始化")
    info = _gf.get_user_info(user_id)
    if not info:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return info


@router.get("/users/{user_id}/chat")
async def get_user_chat_history(user_id: str, limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
    if not _orch or not _orch._memory:
        return {"messages": [], "user_id": user_id}
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


@router.get("/users/{user_id}/emotion")
async def get_user_emotion(user_id: str, _auth: bool = Security(_verify_api_key)):
    if not _gf:
        raise HTTPException(503, "女友管理器未初始化")
    info = _gf.get_user_info(user_id)
    if not info:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"user_id": user_id, "emotion": info.get("emotion", {})}


@router.post("/users/{user_id}/role")
async def set_user_role(user_id: str, card_id: str = Query(..., description="角色卡ID"), _auth: bool = Security(_verify_api_key)):
    if not _gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = _gf.set_user_character(user_id, card_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "ok", "user_id": user_id, "character_card_id": card_id}


@router.post("/users/{user_id}/reset")
async def reset_user(user_id: str, _auth: bool = Security(_verify_api_key)):
    if not _gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = _gf.reset_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "reset", "user_id": user_id}


@router.delete("/users/{user_id}")
async def remove_user(user_id: str, _auth: bool = Security(_verify_api_key)):
    if not _gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = _gf.remove_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "removed", "user_id": user_id}


@router.post("/session")
async def create_session(req: CreateSessionRequest, _auth: bool = Security(_verify_api_key)):
    if _sessions:
        session_id = _sessions.create_session(req.user_id, req.channel)
        if _orch and _orch._memory:
            _orch._memory.working.start_session(session_id, req.channel)
        return {"session_id": session_id}
    return {"session_id": ""}


@router.get("/sessions")
async def list_sessions(_auth: bool = Security(_verify_api_key)):
    if _sessions:
        return {
            "sessions": _sessions.get_active_sessions(),
            "active_count": _sessions.active_count,
        }
    return {"sessions": [], "active_count": 0}
