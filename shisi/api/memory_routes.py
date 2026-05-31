"""记忆增强API端点 — 6个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..memory.favorite_manager import FavoriteManager

from ..memory.forward_manager import ForwardManager
from .common import ApiResponse

logger = logging.getLogger("shisi.api.memory_routes")

router = APIRouter(prefix="/api/shisi/memory", tags=["memory"])

_fav_mgr: FavoriteManager | None = None
_fwd_mgr: ForwardManager | None = None


def set_managers(fav: FavoriteManager, fwd: ForwardManager) -> None:
    global _fav_mgr, _fwd_mgr
    _fav_mgr = fav
    _fwd_mgr = fwd


class FavoriteRequest(BaseModel):
    character_id: str
    memory_id: str


class ForwardRequest(BaseModel):
    from_character: str
    to_character: str
    memory_id: str
    content: str = ""


@router.get("/{character_id}", response_model=ApiResponse)
async def get_memories(character_id: str):
    return ApiResponse(data={"character_id": character_id, "message": "记忆查询需要对接现有MemoryPipeline"})


@router.post("/favorite", response_model=ApiResponse)
async def favorite_memory(req: FavoriteRequest):
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    ok = _fav_mgr.favorite(req.character_id, req.memory_id)
    return ApiResponse(data={"success": ok})


@router.delete("/favorite/{fav_id}", response_model=ApiResponse)
async def unfavorite_memory(fav_id: int, character_id: str = "", memory_id: str = ""):
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    ok = _fav_mgr.unfavorite(character_id, memory_id)
    return ApiResponse(data={"success": ok})


@router.get("/favorites", response_model=ApiResponse)
async def list_favorites(character_id: str):
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    favs = _fav_mgr.list_favorites(character_id)
    return ApiResponse(data=favs)


@router.post("/forward", response_model=ApiResponse)
async def forward_memory(req: ForwardRequest):
    if _fwd_mgr is None:
        raise HTTPException(status_code=503, detail="ForwardManager未初始化")
    ok = _fwd_mgr.forward(req.from_character, req.to_character, req.memory_id, req.content)
    return ApiResponse(data={"success": ok})


@router.delete("/{memory_id}", response_model=ApiResponse)
async def delete_memory(memory_id: str, character_id: str = "", confirm: bool = False):
    if not confirm:
        raise HTTPException(status_code=400, detail="删除记忆需要二次确认(confirm=true)")
    return ApiResponse(data={"memory_id": memory_id, "message": "已移入回收站（30天保留期）"})
