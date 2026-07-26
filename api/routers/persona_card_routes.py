"""统一角色卡 API — 桥接到 shisi 的 CharacterManager，返回完整 CharaCardV2 格式"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel

from api.auth import verify_api_key_dep
from api.deps import deps

logger = logging.getLogger("api.persona_card_routes")

router = APIRouter(prefix="/api/characters", tags=["persona-card"])

class PersonaCardUpdateRequest(BaseModel):
    card: dict[str, Any]


def _chara_card_to_persona_data(card) -> dict[str, Any]:
    """将 CharaCardV2 转为 PersonaEditorPage 期望的格式"""
    data = card.data
    return {
        "name": data.name,
        "description": data.description,
        "personality": data.personality,
        "scenario": data.scenario,
        "first_mes": data.first_mes,
        "mes_example": data.mes_example,
        "creator_notes": data.creator_notes,
        "tags": data.tags,
        "spec": card.spec,
        "spec_version": card.spec_version,
    }


# ── 端点 ──


@router.get("/{character_id}/persona-card")
async def get_persona_card(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取角色完整角色卡 (CharaCardV2 格式)"""
    char_mgr = getattr(deps.shisi_reg, "character_manager", None)
    if char_mgr is None:
        raise HTTPException(status_code=503, detail="角色管理器未初始化")
    card = char_mgr.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return _chara_card_to_persona_data(card)


@router.put("/{character_id}/persona-card")
async def update_persona_card(
    character_id: str,
    req: PersonaCardUpdateRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """更新角色完整角色卡 (CharaCardV2 格式)"""
    char_mgr = getattr(deps.shisi_reg, "character_manager", None)
    if char_mgr is None:
        raise HTTPException(status_code=503, detail="角色管理器未初始化")
    try:
        from shisi.character.models import CharaCardV2
        card = CharaCardV2.model_validate(req.card)
    except Exception as e:
        raise HTTPException(status_code=400, detail="角色卡数据无效") from e
    ok = char_mgr.update_character(character_id, card)
    if not ok:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    logger.info("角色卡已更新: %s", character_id)
    return {"status": "updated", "character_id": character_id}


@router.get("/{character_id}/persona-card/preview")
async def preview_persona_card(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """预览角色卡 — 转为 PersonaEngine 配置"""
    char_mgr = getattr(deps.shisi_reg, "character_manager", None)
    if char_mgr is None:
        raise HTTPException(status_code=503, detail="角色管理器未初始化")
    card = char_mgr.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    from shisi.character.character_card_v2 import to_persona_config
    return {"preview": to_persona_config(card)}
