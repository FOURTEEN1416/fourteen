"""健康检查v2路由"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from .schemas import HealthResponse

router = APIRouter(tags=["v2-health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        version="2.0.0",
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
    )
