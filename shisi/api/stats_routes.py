"""统计诊断API端点。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..stats.analytics import AnalyticsService
from .common import ApiResponse

logger = logging.getLogger("shisi.api.stats_routes")

router = APIRouter(prefix="/api/shisi/stats", tags=["stats"])

_service: Optional[AnalyticsService] = None


def set_service(s: AnalyticsService) -> None:
    global _service
    _service = s


@router.get("", response_model=ApiResponse)
async def get_stats():
    if _service is None:
        raise HTTPException(status_code=503, detail="AnalyticsService未初始化")
    return ApiResponse(data=_service.get_stats())
