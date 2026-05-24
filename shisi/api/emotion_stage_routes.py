"""情感阶段API端点 — 3个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..emotion_stage.stage_engine import EmotionStageEngine
from .common import ApiResponse

logger = logging.getLogger("shisi.api.emotion_stage_routes")

router = APIRouter(prefix="/api/shisi/emotion-stage", tags=["emotion-stage"])

_engine: EmotionStageEngine | None = None


def set_engine(e: EmotionStageEngine) -> None:
    global _engine
    _engine = e


@router.get("/stages", response_model=ApiResponse)
async def list_stages():
    if _engine is None:
        raise HTTPException(status_code=503, detail="EmotionStageEngine未初始化")
    return ApiResponse(data=[{"name": s.name, "min": s.affinity_min, "max": s.affinity_max, "features": s.features} for s in _engine.stages])


@router.get("/{character_id}", response_model=ApiResponse)
async def get_emotion_stage(character_id: str):
    if _engine is None:
        raise HTTPException(status_code=503, detail="EmotionStageEngine未初始化")
    progress = _engine.get_progress(character_id)
    return ApiResponse(data=progress)


@router.post("/{character_id}/evaluate", response_model=ApiResponse)
async def evaluate_stage(character_id: str, affinity: float):
    if _engine is None:
        raise HTTPException(status_code=503, detail="EmotionStageEngine未初始化")
    stage = _engine.evaluate(character_id, affinity)
    return ApiResponse(data={"stage": stage.name, "features": stage.features})
