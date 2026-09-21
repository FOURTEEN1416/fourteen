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
    """更新角色完整角色卡 (CharaCardV2 格式)

    ⚠️ 双写：sqlite store 只是 CharacterManager 的运行态副本；
    `config/characters/` 才是全仓权威真源（v1.9 裁决）。旧实现只写 sqlite，
    下次从真源重载即静默回滚用户改动。
    """
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

    # 镜像回权威真源：保留文件原有扩展字段，只覆盖 CharaCardV2 携带的字段
    from api.routers.character_routes import (
        _invalidate_knowledge_index,
        _save_character,
    )
    from api.routers.character_routes import _load_character as _load_character_file

    file_data = _load_character_file(character_id) or {"id": character_id}
    merged = {
        **file_data,
        "name": card.data.name,
        "description": card.data.description,
        "scenario": card.data.scenario,
        "first_mes": card.data.first_mes,
        "mes_example": card.data.mes_example,
        "creator_notes": card.data.creator_notes,
        "tags": card.data.tags,
    }
    if isinstance(card.data.personality, (str, dict)):
        merged["personality"] = card.data.personality
    if not _save_character(character_id, merged):
        raise HTTPException(status_code=500, detail="角色卡已写入运行态，但真源文件写入失败")

    if deps.orch and hasattr(deps.orch, "invalidate_character_persona_cache"):
        deps.orch.invalidate_character_persona_cache(character_id)
    _invalidate_knowledge_index(character_id)
    logger.info("角色卡已更新（sqlite + config/characters 双写）: %s", character_id)
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
