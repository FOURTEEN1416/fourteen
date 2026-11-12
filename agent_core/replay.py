"""agent_core 回放层 —— 把一轮 / 一段会话按记录顺序还原成事件流（B-1）。

**定位**：离线诊断工具。只读、确定性、不调用 LLM、不参与生产链（零生产调用方）。
输入 ``(session_key, turn_id)`` 或 ``(session_key, limit)``，输出按 ``agent_events.id``
升序的事件字典列表 + 该会话的线程行 + 承诺行。

**三条硬规则**（对应已批准实施契约）：

1. **读侧零写**：恒 ``events.connect(ensure_schema=False)``，**永不建表**；库文件
   不存在时连连接都不开（``sqlite3.connect`` 会顺手创建空库文件，那是写副作用）。
2. **缺表是空结果不是异常**：先探 ``sqlite_master``，三表不全 → ``ok=False``、
   ``reason='tables_missing'``、``events=[]``、不抛。
3. **隔离三谓词**：所有 SQL 恒带 ``owner_user_id=? AND user_key=? AND session_key=?``，
   绝不做只有 ``session_key`` 的查询；``user_key`` 与会话键推出的键不一致即
   ``scope_mismatch``（防跨用户回放）。

**与写侧的分工**：``scope`` 解析、连接参数、时间归一、JSON 序列化格式全部复用
:mod:`agent_core.events`，本模块不复制第二套口径（``from agent_core import events``
再读 ``events._DB_PATH``，**禁止** ``from agent_core.events import _DB_PATH``——
那样 monkeypatch 会打不中）。

**不 fail-open**：写路径吞异常是为了不打断用户对话；回放是离线工具，坏库必须
抛出来（``sqlite3.DatabaseError`` 等一律向上传播）。唯一被吞的是 ``payload_json``
解析失败 —— 那不算 store 故障，按事件级标记成 ``{"_parse_error": True}``
（样板 ``shisi/agent_plane/event_ledger.py:281-283``）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from typing import Any

from agent_core import events
from utils.session_key import owner_of

#: 回放依赖的三张表；缺任何一张即 ``tables_missing``。
_TABLES = ("agent_events", "agent_threads", "agent_commitments")

_TURN_LIMIT_DEFAULT = 500
_SESSION_LIMIT_DEFAULT = 200

#: 事件行的列投影 —— 显式列出，避免 ``SELECT *`` 把 ``id`` / ``dedupe_key`` /
#: ``payload_json`` 这些内部列泄进契约形状。
_EVENT_COLUMNS = (
    "event_id",
    "user_key",
    "session_key",
    "owner_user_id",
    "character_id",
    "turn_id",
    "thread_id",
    "kind",
    "role",
    "source",
    "payload_json",
    "created_at",
)

_GROUPED_COLUMNS = "SELECT " + ", ".join(_EVENT_COLUMNS) + " FROM agent_events WHERE "

_SCOPE_WHERE = "owner_user_id=? AND user_key=? AND session_key=?"


# ---------------------------------------------------------------------------
# 作用域 / 结果形状
# ---------------------------------------------------------------------------


def _resolve_scope(session_key: str, user_key: str) -> tuple[tuple[str, str, int] | None, str, str, int]:
    """解析回放作用域 → ``(scope|None, reason, user_key, owner_user_id)``。

    顺序按契约：空键 → ``session_key_required``；无 owner 段 →
    ``unattributable_session``；显式 ``user_key`` 与会话键推出的键不一致 →
    ``scope_mismatch``。**不做任何启发式归属猜测**。
    """
    sk = str(session_key or "").strip()
    explicit = str(user_key or "").strip()
    if not sk:
        return None, "session_key_required", explicit, 0
    uk = events._user_key_from_session(sk)
    owner_id = owner_of(sk)
    if owner_id is None:
        return None, "unattributable_session", uk, 0
    if not uk:
        return None, "unattributable_session", "", 0
    if explicit and explicit != uk:
        return None, "scope_mismatch", explicit, int(owner_id)
    return (uk, sk, int(owner_id)), "", uk, int(owner_id)


def _result(
    *,
    ok: bool,
    reason: str,
    user_key: str,
    owner_user_id: int,
    session_key: str,
    turn_id: str = "",
    event_rows: list[dict] | None = None,
    thread: dict | None = None,
    commitments: list[dict] | None = None,
    summary: dict | None = None,
) -> dict:
    """统一结果形状（失败与成功同键，调用方无需分支取键）。"""
    result: dict[str, Any] = {
        "ok": ok,
        "reason": reason,
        "user_key": user_key,
        "owner_user_id": owner_user_id,
        "session_key": session_key,
        "turn_id": turn_id,
        "events": list(event_rows or []),
        "thread": thread,
        "commitments": list(commitments or []),
    }
    if summary is not None:
        result["summary"] = summary
    return result


def _limit_of(value: object, default: int) -> int:
    try:
        return max(int(value), 1)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 连接与探测（读侧零写）
# ---------------------------------------------------------------------------


def _connect_read() -> sqlite3.Connection | None:
    """打开只读用途连接；库文件不存在 → ``None``（**绝不创建文件**）。

    ``sqlite3.connect`` 对不存在的路径会创建空库文件 —— 那本身就是写副作用，
    因此先 ``os.path.exists`` 拦住。建表一律不触发（``ensure_schema=False``）。
    """
    if not os.path.exists(str(events._DB_PATH)):
        return None
    return events.connect(ensure_schema=False, busy_timeout_ms=events._READ_BUSY_TIMEOUT_MS)


def _tables_present(conn: sqlite3.Connection) -> bool:
    """三表是否齐全（缺表是业务语义，不抛）。"""
    placeholders = ", ".join("?" * len(_TABLES))
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        _TABLES,
    ).fetchall()
    return len(rows) == len(_TABLES)


# ---------------------------------------------------------------------------
# 行 → 契约字典
# ---------------------------------------------------------------------------


def _row_to_event(row: sqlite3.Row) -> dict:
    """事件行 → 契约字典（``payload_json`` 解析为对象）。

    坏 JSON → ``{"_parse_error": True}``（事件级标记，不让整次回放失败）；
    解析出非对象（如数组 / 标量）→ ``{"_value": ...}``，与既有账本样板一致。
    """
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:  # noqa: BLE001 —— 坏 payload 只标记，不算 store 故障
        payload = {"_parse_error": True}
    if not isinstance(payload, dict):
        payload = {"_value": payload}
    return {
        "event_id": row["event_id"],
        "user_key": row["user_key"],
        "session_key": row["session_key"],
        "owner_user_id": int(row["owner_user_id"]),
        "character_id": row["character_id"] or "",
        "turn_id": row["turn_id"] or "",
        "thread_id": row["thread_id"] or "",
        "kind": row["kind"],
        "role": row["role"] or "assistant",
        "source": row["source"] or "",
        "payload": payload,
        "created_at": row["created_at"],
    }


def _thread_for(
    conn: sqlite3.Connection,
    *,
    user_key: str,
    session_key: str,
    owner_user_id: int,
    event_rows: list[dict],
) -> dict | None:
    """会话线程行；优先用事件行引用的 ``thread_id``（最新事件优先），否则取会话最新。"""
    candidates: list[str] = []
    for row in reversed(event_rows):
        tid = row["thread_id"]
        if tid and tid not in candidates:
            candidates.append(tid)
    for tid in candidates:
        row = conn.execute(
            f"SELECT * FROM agent_threads WHERE {_SCOPE_WHERE} AND thread_id=?",
            (owner_user_id, user_key, session_key, tid),
        ).fetchone()
        if row is not None:
            return dict(row)
    row = conn.execute(
        f"SELECT * FROM agent_threads WHERE {_SCOPE_WHERE}"
        " ORDER BY last_event_at DESC, rowid DESC LIMIT 1",
        (owner_user_id, user_key, session_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _commitments_for_turn(
    conn: sqlite3.Connection,
    *,
    user_key: str,
    session_key: str,
    owner_user_id: int,
    turn_id: str,
    limit: int,
) -> list[dict]:
    """**只带同一 turn 的承诺**（``source_turn_id`` 相等），按写入顺序升序。"""
    rows = conn.execute(
        f"SELECT * FROM agent_commitments WHERE {_SCOPE_WHERE} AND source_turn_id=?"
        " ORDER BY rowid ASC LIMIT ?",
        (owner_user_id, user_key, session_key, turn_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _commitments_for_session(
    conn: sqlite3.Connection,
    *,
    user_key: str,
    session_key: str,
    owner_user_id: int,
    limit: int,
) -> list[dict]:
    """会话全部承诺的最新 ``limit`` 条（取尾窗后转时间升序）。"""
    rows = conn.execute(
        f"SELECT * FROM agent_commitments WHERE {_SCOPE_WHERE} ORDER BY rowid DESC LIMIT ?",
        (owner_user_id, user_key, session_key, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _summarize(event_rows: list[dict]) -> dict:
    """会话级摘要：事件数 / 按 kind / 按 payload.phase / 不同 turn 数。"""
    by_kind: dict[str, int] = {}
    by_phase: dict[str, int] = {}
    turn_ids: set[str] = set()
    for row in event_rows:
        kind = row["kind"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        payload = row["payload"]
        phase = payload.get("phase") if isinstance(payload, dict) else None
        if isinstance(phase, str) and phase:
            by_phase[phase] = by_phase.get(phase, 0) + 1
        if row["turn_id"]:
            turn_ids.add(row["turn_id"])
    return {
        "event_count": len(event_rows),
        "by_kind": by_kind,
        "by_phase": by_phase,
        "turn_count": len(turn_ids),
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def replay_turn(
    session_key: str,
    turn_id: str,
    *,
    user_key: str = "",
    limit: int = 500,
) -> dict:
    """回放单个 turn（按 ``id`` 升序，取前 ``limit`` 条）。

    返回 ``{'ok','reason','user_key','owner_user_id','session_key','turn_id',
    'events','thread','commitments'}``；``turn_id`` 为空 → ``turn_id_required``
    且**不连接 DB**。
    """
    tid = str(turn_id or "").strip()
    if not tid:
        return _result(
            ok=False,
            reason="turn_id_required",
            user_key=str(user_key or "").strip(),
            owner_user_id=0,
            session_key=str(session_key or "").strip(),
        )

    scope, reason, uk, owner_id = _resolve_scope(session_key, user_key)
    if scope is None:
        return _result(
            ok=False,
            reason=reason,
            user_key=uk,
            owner_user_id=owner_id,
            session_key=str(session_key or "").strip(),
            turn_id=tid,
        )
    uk, sk, owner_id = scope
    capped = _limit_of(limit, _TURN_LIMIT_DEFAULT)

    conn = _connect_read()
    if conn is None:
        return _result(
            ok=False,
            reason="tables_missing",
            user_key=uk,
            owner_user_id=owner_id,
            session_key=sk,
            turn_id=tid,
        )
    with closing(conn):
        if not _tables_present(conn):
            return _result(
                ok=False,
                reason="tables_missing",
                user_key=uk,
                owner_user_id=owner_id,
                session_key=sk,
                turn_id=tid,
            )
        rows = conn.execute(
            _GROUPED_COLUMNS + _SCOPE_WHERE + " AND turn_id=? ORDER BY id ASC LIMIT ?",
            (owner_id, uk, sk, tid, capped),
        ).fetchall()
        event_rows = [_row_to_event(row) for row in rows]
        thread = _thread_for(
            conn,
            user_key=uk,
            session_key=sk,
            owner_user_id=owner_id,
            event_rows=event_rows,
        )
        commitments = _commitments_for_turn(
            conn,
            user_key=uk,
            session_key=sk,
            owner_user_id=owner_id,
            turn_id=tid,
            limit=capped,
        )
    return _result(
        ok=True,
        reason="",
        user_key=uk,
        owner_user_id=owner_id,
        session_key=sk,
        turn_id=tid,
        event_rows=event_rows,
        thread=thread,
        commitments=commitments,
    )


def replay_session(
    session_key: str,
    *,
    user_key: str = "",
    limit: int = 200,
    kinds: list[str] | None = None,
) -> dict:
    """回放会话最近 ``limit`` 条事件（尾窗后转时间升序）+ ``summary``。

    额外键 ``summary`` = ``{'event_count','by_kind','by_phase','turn_count'}``；
    ``kinds`` 非空时按 kind 过滤（过滤发生在 ``LIMIT`` 之前，窗口对同一谓词一致）。
    """
    scope, reason, uk, owner_id = _resolve_scope(session_key, user_key)
    sk = str(session_key or "").strip()
    if scope is None:
        return _result(
            ok=False,
            reason=reason,
            user_key=uk,
            owner_user_id=owner_id,
            session_key=sk,
            summary={"event_count": 0, "by_kind": {}, "by_phase": {}, "turn_count": 0},
        )
    uk, sk, owner_id = scope
    capped = _limit_of(limit, _SESSION_LIMIT_DEFAULT)

    wanted: list[str] = []
    for raw_kind in kinds or []:
        kind = str(raw_kind or "").strip()
        if kind and kind not in wanted:
            wanted.append(kind)

    sql = _GROUPED_COLUMNS + _SCOPE_WHERE
    params: list[object] = [owner_id, uk, sk]
    if wanted:
        sql += f" AND kind IN ({', '.join('?' * len(wanted))})"
        params.extend(wanted)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(capped)

    conn = _connect_read()
    if conn is None:
        return _result(
            ok=False,
            reason="tables_missing",
            user_key=uk,
            owner_user_id=owner_id,
            session_key=sk,
            summary={"event_count": 0, "by_kind": {}, "by_phase": {}, "turn_count": 0},
        )
    with closing(conn):
        if not _tables_present(conn):
            return _result(
                ok=False,
                reason="tables_missing",
                user_key=uk,
                owner_user_id=owner_id,
                session_key=sk,
                summary={"event_count": 0, "by_kind": {}, "by_phase": {}, "turn_count": 0},
            )
        tail = conn.execute(sql, params).fetchall()
        event_rows = [_row_to_event(row) for row in reversed(tail)]
        thread = _thread_for(
            conn,
            user_key=uk,
            session_key=sk,
            owner_user_id=owner_id,
            event_rows=event_rows,
        )
        commitments = _commitments_for_session(
            conn,
            user_key=uk,
            session_key=sk,
            owner_user_id=owner_id,
            limit=capped,
        )
    return _result(
        ok=True,
        reason="",
        user_key=uk,
        owner_user_id=owner_id,
        session_key=sk,
        event_rows=event_rows,
        thread=thread,
        commitments=commitments,
        summary=_summarize(event_rows),
    )
