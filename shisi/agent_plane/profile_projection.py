"""画像投影 — 写走 EventLedger，profile 表仅作投影（用户裁决 2026-09-21）。

写权威：ledger 事件（profile_update / profile_correct）
读投影：按 session_key 重放事件得到画像字典
兼容：P1 接线时可将投影物化写入 user_profile 以供旧 prompt 路径廉价读取，
但**禁止**工具/正则再直接当写权威。
"""

from __future__ import annotations

from typing import Any

from shisi.agent_plane.event_ledger import (
    EVENT_PROFILE_CORRECT,
    EVENT_PROFILE_UPDATE,
    EventLedger,
)

PROFILE_SCALAR_FIELDS = (
    "nickname",
    "birthday",
    "occupation",
    "location",
    "notes",
)
PROFILE_LIST_FIELDS = ("preferences", "commitments")


def _empty_profile() -> dict[str, Any]:
    p: dict[str, Any] = {k: "" for k in PROFILE_SCALAR_FIELDS}
    p["preferences"] = []
    p["commitments"] = []
    return p


def apply_profile_ops_to_state(state: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    """把单条 profile 事件应用到投影状态（纯函数，便于单测与突变验红）。"""
    out = dict(state)
    watermarks = dict(out.get("_source_watermarks") or {})
    source_id = int(op.get("_source_chat_id") or 0)
    op = dict(op)
    for field in (*PROFILE_SCALAR_FIELDS, *PROFILE_LIST_FIELDS):
        operations = (field, f"{field}_add", f"{field}_remove", f"{field}_clear", f"clear_{field}")
        if not any(key in op for key in operations):
            continue
        if source_id and source_id < watermarks.get(field, 0):
            for key in operations:
                op.pop(key, None)
        elif source_id:
            watermarks[field] = source_id
    out["_source_watermarks"] = watermarks
    for k in PROFILE_LIST_FIELDS:
        out[k] = list(out.get(k) or [])

    # 标量：非空覆盖；clear_* / correct 空串可清空
    for field in PROFILE_SCALAR_FIELDS:
        if field in op and op.get(field) is not None:
            val = str(op.get(field) or "")
            if op.get("_event") == EVENT_PROFILE_UPDATE and val == "":
                continue
            out[field] = val

    if op.get("clear_birthday"):
        out["birthday"] = ""
    if op.get("commitments_clear"):
        out["commitments"] = []

    for key in PROFILE_LIST_FIELDS:
        add_key = f"{key}_add"
        rm_key = f"{key}_remove"
        adds = list(op.get(add_key) or op.get(key) or [])
        rms = list(op.get(rm_key) or [])
        if (
            isinstance(op.get(key), list)
            and op.get("_event") == EVENT_PROFILE_CORRECT
            and op.get("replace_lists")
        ):
            # correct 时若给全量列表则替换（更正/清空后重写）
            out[key] = [str(x) for x in (op.get(key) or []) if str(x).strip()]
        if rms:
            out[key] = [x for x in out[key] if x not in rms]
        for item in adds:
            s = str(item or "").strip()
            if s and s not in out[key]:
                out[key].append(s)
        # 容量：preferences cap 20，commitments cap 8
        cap = 20 if key == "preferences" else 8
        out[key] = out[key][-cap:]
    return out


def project_profile(ledger: EventLedger, session_key: str, limit: int = 500) -> dict[str, Any]:
    """按真实写入序重放完整画像事件；limit 是分页大小，不是丢弃旧事件的窗口。"""
    sk = str(session_key or "").strip()
    state = _empty_profile()
    state["user_key"] = sk
    if not sk:
        return state
    page_size = max(1, min(int(limit), 2000))
    after_id = 0
    while True:
        events = ledger.query(
            session_key=sk, event_types=(EVENT_PROFILE_UPDATE, EVENT_PROFILE_CORRECT),
            limit=page_size, order="ASC", after_id=after_id,
        )
        for ev in events:
            op = dict(ev.payload or {})
            op["_event"] = ev.event_type
            state = apply_profile_ops_to_state(state, op)
        if len(events) < page_size:
            break
        after_id = events[-1].id
    return state


def write_profile_event(
    ledger: EventLedger,
    *,
    session_key: str,
    payload: dict[str, Any],
    correct: bool = False,
    turn_id: str = "",
    actor: str = "agent",
) -> dict[str, Any]:
    """写权威入口：只 append ledger，随后返回投影。禁止第二写路径。"""
    et = EVENT_PROFILE_CORRECT if correct else EVENT_PROFILE_UPDATE
    ledger.append(
        session_key=session_key,
        event_type=et,
        turn_id=turn_id,
        actor=actor,
        payload=dict(payload or {}),
    )
    return project_profile(ledger, session_key)


def seed_profile_baseline(
    ledger: EventLedger,
    session_key: str,
    profile_row: dict[str, Any],
) -> dict[str, Any]:
    """把存量 user_profile 行记为基线投影事件（一次性迁移，correct=False）。"""
    payload: dict[str, Any] = {}
    for k in PROFILE_SCALAR_FIELDS:
        if profile_row.get(k):
            payload[k] = profile_row.get(k)
    for k in PROFILE_LIST_FIELDS:
        items = profile_row.get(k) or []
        if isinstance(items, list) and items:
            payload[f"{k}_add"] = items
    if not payload:
        return project_profile(ledger, session_key)
    return write_profile_event(
        ledger,
        session_key=session_key,
        payload=payload,
        correct=False,
        actor="seed",
    )
