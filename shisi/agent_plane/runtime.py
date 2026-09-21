"""agent_plane 生产运行时 — 账本访问、画像写投影、prompt 画像槽。

用户裁决 2026-09-21：
- 写全走 EventLedger；user_profile 表仅投影/缓存
- 主动消息由 LLM 判断，系统不做策略闸
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent_plane")


def get_ledger():
    from shisi.agent_plane.event_ledger import default_ledger

    return default_ledger()


def _store():
    from shisi.memory.legacy.user_profile import default_store

    return default_store()


def ensure_profile_seeded(session_key: str) -> dict[str, Any]:
    """ledger 无画像事件时，把存量 user_profile 行 seed 为基线（一次）。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_PROFILE_CORRECT,
        EVENT_PROFILE_UPDATE,
    )
    from shisi.agent_plane.profile_projection import (
        project_profile,
        seed_profile_baseline,
    )

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ledger = get_ledger()
    rows = ledger.query(session_key=sk, limit=500)
    if any(e.event_type in (EVENT_PROFILE_UPDATE, EVENT_PROFILE_CORRECT) for e in rows):
        return project_profile(ledger, sk)
    try:
        row = _store().get(sk) or {}
    except Exception:  # noqa: BLE001
        row = {}
    if row:
        return seed_profile_baseline(ledger, sk, row)
    return project_profile(ledger, sk)


def materialize_profile_cache(session_key: str, projection: dict[str, Any]) -> None:
    """把投影物化进 user_profile 表——**只读缓存**，禁止当作写权威。"""
    sk = str(session_key or "").strip()
    if not sk or not projection:
        return
    try:
        store = _store()
        fields: dict[str, Any] = {}
        for k in ("nickname", "birthday", "occupation", "location", "notes"):
            if projection.get(k):
                fields[k] = str(projection[k])
        if projection.get("preferences"):
            fields["preferences"] = list(projection["preferences"])
        if projection.get("commitments"):
            fields["commitments"] = list(projection["commitments"])
        # 清空语义：投影为空时显式 clear（correct 已发生）
        if "birthday" in projection and not projection.get("birthday"):
            fields["clear_birthday"] = True
        if fields:
            store.upsert(sk, **fields)
    except Exception as e:  # noqa: BLE001
        logger.debug("profile cache materialize failed: %s", e)


def write_profile_from_tool(
    session_key: str,
    payload: dict[str, Any],
    *,
    correct: bool = False,
    turn_id: str = "",
    actor: str = "agent",
    reason: str = "",
) -> dict[str, Any]:
    """工具写权威入口：seed → ledger append → 投影 → 物化缓存。"""
    from shisi.agent_plane.profile_projection import write_profile_event

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ensure_profile_seeded(sk)
    body = dict(payload or {})
    if reason:
        body["reason"] = reason
    if body.get("clear_birthday") or body.get("commitments_clear"):
        correct = True
    proj = write_profile_event(
        get_ledger(),
        session_key=sk,
        payload=body,
        correct=correct,
        turn_id=turn_id,
        actor=actor,
    )
    materialize_profile_cache(sk, proj)
    return proj


def project_profile_for(session_key: str) -> dict[str, Any]:
    from shisi.agent_plane.profile_projection import project_profile

    sk = str(session_key or "").strip()
    if not sk:
        return {}
    ensure_profile_seeded(sk)
    return project_profile(get_ledger(), sk)


