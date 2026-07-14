"""统一记忆 API — 桥接到 shisi 的 FavoriteManager / ForwardManager"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep

logger = logging.getLogger("api.memory_routes")

router = APIRouter(prefix="/api/characters", tags=["memory"])

_fav_mgr: Any | None = None
_fwd_mgr: Any | None = None


class ForwardRequest(BaseModel):
    to_character: str
    memory_id: str
    content: str = ""


# ── 端点 ──


@router.get("/{character_id}/favorites")
async def list_favorites(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取角色收藏列表"""
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    try:
        favs = _fav_mgr.list_favorites(character_id)
        return {"favorites": favs, "total": len(favs)}
    except Exception as e:
        logger.exception("获取收藏失败 %s", character_id)
        raise HTTPException(status_code=500, detail="内部错误") from e


@router.post("/{character_id}/favorites")
async def add_favorite(
    character_id: str,
    memory_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """添加收藏"""
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    ok = _fav_mgr.favorite(character_id, memory_id)
    if not ok:
        raise HTTPException(status_code=400, detail="收藏失败，可能已存在")
    return {"status": "favorited", "character_id": character_id, "memory_id": memory_id}


@router.delete("/{character_id}/favorites/{memory_id}")
async def remove_favorite(
    character_id: str,
    memory_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """取消收藏"""
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    ok = _fav_mgr.unfavorite(character_id, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="收藏未找到")
    return {"status": "unfavorited", "character_id": character_id}


@router.post("/{character_id}/favorites/forward")
async def forward_favorite(
    character_id: str,
    req: ForwardRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """转发收藏到其他角色"""
    if _fwd_mgr is None:
        raise HTTPException(status_code=503, detail="转发管理器未初始化")
    ok = _fwd_mgr.forward(character_id, req.to_character, req.memory_id, req.content)
    if not ok:
        raise HTTPException(status_code=400, detail="转发失败")
    return {"status": "forwarded", "from": character_id, "to": req.to_character}
