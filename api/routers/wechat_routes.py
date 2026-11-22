"""微信绑定管理 API — /api/wechat/bind*（连接管理已随 D16 退役，见 DELETION_LOG 2026-11-22）"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy import select

from api.auth_jwt import get_current_user_id
from api.database import WechatBinding, get_db
from api.deps import deps

logger = logging.getLogger("api.wechat_routes")

router = APIRouter(prefix="/api/wechat", tags=["wechat"])


# ═══════════════════════════════════════════════════════
# 微信绑定管理（JWT 鉴权）
# ═══════════════════════════════════════════════════════


class WechatBindRequest(BaseModel):
    wxid: str
    nickname: str = ""
    avatar: str = ""


class WechatBindUpdate(BaseModel):
    nickname: str | None = None
    character_card_id: str | None = None


@router.post("/bind", status_code=201)
async def bind_wechat(
    req: WechatBindRequest,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    """将微信账号绑定到当前登录用户"""
    if not req.wxid.strip():
        raise HTTPException(status_code=400, detail="wxid 不能为空")
    if len(req.wxid) > 255:
        raise HTTPException(status_code=400, detail="wxid 过长")

    # 查重：同一 WXID 是否已被他人绑定
    result = await db.execute(
        select(WechatBinding).where(WechatBinding.wxid == req.wxid)
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.user_id != user_id:
            raise HTTPException(status_code=409, detail="该微信已被其他账号绑定")
        # 已绑定到当前用户，更新信息
        if req.nickname:
            existing.nickname = req.nickname
        if req.avatar:
            existing.avatar = req.avatar
        await db.flush()
        binding = existing
    else:
        binding = WechatBinding(
            user_id=user_id,
            wxid=req.wxid,
            nickname=req.nickname,
            avatar=req.avatar,
        )
        db.add(binding)
        await db.flush()

    # ── 先 commit，再更新缓存 ──
    await db.commit()
    await db.refresh(binding)

        # ── DB 确认后，同步到 UserManager ──
    gf = deps.gf
    if gf:
        await gf.upsert_binding(req.wxid, {
            "wxid": req.wxid,
            "user_id": user_id,
            "nickname": req.nickname,
            "character_card_id": binding.character_card_id,
        })

    return {"status": "bound", "wxid": req.wxid, "binding": binding.to_dict()}


@router.get("/bindings")
async def list_my_bindings(
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    """获取当前用户的所有微信绑定"""
    result = await db.execute(
        select(WechatBinding).where(WechatBinding.user_id == user_id)
        .order_by(WechatBinding.bound_at.desc())
    )
    bindings = result.scalars().all()
    return {
        "bindings": [b.to_dict() for b in bindings],
        "total": len(bindings),
    }


@router.put("/bindings/{wxid}")
async def update_binding(
    wxid: str,
    req: WechatBindUpdate,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    """更新绑定信息（昵称 / 角色卡）"""
    result = await db.execute(
        select(WechatBinding).where(
            WechatBinding.wxid == wxid,
            WechatBinding.user_id == user_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        raise HTTPException(status_code=404, detail="绑定不存在")

    changed = {}
    if req.nickname is not None:
        binding.nickname = req.nickname
        changed["nickname"] = req.nickname
    if req.character_card_id is not None:
        binding.character_card_id = req.character_card_id
        changed["character_card_id"] = req.character_card_id

    if changed:
        await db.commit()
        await db.refresh(binding)

    # ── DB 确认后，同步到 UserManager ──
        gf = deps.gf
        if gf:
            await gf.upsert_binding(wxid, changed)

    return {"status": "updated", "wxid": wxid, "binding": binding.to_dict()}


@router.delete("/bindings/{wxid}")
async def unbind_wechat(
    wxid: str,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    """解除微信绑定"""
    result = await db.execute(
        select(WechatBinding).where(
            WechatBinding.wxid == wxid,
            WechatBinding.user_id == user_id,
        )
    )
    binding = result.scalar_one_or_none()
    if not binding:
        raise HTTPException(status_code=404, detail="绑定不存在")

    await db.delete(binding)
    await db.commit()

    # 清理 UserManager 缓存
    gf = deps.gf
    if gf:
        await gf.remove_binding(wxid)

    return {"status": "unbound", "wxid": wxid}
