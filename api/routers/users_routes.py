"""
多用户管理路由 — /api/users/*

来源：原 api.main_routes.py L753/761/772/791/802/813/828 共 7 端点

依赖：
- deps.gf（女友管理器）— get_user_info/set_user_character/reset_user/remove_user
- deps.orch（用户聊天历史查询）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps

logger = logging.getLogger("api.routers.users_routes")

router = APIRouter(tags=["users"])


# ═══════════════════════════════════════════════════════
# 用户列表 / 详情
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
async def get_user_chat_history(
    user_id: str,
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
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


# ═══════════════════════════════════════════════════════
# 用户角色绑定 / 重置 / 删除
# ═══════════════════════════════════════════════════════


@router.post("/api/users/{user_id}/role")
async def set_user_role(
    user_id: str,
    card_id: str = Query(..., description="角色卡ID"),
    _auth: bool = Security(verify_api_key_dep),
):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.set_user_character(user_id, card_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "ok", "user_id": user_id, "character_card_id": card_id}


@router.post("/api/users/{user_id}/reset")
async def reset_user(
    user_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.reset_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "reset", "user_id": user_id}


@router.delete("/api/users/{user_id}")
async def remove_user(
    user_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    gf = deps.gf
    if not gf:
        raise HTTPException(503, "女友管理器未初始化")
    ok = gf.remove_user(user_id)
    if not ok:
        raise HTTPException(404, f"用户 {user_id} 未找到")
    return {"status": "removed", "user_id": user_id}
