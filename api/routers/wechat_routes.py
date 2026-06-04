"""微信连接持久化管理 API — 保存/管理已连接的微信账号 + 微信绑定管理"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select

from api.auth_jwt import get_current_user_id
from api.database import WechatBinding, get_db
from api.deps import deps

logger = logging.getLogger("api.wechat_routes")

router = APIRouter(prefix="/api/wechat", tags=["wechat"])

_verify_api_key_func: Callable | None = None
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

CONNECTIONS_FILE = Path(__file__).parent.parent.parent / "data" / "wechat_connections.json"


async def _verify_api_key(api_key: str | None = Security(_api_key_header)):
    if _verify_api_key_func is not None:
        return await _verify_api_key_func(api_key)
    return True


def set_dependencies(verify_api_key: Callable) -> None:
    global _verify_api_key_func
    _verify_api_key_func = verify_api_key


class WechatConnectionCreate(BaseModel):
    wxid: str
    nickname: str = ""
    alias: str = ""


class WechatConnectionUpdate(BaseModel):
    nickname: str | None = None
    alias: str | None = None


def _load_connections() -> dict[str, Any]:
    if CONNECTIONS_FILE.exists():
        try:
            with open(CONNECTIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("读取连接列表失败: %s", e)
    return {"connections": [], "last_sync": 0}


def _save_connections(data: dict) -> bool:
    CONNECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONNECTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error("保存连接列表失败: %s", e)
        return False


@router.get("/connections")
def list_connections(_auth: bool = Security(_verify_api_key)):
    data = _load_connections()
    return {
        "connections": data.get("connections", []),
        "total": len(data.get("connections", [])),
        "last_sync": data.get("last_sync", 0),
    }


@router.post("/connections", status_code=201)
def save_connection(req: WechatConnectionCreate, _auth: bool = Security(_verify_api_key)):
    if not req.wxid.strip():
        raise HTTPException(status_code=400, detail="wxid 不能为空")
    data = _load_connections()
    conns = data["connections"]
    existing = next((c for c in conns if c.get("wxid") == req.wxid), None)
    now = time.time()
    if existing:
        existing["nickname"] = req.nickname or existing["nickname"]
        existing["alias"] = req.alias or existing["alias"]
        existing["updated_at"] = now
    else:
        conns.append({
            "wxid": req.wxid,
            "nickname": req.nickname,
            "alias": req.alias,
            "created_at": now,
            "updated_at": now,
        })
    data["last_sync"] = now
    if not _save_connections(data):
        raise HTTPException(status_code=500, detail="保存连接失败")
    return {"status": "saved", "wxid": req.wxid}


@router.put("/connections/{wxid}")
def update_connection(wxid: str, req: WechatConnectionUpdate, _auth: bool = Security(_verify_api_key)):
    data = _load_connections()
    conns = data["connections"]
    existing = next((c for c in conns if c.get("wxid") == wxid), None)
    if not existing:
        raise HTTPException(status_code=404, detail=f"连接不存在: {wxid}")
    if req.nickname is not None:
        existing["nickname"] = req.nickname
    if req.alias is not None:
        existing["alias"] = req.alias
    existing["updated_at"] = time.time()
    data["last_sync"] = time.time()
    if not _save_connections(data):
        raise HTTPException(status_code=500, detail="保存连接失败")
    return {"status": "updated", "wxid": wxid}


@router.delete("/connections/{wxid}")
def delete_connection(wxid: str, _auth: bool = Security(_verify_api_key)):
    data = _load_connections()
    conns = data["connections"]
    new_conns = [c for c in conns if c.get("wxid") != wxid]
    if len(new_conns) == len(conns):
        raise HTTPException(status_code=404, detail=f"连接不存在: {wxid}")
    data["connections"] = new_conns
    data["last_sync"] = time.time()
    if not _save_connections(data):
        raise HTTPException(status_code=500, detail="删除连接失败")
    return {"status": "deleted", "wxid": wxid}


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

    # ── DB 确认后，同步到 GirlfriendManager ──
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

        # ── DB 确认后，同步到 GirlfriendManager ──
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

    # 清理 GirlfriendManager 缓存
    gf = deps.gf
    if gf:
        await gf.remove_binding(wxid)

    return {"status": "unbound", "wxid": wxid}
