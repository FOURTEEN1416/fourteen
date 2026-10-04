"""情感阶段API端点 — 3个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.auth_jwt import User, require_role

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
async def get_emotion_stage(
    character_id: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：按 character 读情感阶段进度——引擎键空间是裸角色键
    （无 user 维度，主体过滤不可行），返回的是会话派生的关系状态，属
    他人数据面 → 收 admin。``/stages``（静态目录，前端消费）与
    ``/evaluate``（纯计算，不读存量键）保持登录可用。"""
    if _engine is None:
        raise HTTPException(status_code=503, detail="EmotionStageEngine未初始化")
    progress = _engine.get_progress(character_id)
    return ApiResponse(data=progress)


@router.post("/{character_id}/evaluate", response_model=ApiResponse)
async def evaluate_stage(character_id: str, affinity: float):
    if _engine is None:
        raise HTTPException(status_code=503, detail="EmotionStageEngine未初始化")
    # 纯查询：本端点无用户维度，走 evaluate 会以裸角色键 UPSERT 进
    # emotion_stage_state、污染 track 键空间的跨重启回放（2026-09-22 收口）。
    stage = _engine.resolve_stage(affinity)
    return ApiResponse(data={"stage": stage.name, "features": stage.features})
