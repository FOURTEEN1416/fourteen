"""生理指标API端点 + 微信查询适配。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..vital_signs.vital_engine import DEFAULT_READING_NOTE, VitalSignsEngine
from .common import ApiResponse

logger = logging.getLogger("shisi.api.vital_signs_routes")

router = APIRouter(prefix="/api/shisi/vital-signs", tags=["vital-signs"])

_engine: VitalSignsEngine | None = None


def set_engine(e: VitalSignsEngine) -> None:
    global _engine
    _engine = e


@router.get("/{character_id}", response_model=ApiResponse)
async def get_vital_signs(character_id: str):
    if _engine is None:
        raise HTTPException(status_code=503, detail="VitalSignsEngine未初始化")
    state = _engine.get_current(character_id)
    # 无演算结果时读数只是基准占位，必须与文案同一标注口径自证（LLM 透明边界；
    # 文案常量唯一真源 = vital_engine.DEFAULT_READING_NOTE，此处不再各写一套）
    return ApiResponse(data={
        "character_id": character_id,
        "heart_rate": state.heart_rate,
        "temperature": state.temperature,
        "breath_rate": state.breath_rate,
        "last_emotion": state.last_emotion,
        "is_default": state.is_default,
        "note": DEFAULT_READING_NOTE if state.is_default else "",
        "wechat_format": _engine.format_wechat_message(character_id),
    })
