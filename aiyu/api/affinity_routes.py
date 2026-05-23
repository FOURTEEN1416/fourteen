"""好感度API端点 — 4个端点。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..affinity.enhancer import AffinityEnhancer
from .common import ApiResponse

logger = logging.getLogger("aiyu.api.affinity_routes")

router = APIRouter(prefix="/api/aiyu/affinity", tags=["affinity"])

_enhancer: Optional[AffinityEnhancer] = None


def set_enhancer(e: AffinityEnhancer) -> None:
    global _enhancer
    _enhancer = e


class UpdateRequest(BaseModel):
    delta: float
    reason: str = ""
    source: str = "api"


@router.get("/{character_id}", response_model=ApiResponse)
async def get_affinity(character_id: str):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    return ApiResponse(data=_enhancer.get_progress(character_id))


@router.post("/{character_id}/update", response_model=ApiResponse)
async def update_affinity(character_id: str, req: UpdateRequest):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    new_val, unlocks = _enhancer.update(character_id, req.delta, req.reason, req.source)
    return ApiResponse(data={"affinity": new_val, "unlocks": [u.__dict__ for u in unlocks]})


@router.post("/{character_id}/decay", response_model=ApiResponse)
async def apply_decay(character_id: str):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    decay = _enhancer.apply_decay(character_id)
    return ApiResponse(data={"decay": decay, "affinity": _enhancer.get_value(character_id)})


@router.get("/{character_id}/unlocks", response_model=ApiResponse)
async def get_unlocks(character_id: str):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    value = _enhancer.get_value(character_id)
    unlocks = _enhancer.unlock_manager.get_unlocks_at(value)
    return ApiResponse(data={"affinity": value, "unlocks": [u.__dict__ for u in unlocks]})
