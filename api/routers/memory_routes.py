"""统一记忆 API — 桥接到 shisi 的 FavoriteManager / ForwardManager"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
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
    principal: AuthPrincipal | None = Security(get_optional_principal),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    _owned: dict = Depends(require_character_access),
):
    """跨角色转发：生成可追溯目标侧派生记录（缺陷 F 恢复）。

    目标卡归属与源卡同口径收口（PRIV-2）：有 Bearer 主体时，目标卡不存在 /
    无权一律 404（复用 require_character_access 同文案防枚举）；公共目标卡
    （owner 为空）按 card_access_allowed 同一判定，不特判；机器面（无 Bearer）
    不收窄。context 已挂载 `forwarded_notes`（含 forward_id/from 可追溯字段）；
    ⚠️ prompt 渲染层尚未消费——接线前不得按「已进 prompt」的假设消费该字段。
    返回真实回执。
    """
    fwd_mgr = getattr(deps.shisi_reg, "forward_manager", None)
    if fwd_mgr is None:
        raise HTTPException(status_code=503, detail="转发管理器未初始化")
    # PRIV-2：目标卡与源卡同一 owner、同一口径——机器面（principal None）不干预；
    # 有主体时目标不存在/无权一律 404（同文案防枚举），杜绝向他人私人卡直写便签。
    await require_character_access(req.to_character, principal)
    receipt = fwd_mgr.forward_receipt(
        character_id, req.to_character, req.memory_id, req.content
    )
    if not receipt.get("ok"):
        raise HTTPException(status_code=400, detail=f"转发失败: {receipt.get('error')}")
    return {
        "status": "forwarded",
        "from": character_id,
        "to": req.to_character,
        "forward_id": receipt.get("forward_id"),
        "memory_id": req.memory_id,
        "target_side": "derived_note",
    }


@router.get("/{character_id}/forwards")
async def list_forwards(
    character_id: str,
    limit: int = 20,
    _auth: bool = Security(verify_api_key_dep),
    _owned: dict = Depends(require_character_access),
):
    """目标角色收到的转发派生记录（缺陷 F 消费面）。"""
    fwd_mgr = getattr(deps.shisi_reg, "forward_manager", None)
    if fwd_mgr is None:
        raise HTTPException(status_code=503, detail="转发管理器未初始化")
    items = fwd_mgr.get_forwards(character_id)[: max(1, min(int(limit), 100))]
    return {"forwards": items, "total": len(items)}
