"""人设编辑器API端点 — 3个端点。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..character.character_card_v2 import to_persona_config
from ..character.manager import CharacterManager
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
    """已作废：此路径只 UPDATE SQLite 运行态副本，不写 `config/characters` 权威真源。

    旧实现在副本写入成功后返回「人设已更新，对话中立即生效」，而生成侧
    （`PersonaService._load_character_card`）读的是真源文件 —— 该承诺不成立，
    且下次从真源重载会静默回滚用户改动。410 在任何写动作之前抛出，
    不留「副本改了、真源没改」的半写态。
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "该端点已作废：只写运行态副本、不写权威真源，改动不会在对话中生效。"
            f"完整角色卡请用 PUT /api/characters/{character_id}/persona-card，"
            f"数值/风格人设请用 PUT /api/characters/{character_id}/persona"
        ),
    )


@router.get("/characters/{character_id}/preview", response_model=ApiResponse)
async def preview_persona(character_id: str):
    if _manager is None:
        raise HTTPException(status_code=503, detail="CharacterManager未初始化")
    card = _manager.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return ApiResponse(data=to_persona_config(card))
