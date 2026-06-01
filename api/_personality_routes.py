"""
人格画像路由 — emotion / persona / psych

来源：原 api.main_routes.py L231/245/254/266/286/294/303/315/333 共 9 端点

依赖：
- deps.orch（emotion / persona / persona_extractor）
- EmotionStateResponse 模型来自 api.main_routes
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import EmotionStateResponse

logger = logging.getLogger("api._personality_routes")

router = APIRouter(tags=["personality"])


# ═══════════════════════════════════════════════════════
# Emotion API
# ═══════════════════════════════════════════════════════


@router.get("/api/emotion/state", response_model=EmotionStateResponse)
async def emotion_state(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._emotion:
        health_data = orch._emotion.health_check()
        return EmotionStateResponse(
            current_emotion=health_data.get("current_emotion", ""),
            intensity=health_data.get("intensity", 0.0),
            energy=health_data.get("energy", 0.0),
            affinity=health_data.get("affinity", 0.0),
        )
    return EmotionStateResponse()


@router.get("/api/emotion/trend")
async def emotion_trend(
    days: int = Query(default=7, ge=1, le=30),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"trend": [], "days": days}
    trend = getattr(orch._emotion, "_emotion_history", [])
    return {"trend": trend[-days * 20:], "days": days}


# ═══════════════════════════════════════════════════════
# Persona API
# ═══════════════════════════════════════════════════════


@router.get("/api/persona/profile")
async def persona_profile(_auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    if orch and orch._persona:
        return {
            "core_character": orch._persona.profile.core_character,
            "speaking_style": orch._persona.profile.speaking_style,
            "emotional_preference": orch._persona.profile.emotional_preference,
        }
    return {}


@router.get("/api/persona/evolution-log")
async def persona_evolution_log(
    limit: int = Query(default=50, ge=1, le=500),
    _auth: bool = Security(verify_api_key_dep),
):
    orch = deps.orch
    if orch and orch._persona:
        return {"log": orch._persona.get_evolution_log(limit)}
    return {"log": []}


# ═══════════════════════════════════════════════════════
# 用户心理画像 API
# ═══════════════════════════════════════════════════════


def _get_pe():
    """从 orch.components 或 _persona_extractor 兜底取出 PersonaExtractor"""
    orch = deps.orch
    if orch and hasattr(orch, "components"):
        return orch.components.get("persona_extractor")
    return None


@router.get("/api/psych/profile")
async def psych_profile(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None:
        return {"user_id": "default", "status": "unavailable", "snapshots": 0}
    return pe.get_user_profile_summary()


@router.get("/api/psych/snapshots")
async def psych_snapshots(
    limit: int = Query(default=20, ge=1, le=200),
    _auth: bool = Security(verify_api_key_dep),
):
    pe = _get_pe()
    if pe is None:
        return {"snapshots": []}
    snaps = pe.bank.get_recent_snapshots(user_id=pe.user_id, limit=limit)
    return {"snapshots": [s.to_dict() for s in snaps]}


@router.delete("/api/psych/profile")
async def reset_psych_profile(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    pe = _get_pe()
    if pe is None:
        raise HTTPException(503, "PersonaExtractor未初始化")
    ok = pe.bank.clear_user(pe.user_id)
    return {"status": "reset" if ok else "failed"}


@router.get("/api/psych/mental-health")
async def psych_mental_health(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None:
        return {"available": False}
    persona = pe.bank.get_persona(pe.user_id)
    if persona is None:
        return {"available": True, "data": None}
    return {
        "available": True,
        "mental_health": persona.mental_health,
        "cognitive": persona.cognitive,
        "liwc": persona.liwc,
        "dark_triad": persona.dark_triad,
        "hexaco": persona.hexaco,
    }


@router.get("/api/psych/liwc")
async def psych_liwc(_auth: bool = Security(verify_api_key_dep)):
    pe = _get_pe()
    if pe is None or not pe.liwc:
        return {"available": False}
    persona = pe.bank.get_persona(pe.user_id)
    if persona is None or not persona.liwc:
        return {"available": True, "data": None}
    return {"available": True, "data": persona.liwc}
