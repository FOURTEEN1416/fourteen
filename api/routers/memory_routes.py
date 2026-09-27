"""统一记忆 API — 桥接到 shisi 的 FavoriteManager / ForwardManager"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.deps import deps
from api.routers.character_routes import require_character_access

logger = logging.getLogger("api.memory_routes")

router = APIRouter(prefix="/api/characters", tags=["memory"])


class ForwardRequest(BaseModel):
    to_character: str
    memory_id: str
    content: str = ""


# ── 端点 ──


@router.get("/{character_id}/favorites")
async def list_favorites(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """获取角色收藏列表"""
    fav_mgr = getattr(deps.shisi_reg, "favorite_manager", None)
    if fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    try:
        favs = fav_mgr.list_favorites(character_id)
        return {"favorites": favs, "total": len(favs)}
    except Exception as e:
        logger.exception("获取收藏失败 %s", character_id)
        raise HTTPException(status_code=500, detail="内部错误") from e


@router.post("/{character_id}/favorites")
async def add_favorite(
    character_id: str,
    memory_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """添加收藏"""
    fav_mgr = getattr(deps.shisi_reg, "favorite_manager", None)
    if fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    ok = fav_mgr.favorite(character_id, memory_id)
    if not ok:
        raise HTTPException(status_code=400, detail="收藏失败，可能已存在")
    return {"status": "favorited", "character_id": character_id, "memory_id": memory_id}


@router.delete("/{character_id}/favorites/{memory_id}")
async def remove_favorite(
    character_id: str,
    memory_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """取消收藏"""
    fav_mgr = getattr(deps.shisi_reg, "favorite_manager", None)
    if fav_mgr is None:
        raise HTTPException(status_code=503, detail="收藏管理器未初始化")
    ok = fav_mgr.unfavorite(character_id, memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="收藏未找到")
    return {"status": "unfavorited", "character_id": character_id}


@router.post("/{character_id}/favorites/forward")
async def forward_favorite(
    character_id: str,
    req: ForwardRequest,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """跨角色转发**未生效**（缺陷 F，2026-09-27 W4：501 先于任何写动作）。

    `memory_forwards` 落行后目标角色无任何消费者——无 GET forwards 端点、
    对话/检索链不读该表，旧响应 `forwarded` 属谎报成功（删除面 501 同法）。
    恢复条件：目标侧先接入授权派生记录（可追溯、进检索/上下文）再放开本面。
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "跨角色转发尚未生效：memory_forwards 落库后目标侧无消费链"
            "（无读取端点、不进对话上下文），请先接入目标侧派生记录再放开"
        ),
    )
