"""
人格画像路由 — emotion / persona / psych

来源：原 api.main_routes.py L231/245/254/266/286/294/303/315/333 共 9 端点

依赖：
- deps.orch（emotion / persona / persona_extractor）
- EmotionStateResponse 模型来自 api.main_routes
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps
from api.main_routes import EmotionStateResponse

logger = logging.getLogger("api.routers.personality_routes")

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
    """情绪趋势（会话内存态）：按时间窗返回历史记录（旧→新）。

    2026-09-01 修复：旧实现读不存在的 `_emotion_history` 属性，端点自创建起
    恒返回空数组；现统一走 EmotionEngine.get_history()。历史为环形缓冲
    （500 条上限，重启清零），days 仅作时间窗近似截断。
    """
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"trend": [], "days": days}
    history = orch._emotion.get_history()
    return {"trend": history[-days * 20:], "days": days}


@router.get("/api/emotion/distribution")
async def emotion_distribution(
    days: int = Query(default=7, ge=1, le=30),
    _auth: bool = Security(verify_api_key_dep),
):
    """情绪分布（会话内存态）：聚合历史中各主情绪的占比。

    SP-1 补齐：与 trend 同源（EmotionEngine 环形缓冲），无持久化——
    重启后从零累计，前端需诚实标注"会话内"。
    """
    orch = deps.orch
    if not orch or not orch._emotion:
        return {"distribution": [], "total": 0, "days": days}
    history = orch._emotion.get_history()
    cutoff = time.time() - days * 86400
    counts: dict[str, int] = {}
    for item in history:
        ts = item.get("timestamp", 0) or 0
        if ts and ts < cutoff:
            continue
        emotion = item.get("primary_emotion", "平常")
        counts[emotion] = counts.get(emotion, 0) + 1
    distribution = [
        {"emotion": emotion, "count": count}
        for emotion, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {"distribution": distribution, "total": sum(counts.values()), "days": days}


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
async def psych_profile(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """画像摘要：按生成侧写入的 scope 取数（`{character_id}:{session_id}`）。

    旧实现固定读 `pe.user_id`（默认 `default`，请求链早已不再切换），与生成侧
    键空间错位，页面恒报"画像尚未生成"。
    """
    pe = _get_pe()
    if pe is None:
        return {
            "user_id": "default",
            "scope": "default",
            "scope_count": 0,
            "status": "unavailable",
            "snapshots": 0,
        }
    scope, scope_count = pe.resolve_profile_scope(character_id, session_id)
    return pe.profile_summary(scope, scope_count)


@router.get("/api/psych/snapshots")
async def psych_snapshots(
    limit: int = Query(default=20, ge=1, le=200),
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    pe = _get_pe()
    if pe is None:
        return {"snapshots": []}
    scope, _count = pe.resolve_profile_scope(character_id, session_id)
    snaps = pe.bank.get_recent_snapshots(user_id=scope, limit=limit)
    return {"scope": scope, "snapshots": [s.to_dict() for s in snaps]}


@router.delete("/api/psych/profile")
async def reset_psych_profile(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """清除画像：无过滤条件时清除**全部**已学 scope（与页面文案一致）。

    旧实现只清 `pe.user_id` 那一个（通常是空的 `default` 桶），却对用户承诺
    "清除所有已学习的心理特征数据"；失败时还返回 200 + `{"status":"failed"}`。
    """
    pe = _get_pe()
    if pe is None:
        raise HTTPException(503, "PersonaExtractor未初始化")
    res = pe.clear_profiles(character_id, session_id)
    if res["failed"] > 0:
        raise HTTPException(
            500,
            f"心理画像未全部清除：{res['cleared']}/{len(res['targets'])}，请重试",
        )
    return {"status": "reset", "cleared": res["cleared"], "scopes": res["targets"]}


@router.get("/api/psych/mental-health")
async def psych_mental_health(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    pe = _get_pe()
    if pe is None:
        return {"available": False}
    scope, _count = pe.resolve_profile_scope(character_id, session_id)
    persona = pe.bank.get_persona(scope)
    if persona is None:
        return {"available": True, "scope": scope, "data": None}
    return {
        "available": True,
        "scope": scope,
        "mental_health": persona.mental_health,
        "cognitive": persona.cognitive,
        "liwc": persona.liwc,
        "dark_triad": persona.dark_triad,
        "hexaco": persona.hexaco,
    }


@router.get("/api/psych/liwc")
async def psych_liwc(
    character_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    pe = _get_pe()
    if pe is None or not pe.liwc:
        return {"available": False}
    scope, _count = pe.resolve_profile_scope(character_id, session_id)
    persona = pe.bank.get_persona(scope)
    if persona is None or not persona.liwc:
        return {"available": True, "scope": scope, "data": None}
    return {"available": True, "scope": scope, "data": persona.liwc}
