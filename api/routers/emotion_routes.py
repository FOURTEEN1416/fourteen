"""情绪参数编辑 API — 可编辑的情绪基线/波动/恢复力"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.deps import deps

logger = logging.getLogger("api.emotion_routes")

router = APIRouter(prefix="/api/emotion", tags=["emotion"])


class EmotionConfig(BaseModel):
    baseline_mood: str = Field(default="平和", description="情绪基线")
    volatility: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪波动性")
    resilience: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪恢复力")


def _get_emotion_engine() -> Any | None:
    orch = deps.orch
    if orch:
        if hasattr(orch, 'components') and 'emotion' in orch.components:
            return orch.components['emotion']
        return getattr(orch, '_emotion', None)
    return None


@router.get("/params")
def get_emotion_params(_auth: bool = Security(verify_api_key_dep)):
    engine = _get_emotion_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="情感引擎未初始化")
    config = getattr(engine, '_config', {})
    default_config = getattr(engine, '_default_config', {})
    return {
        "baseline_mood": config.get("baseline_mood", "平和"),
        "volatility": config.get("volatility", 0.5),
        "resilience": config.get("resilience", 0.5),
        "config": {
            "energy_drain_per_message": config.get("energy_drain_per_message", default_config.get("energy_drain_per_message", 0.02)),
            "energy_recovery_per_hour": config.get("energy_recovery_per_hour", default_config.get("energy_recovery_per_hour", 0.05)),
            "per_positive_reply": config.get("per_positive_reply", default_config.get("per_positive_reply", 1.0)),
            "per_negative_reply": config.get("per_negative_reply", default_config.get("per_negative_reply", -0.5)),
            "per_day_decay": config.get("per_day_decay", default_config.get("per_day_decay", 0.1)),
        },
    }


@router.put("/params")
def update_emotion_params(req: EmotionConfig, _auth: bool = Security(verify_api_key_dep)):
    engine = _get_emotion_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="情感引擎未初始化")
    if not hasattr(engine, '_config'):
        raise HTTPException(status_code=500, detail="情感引擎不支持配置更新")
    engine._config["baseline_mood"] = req.baseline_mood
    engine._config["volatility"] = req.volatility
    engine._config["resilience"] = req.resilience
    logger.info("情感参数已更新: baseline=%s, volatility=%.2f, resilience=%.2f",
                req.baseline_mood, req.volatility, req.resilience)
    return {"status": "updated"}
