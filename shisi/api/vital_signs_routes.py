"""生理指标API端点 + 微信查询适配。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.auth_jwt import User, require_role

from ..vital_signs.vital_engine import DEFAULT_READING_NOTE, VitalSignsEngine
from .common import ApiResponse

logger = logging.getLogger("shisi.api.vital_signs_routes")

router = APIRouter(prefix="/api/shisi/vital-signs", tags=["vital-signs"])

_engine: VitalSignsEngine | None = None


def set_engine(e: VitalSignsEngine) -> None:
    global _engine
    _engine = e


@router.get("/{character_id}", response_model=ApiResponse)
async def get_vital_signs(
    character_id: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：按 character 读生理指标演算——键空间是裸角色键
    （无 user 维度，主体过滤不可行），读数由会话情绪派生，属他人数据面
    → 收 admin。微信侧消费走进程内 ``format_wechat_message``，不经本端点。"""
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
