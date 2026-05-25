"""人设编辑器API端点 — 3个端点。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..character.character_card_v2 import to_persona_config
from ..character.manager import CharacterManager
from ..character.models import CharaCardV2
from .common import ApiResponse

logger = logging.getLogger("shisi.api.persona_routes")

router = APIRouter(prefix="/api/shisi/persona", tags=["persona"])

_manager: CharacterManager | None = None


def set_manager(mgr: CharacterManager) -> None:
    global _manager
    _manager = mgr


class PersonaUpdateRequest(BaseModel):
    card: dict[str, Any]


@router.get("/characters/{character_id}", response_model=ApiResponse)
async def get_persona(character_id: str):
    if _manager is None:
        raise HTTPException(status_code=503, detail="CharacterManager未初始化")
    card = _manager.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return ApiResponse(data=card.model_dump(mode="json"))


@router.put("/characters/{character_id}", response_model=ApiResponse)
async def update_persona(character_id: str, req: PersonaUpdateRequest):
    if _manager is None:
        raise HTTPException(status_code=503, detail="CharacterManager未初始化")
    try:
        card = CharaCardV2.model_validate(req.card)
    except Exception:
        logger.exception("角色卡数据校验失败: %s", character_id)
        raise HTTPException(status_code=400, detail="角色卡数据无效") from None
    ok = _manager.update_character(character_id, card)
    if not ok:
        raise HTTPException(status_code=404, detail="角色不存在")
    return ApiResponse(data={"character_id": character_id, "message": "人设已更新，对话中立即生效"})


@router.get("/characters/{character_id}/preview", response_model=ApiResponse)
async def preview_persona(character_id: str):
    if _manager is None:
        raise HTTPException(status_code=503, detail="CharacterManager未初始化")
    card = _manager.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return ApiResponse(data=to_persona_config(card))
