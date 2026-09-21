"""Agent Plane 控制面 API：因果回放 / 画像投影 / curator / 探针。

P0-10：全部端点为**控制面/调试面**，须 admin 角色。旧实现仅 `verify_api_key_dep`
（任意有效用户 JWT 即放行），且 `session_key` 无归属校验 → 任一登录用户可读
他人账本回放/事件/画像投影，`curate` 空键 `apply=True` 更会全库改写。
改用 `require_role("admin")`，与 `admin_routes` 同规。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth_jwt import require_role
from api.database import User
from api.deps import deps

router = APIRouter(tags=["agent-plane"])


def _ledger():
    from shisi.agent_plane.event_ledger import default_ledger

    return default_ledger()


def _distinct_user_keys(limit: int = 200) -> list[str]:
    """读 `user_facts` 去重 user_key（**同步** SQLite）。

    ⚠️ 必须经 `asyncio.to_thread` 调用：`curate` 端点是 `async def`，
    旧实现在事件循环里直接 `sqlite3.connect` + 全表扫描 → 阻塞整个 loop
    （本端点扫的是全量 user_facts，不是小表）。
    """
    import sqlite3
    from contextlib import closing

    from utils.project_paths import project_path

    # P1-52：锚定项目根（旧 Path("data/sqlite.db") 按 CWD 解析，
    # 非仓库根启动时全库扫描静默返回空 keys）
    db = project_path("data", "sqlite.db")
    if not db.exists():
        return []
    with closing(sqlite3.connect(str(db))) as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_key FROM user_facts WHERE user_key != ''"
        ).fetchall()
    return [str(r[0]) for r in rows[:limit]]


@router.get("/api/agent-plane/replay")
async def agent_plane_replay(
    session_key: str = Query(..., min_length=1),
    turn_id: str = Query(""),
    reply_id: str = Query(""),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """因果回放：按 session+turn/reply 查账本切片（回答「角色为何这样答」）。"""
    if not turn_id and not reply_id:
        raise HTTPException(400, "turn_id_or_reply_id_required")
    try:
        bundle = _ledger().replay(session_key=session_key, turn_id=turn_id, reply_id=reply_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"replay_failed:{e}") from e
    from shisi.agent_plane.event_ledger import event_to_dict

    return {
        "summary": bundle.summary(),
        "slots": bundle.slots,
        "profile_ops": bundle.profile_ops,
        "memory_ops": bundle.memory_ops,
        "tool_ops": bundle.tool_ops,
        "affinity_ops": bundle.affinity_ops,
        "errors": bundle.errors,
        "events": [event_to_dict(e) for e in bundle.events[:100]],
    }


@router.get("/api/agent-plane/profile")
async def agent_plane_profile(
    session_key: str = Query(..., min_length=1),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    from shisi.agent_plane.runtime import get_profile_prompt_block, project_profile_for

    proj = project_profile_for(session_key)
    return {
        "session_key": session_key,
        "projection": proj,
        "prompt_block": get_profile_prompt_block(session_key),
        "counts": _ledger().count_by_session(session_key),
    }


@router.get("/api/agent-plane/events")
async def agent_plane_events(
    session_key: str = Query(..., min_length=1),
    event_type: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    events = _ledger().query(
        session_key=session_key,
        event_type=event_type or None,
        limit=limit,
    )
    from shisi.agent_plane.event_ledger import event_to_dict

    return {"session_key": session_key, "events": [event_to_dict(e) for e in events]}


@router.post("/api/agent-plane/curate")
async def agent_plane_curate(
    session_key: str = "",
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """记忆 curator：规则整理垃圾/near-dup；可选全会话。"""
    from shisi.agent_plane.curator import run_curator_all_known, run_curator_for_session

    orch = deps.orch
    sm = None
    llm = None
    if orch is not None:
        comps = getattr(orch, "components", None) or {}
        mem = comps.get("memory")
        sm = getattr(mem, "structured_memory", None) or getattr(mem, "_sm", None)
        llm = comps.get("llm")
    if sm is None:
        raise HTTPException(503, "memory_unavailable")
    if session_key:
        return run_curator_for_session(session_key, sm=sm, llm=llm, apply=True)
    keys: list[str] = []
    try:
        keys = await asyncio.to_thread(_distinct_user_keys)
    except Exception:  # noqa: BLE001
        keys = []
    return run_curator_all_known(sm, llm=llm, session_keys=keys)


@router.get("/api/agent-plane/probes")
async def agent_plane_probes(
    session_key: str = Query("", min_length=0),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """验收探针骨架：空 session_key 时跑逻辑自检；有 key 时读真实账本。"""
    from scripts.ax_acceptance_probes import (
        probe_causal_replay,
        probe_profile_correct,
        probe_tool_execution,
    )

    led = _ledger()
    if not session_key:
        return {
            "mode": "hint",
            "message": "传 session_key 查该用户；或在服务器跑 scripts/ax_acceptance_probes.py --self-test",
        }
    counts = led.count_by_session(session_key)
    recent = led.query(session_key=session_key, limit=50)
    turn = ""
    for e in reversed(recent):
        if e.turn_id:
            turn = e.turn_id
            break
    if not turn:
        return {"mode": "live", "session_key": session_key, "counts": counts, "note": "no_turn_id_yet"}
    return {
        "mode": "live",
        "session_key": session_key,
        "counts": counts,
        "causal_replay": probe_causal_replay(led, session_key, turn),
        "profile_correction": probe_profile_correct(led, session_key, turn),
        "tool_execution": probe_tool_execution(led, session_key, turn),
        "isolation_note": "串台探针需双 session；见 ax_acceptance_probes",
    }