def format_profile_prompt_block(projection: dict[str, Any]) -> str:
    """渲染「# 用户画像」段（与 UserProfileStore.to_prompt_block 同文案契约）。"""
    p = projection or {}
    if not any(p.get(k) for k in ("nickname", "birthday", "occupation", "location", "notes")) and not (
        p.get("preferences") or p.get("commitments")
    ):
        return ""
    lines: list[str] = ["# 用户画像（稳定事实，以本段为准；段外信息禁止编造）"]
    if p.get("nickname"):
        lines.append(f"- 称呼：{p['nickname']}")
    if p.get("birthday"):
        lines.append(f"- 生日：{p['birthday']}")
    if p.get("occupation"):
        lines.append(f"- 身份/近况：{p['occupation']}")
    if p.get("location"):
        lines.append(f"- 常驻地：{p['location']}")
    prefs = p.get("preferences") or []
    if prefs:
        lines.append("- 偏好：" + "；".join(str(x) for x in prefs[:6]))
    commits = p.get("commitments") or []
    if commits:
        lines.append("- 你们的约定：")
        for c in commits[-5:]:
            lines.append(f"  · {c}")
    if p.get("notes"):
        lines.append(f"- 备注：{p['notes']}")
    if len(lines) == 1:
        return ""
    lines.append(
        "- 硬约束：画像未写的信息**不得编造**；用户更正时以用户刚说的为准，并当作唯一真相。"
    )
    return "\n".join(lines)


def get_profile_prompt_block(session_key: str) -> str:
    """prompt 画像槽唯一读入口（投影优先）。"""
    try:
        return format_profile_prompt_block(project_profile_for(session_key))
    except Exception as e:  # noqa: BLE001
        logger.debug("profile prompt block failed: %s", e)
        return ""


def append_chat_events(
    *,
    session_key: str,
    character_id: str = "",
    turn_id: str = "",
    reply_id: str = "",
    user_msg: str = "",
    reply: str = "",
    slots: dict[str, Any] | None = None,
) -> None:
    """orchestrator 每轮回调：因果账本（失败不阻塞聊天）。"""
    from shisi.agent_plane.event_ledger import (
        EVENT_ASSISTANT_REPLY,
        EVENT_PROMPT_SLOTS,
        EVENT_USER_MESSAGE,
    )

    sk = str(session_key or "").strip()
    if not sk:
        return
    ledger = get_ledger()
    try:
        if user_msg:
            ledger.append(
                session_key=sk,
                event_type=EVENT_USER_MESSAGE,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="user",
                payload={"text": str(user_msg)[:500]},
            )
        if slots is not None:
            ledger.append(
                session_key=sk,
                event_type=EVENT_PROMPT_SLOTS,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="system",
                payload={"slots": dict(slots)},
            )
        if reply:
            ledger.append(
                session_key=sk,
                event_type=EVENT_ASSISTANT_REPLY,
                character_id=character_id,
                turn_id=turn_id,
                reply_id=reply_id,
                actor="assistant",
                payload={"text": str(reply)[:500]},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger chat events failed: %s", e)


def append_proactive_event(
    *,
    session_key: str,
    sent: bool,
    message: str = "",
    reason: str = "",
    wait_minutes: int | None = None,
    character_id: str = "",
) -> None:
    from shisi.agent_plane.event_ledger import EVENT_PROACTIVE_SEND, EVENT_PROACTIVE_SKIP

    sk = str(session_key or "").strip()
    if not sk:
        return
    try:
        get_ledger().append(
            session_key=sk,
            event_type=EVENT_PROACTIVE_SEND if sent else EVENT_PROACTIVE_SKIP,
            character_id=character_id,
            actor="proactive_llm",
            payload={
                "message": str(message or "")[:200],
                "reason": str(reason or "")[:300],
                "wait_minutes": wait_minutes,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger proactive event failed: %s", e)


def append_memory_write_event(
    *,
    session_key: str,
    facts: list[dict[str, Any]],
    action: str = "write",
) -> None:
    from shisi.agent_plane.event_ledger import (
        EVENT_MEMORY_REINFORCE,
        EVENT_MEMORY_WRITE,
    )

    sk = str(session_key or "").strip()
    if not sk or not facts:
        return
    try:
        get_ledger().append(
            session_key=sk,
            event_type=EVENT_MEMORY_REINFORCE if action == "reinforce" else EVENT_MEMORY_WRITE,
            actor="agent",
            payload={"facts": list(facts)[:20], "action": action},
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("ledger memory event failed: %s", e)
