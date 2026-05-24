"""生理指标API端点 + 微信查询适配。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..vital_signs.vital_engine import VitalSignsEngine
from .common import ApiResponse

logger = logging.getLogger("shisi.api.vital_signs_routes")

router = APIRouter(prefix="/api/shisi/vital-signs", tags=["vital-signs"])

_engine: Optional[VitalSignsEngine] = None


def set_engine(e: VitalSignsEngine) -> None:
    global _engine
    _engine = e


@router.get("/{character_id}", response_model=ApiResponse)
async def get_vital_signs(character_id: str):
    if _engine is None:
        raise HTTPException(status_code=503, detail="VitalSignsEngine未初始化")
    state = _engine.get_current(character_id)
    return ApiResponse(data={
        "character_id": character_id,
        "heart_rate": state.heart_rate,
        "temperature": state.temperature,
        "breath_rate": state.breath_rate,
        "last_emotion": state.last_emotion,
        "wechat_format": _engine.format_wechat_message(character_id),
    })
