from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Security

from api.state import SafetyLogManager

logger = logging.getLogger("rest_api.safety")

router = APIRouter(prefix="/api", tags=["safety"])

_orch = None
_verify_api_key = None

_safety_log_mgr = SafetyLogManager(maxlen=2000)


def set_dependencies(orch, verify_api_key):
    global _orch, _verify_api_key
    _orch = orch
    _verify_api_key = verify_api_key


def _get_safety():
    if _orch:
        return _orch.components.get("safety") if hasattr(_orch, 'components') else getattr(_orch, '_safety', None)
    return None


@router.get("/safety/stats")
async def safety_stats(_auth: bool = Security(_verify_api_key)):
    sf = _get_safety()
    return _safety_log_mgr.get_stats(enabled=sf.enabled if sf else False)


@router.get("/safety/log")
async def safety_log(limit: int = Query(default=50, le=200), _auth: bool = Security(_verify_api_key)):
    return {"log": _safety_log_mgr.get_recent(limit)}


@router.post("/safety/config")
async def safety_config(enabled: bool = True, _auth: bool = Security(_verify_api_key)):
    sf = _get_safety()
    if sf:
        sf.enabled = enabled
        return {"status": "ok", "enabled": enabled}
    return {"status": "not_available"}


# ═══ 用户心理画像（OCEAN+PAD人格分析） ═══

@router.get("/psych/profile")
async def psych_profile(_auth: bool = Security(_verify_api_key)):
    pe = None
    if _orch:
        pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
    if pe is None:
        return {"user_id": "default", "status": "unavailable", "snapshots": 0}
    return pe.get_user_profile_summary()


@router.get("/psych/snapshots")
async def psych_snapshots(limit: int = Query(default=20, ge=1, le=200), _auth: bool = Security(_verify_api_key)):
    pe = None
    if _orch:
        pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
    if pe is None:
        return {"snapshots": []}
    snaps = pe.bank.get_recent_snapshots(user_id=pe.user_id, limit=limit)
    return {"snapshots": [s.to_dict() for s in snaps]}


@router.delete("/psych/profile")
async def reset_psych_profile(_auth: bool = Security(_verify_api_key)):
    pe = None
    if _orch:
        pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
    if pe is None:
        raise HTTPException(503, "PersonaExtractor未初始化")
    ok = pe.bank.clear_user(pe.user_id)
    return {"status": "reset" if ok else "failed"}


@router.get("/psych/mental-health")
async def psych_mental_health(_auth: bool = Security(_verify_api_key)):
    pe = None
    if _orch:
        pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
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


@router.get("/psych/liwc")
async def psych_liwc(_auth: bool = Security(_verify_api_key)):
    pe = None
    if _orch:
        pe = _orch.components.get("persona_extractor") if hasattr(_orch, 'components') else None
    if pe is None or not pe.liwc:
        return {"available": False}
    persona = pe.bank.get_persona(pe.user_id)
    if persona is None or not persona.liwc:
        return {"available": True, "data": None}
    return {"available": True, "data": persona.liwc}
