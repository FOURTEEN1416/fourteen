"""「我 × 这个角色」设置 API（2026-11-07 痛点批 P1）。

用户裁决（原话）：「先选角色，对这个角色进行设置」「不要接收偏好，就是需要
单个单个角色的设置，每个角色的单独设置」「这些（LLM 决策 / 骰子闸 / 话题 /
记忆整理）本来就是和用户自己体验和个性化强相关的设置，就应该给用户自己设置」。

因此本路由提供的是 **用户对自己的角色设置**，不是管理员代设：

- 读：`GET  /api/characters/{id}/my-settings`
- 写：`PUT  /api/characters/{id}/my-settings`（body `{"config": {...}}`，局部补丁）
- 复位：`DELETE /api/characters/{id}/my-settings`（删快照，下次读取按当前出厂默认重物化）

隔离与鉴权：
- **归属判定用 `require_character_access`（读门）**：公共池卡任何登录用户都可
  拥有自己的设置（这正是「全部角色卡可见 + 用户自调」的语义），但**改卡本体**
  仍是 `require_character_write`（P4 批已收紧）——两者不可混用。
- `user_key` 一律**服务端从 token + 绑定派生**（`N:wxid`），绝不接受客户端
  传 user_key（否则等于允许改别人的设置）；未绑定微信 → 409，不猜键。
- 管理员没有任何「代他人读写」入口：本路由只认 principal.user_id 自己的键。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.database import get_db
from api.routers.character_routes import require_character_access

logger = logging.getLogger("api.character_settings")

router = APIRouter(prefix="/api/characters", tags=["character-settings"])


class CharacterSettingsPatch(BaseModel):
    """局部补丁：只提交要改的字段；全量快照由服务端维护。"""

    config: dict[str, Any] = Field(
        default_factory=dict,
        description="要修改的字段（未知键/越界 → 400）；空对象 = 无操作",
    )


async def _user_key_for(user_id: int, db) -> str:
    """把注册用户 id 还原成完整会话键 `N:wxid`；无绑定返回空串（不猜键）。"""
    from api.routers.training_routes import _user_session_key_for

    return await _user_session_key_for(user_id, db)


@router.get("/{character_id}/my-settings")
async def get_my_character_settings(
    character_id: str,
    request: Request,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    db=Depends(get_db),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """读取「我 × 这个角色」的完整设置（缺行时按出厂默认物化）。"""
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录后查看角色设置")
    user_key = await _user_key_for(principal.user_id, db)
    if not user_key:
        raise HTTPException(
            status_code=409,
            detail="尚未绑定微信，无法确定你的会话键（不猜键）；请先在微信扫码绑定",
        )
    from utils import character_settings

    try:
        config = character_settings.read(user_key, character_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "character_id": character_id,
        "scope": "user_character",
        "config": config,
        "groups": {g: list(names) for g, names in character_settings.FIELDS_BY_GROUP.items()},
    }


@router.put("/{character_id}/my-settings")
async def update_my_character_settings(
    character_id: str,
    req: CharacterSettingsPatch,
    request: Request,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    db=Depends(get_db),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """写入局部补丁；越界/类型不符/未知键 → 400（绝不回执假成功）。"""
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录后修改角色设置")
    user_key = await _user_key_for(principal.user_id, db)
    if not user_key:
        raise HTTPException(
            status_code=409,
            detail="尚未绑定微信，无法确定你的会话键（不猜键）；请先在微信扫码绑定",
        )
    if not req.config:
        raise HTTPException(status_code=400, detail="config 不能为空（没有要修改的字段）")

    from utils import character_settings

    try:
        config = character_settings.write(user_key, character_id, req.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "[character_settings] 保存 user=%s ch=%s keys=%s",
        user_key,
        character_id,
        sorted(req.config),
    )
    return {
        "status": "ok",
        "character_id": character_id,
        "scope": "user_character",
        "config": config,
    }


@router.delete("/{character_id}/my-settings")
async def reset_my_character_settings(
    character_id: str,
    request: Request,
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Security(get_optional_principal),
    db=Depends(get_db),
    _owned: dict[str, Any] = Depends(require_character_access),
):
    """删除「我 × 这个角色」的快照；下一次读取按当前出厂默认重新物化。"""
    if principal is None:
        raise HTTPException(status_code=401, detail="需要登录后重置角色设置")
    user_key = await _user_key_for(principal.user_id, db)
    if not user_key:
        raise HTTPException(status_code=409, detail="尚未绑定微信，无法确定你的会话键")

    from utils import character_settings

    try:
        deleted = character_settings.reset(user_key, character_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "ok",
        "character_id": character_id,
        "reset": True,
        "had_settings": bool(deleted),
    }
