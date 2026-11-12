"""agent_core 承诺层 —— 「我说过要做什么」（B-1）。

**归属硬约束**：``fulfil_commitment`` 的 ``user_key`` 是**必填关键字**，为空直接
``False``；SQL 恒带 ``commitment_id=? AND user_key=? AND owner_user_id=? AND
status='open'`` 三谓词（按 ``ref`` 定位时把首谓词换成 ``ref=?``，其余不变）——
修「水平越权写」：只凭 id 或只凭会话键都能改别人行的旧式写法在此不存在。

**detail 清洗**：``_clean_detail`` 只做「去控制字符 + 折叠空白 + clip 200」，
**不 import** orchestrator 的 ``strip_internal_identifiers``（承诺内容是用户可见
语义，不是出站回复；引入出站守卫会把清洗职责双 owner 化）。

**幂等**：``dedupe_key`` 非空 → ``commitment_id = uuid5(NAMESPACE_URL,
f"agent_core:{user_key}|{dedupe_key}")`` + ``INSERT OR IGNORE``（种子含 user_key，
与事件层同格式）；重放同一提醒不会产生第二行。

表由 :mod:`agent_core.events` 唯一拥有。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import closing

from agent_core import events

_NAMESPACE_URL = uuid.NAMESPACE_URL

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_DETAIL_MAX = 200


def _clean_detail(detail: object) -> str:
    """去控制字符 → 折叠空白 → strip → clip 200。"""
    raw = _CTRL_RE.sub(" ", str(detail or ""))
    raw = _WS_RE.sub(" ", raw).strip()
    return raw[:_DETAIL_MAX]


def _capped(limit: object, default: int) -> int:
    try:
        return max(int(limit or 0), 1)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def add_commitment(
    detail: str = "",
    *,
    kind: str = "remind",
    user_key: str = "",
    session_key: str = "",
    character_id: str = "",
    thread_id: str = "",
    due_at: str = "",
    expires_at: str = "",
    source_turn_id: str = "",
    ref: str = "",
    dedupe_key: str = "",
    meta: dict | None = None,
) -> str:
    """登记一条承诺；返回 ``commitment_id``（关闭 / 不可归属 / 失败 → 空串）。"""
    if not events.enabled():
        return ""
    scope = events._resolve_scope(user_key=user_key, session_key=session_key)
    if scope is None:
        # B-2：不可归属不是「开关关着」——必须可观测，否则事件被丢与功能未启用
        # 在 failure_count 里无法区分。
        events._note_failure("unattributable", "commitment_add")
        return ""
    uk, sk, owner_id = scope

    key = str(dedupe_key or "").strip()
    commitment_id = str(uuid.uuid5(_NAMESPACE_URL, f"agent_core:{uk}|{key}")) if key else str(uuid.uuid4())
    meta_json = "{}"
    if isinstance(meta, dict) and meta:
        try:
            meta_json = json.dumps(meta, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001 —— 序列化失败降级空对象，不丢承诺
            meta_json = "{}"
    now = events._now_ts()

    if not events._ensure_ready():
        return ""
    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO agent_commitments (commitment_id, user_key,"
                " session_key, owner_user_id, character_id, thread_id, kind, detail,"
                " due_at, expires_at, status, source_turn_id, ref, dedupe_key,"
                " created_at, updated_at, fulfilled_at, meta_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, '', ?)",
                (
                    commitment_id,
                    uk,
                    sk,
                    owner_id,
                    str(character_id or ""),
                    str(thread_id or ""),
                    str(kind or "remind"),
                    _clean_detail(detail),
                    events._normalize_ts(due_at),
                    events._normalize_ts(expires_at),
                    str(source_turn_id or ""),
                    str(ref or ""),
                    key,
                    now,
                    now,
                    meta_json,
                ),
            )
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "commitment_add", exc)
        return ""
    return commitment_id


def list_open_commitments(
    user_key: str,
    *,
    session_key: str | None = None,
    character_id: str | None = None,
    due_before: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """本人未兑现承诺，按 ``(due_at 空在后, due_at, rowid)`` 升序。B-1 主链零调用。

    **B-2 起严格零写**：不再调 ``_ensure_ready()``（旧实现会顺手建表 = 读接口带写
    副作用），库文件不存在时连连接都不开（``sqlite3.connect`` 会创建空库）；
    缺表（``no such table``）按空结果返回，不计为调用方错误。
    """
    uk = str(user_key or "").strip()
    if not uk or not events.enabled():
        return []
    if not os.path.exists(str(events._DB_PATH)):
        return []

    clauses = ["user_key=?", "status='open'"]
    params: list[object] = [uk]
    if session_key:
        clauses.append("session_key=?")
        params.append(str(session_key).strip())
    if character_id:
        clauses.append("character_id=?")
        params.append(str(character_id).strip())
    if due_before:
        stamp = events._normalize_ts(due_before)
        # 不可解析的过滤值不「猜」：退化成不过滤并计数（不能把坏比对当成空结果，
        # 那会让提醒候选静默消失）。
        if stamp:
            clauses.append("due_at<>'' AND due_at<=?")
            params.append(stamp)
        else:
            events._note_failure("shadow_failed", "commitment_due_before_invalid")
    sql = (
        "SELECT * FROM agent_commitments WHERE "
        + " AND ".join(clauses)
        + " ORDER BY (due_at=''), due_at, rowid LIMIT ?"
    )
    params.append(_capped(limit, 50))

    try:
        with closing(events.connect(busy_timeout_ms=events._READ_BUSY_TIMEOUT_MS)) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        # 缺表（开关刚打开、写侧还没建表）/ 库里没有这张表：空结果，不建表。
        events._note_failure("shadow_failed", "commitment_list_missing", exc)
        return []


def fulfil_commitment(
    commitment_id: str = "",
    *,
    user_key: str,
    owner_user_id: int = 0,
    ref: str = "",
    note: str = "",
) -> bool:
    """把 open 承诺置 fulfilled（幂等）；返回是否**本次**真正发生状态转换。"""
    if not events.enabled():
        return False
    uk = str(user_key or "").strip()
    if not uk:
        return False
    cid = str(commitment_id or "").strip()
    ref_key = str(ref or "").strip()
    if not cid and not ref_key:
        return False
    if not events._ensure_ready():
        return False

    now = events._now_ts()
    try:
        owner_id = int(owner_user_id or 0)
    except (TypeError, ValueError):
        return False
    if cid:
        where = "commitment_id=? AND user_key=? AND owner_user_id=? AND status='open'"
        where_params: list[object] = [cid, uk, owner_id]
    else:
        where = "ref=? AND user_key=? AND owner_user_id=? AND status='open'"
        where_params = [ref_key, uk, owner_id]

    note_text = _clean_detail(note)
    if note_text:
        sql = (
            "UPDATE agent_commitments SET status='fulfilled', fulfilled_at=?, updated_at=?,"
            " meta_json=json_set(CASE WHEN json_valid(meta_json) THEN meta_json ELSE '{}' END,"
            " '$.fulfil_note', ?) WHERE " + where
        )
        params: list[object] = [now, now, note_text, *where_params]
    else:
        sql = "UPDATE agent_commitments SET status='fulfilled', fulfilled_at=?, updated_at=? WHERE " + where
        params = [now, now, *where_params]

    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            cursor = conn.execute(sql, params)
            changed = cursor.rowcount
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "commitment_fulfil", exc)
        return False
    return changed > 0


def expire_commitments(now: str | None = None, *, user_key: str = "", limit: int = 500) -> int:
    """把过期 ``open`` 置 expired，返回行数；``user_key`` 为空 = 显式全库维护。"""
    if not events.enabled():
        return 0
    stamp = events._normalize_ts(now) or events._now_ts()
    if not events._ensure_ready():
        return 0

    uk = str(user_key or "").strip()
    clauses = [
        "status='open'",
        "((expires_at<>'' AND expires_at<?) OR (due_at<>'' AND due_at<?))",
    ]
    inner_params: list[object] = [stamp, stamp]
    if uk:
        clauses.append("user_key=?")
        inner_params.append(uk)
    sql = (
        "UPDATE agent_commitments SET status='expired', updated_at=?"
        " WHERE rowid IN (SELECT rowid FROM agent_commitments WHERE "
        + " AND ".join(clauses)
        + " ORDER BY due_at LIMIT ?)"
    )
    params: list[object] = [stamp, *inner_params, _capped(limit, 500)]

    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.rowcount or 0)
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "commitment_expire", exc)
        return 0


def close_commitment(
    commitment_id: str | int = "",
    now: str | None = None,
    reason: str = "",
    *,
    user_key: str = "",
) -> bool:
    """入站回执销账（B-2）：把 ``open`` 承诺置 ``fulfilled``；幂等。

    返回是否**本次**真正发生状态转换 —— 已是 ``fulfilled`` / ``expired`` /
    ``cancelled``、id 不存在、开关关闭、写失败一律 ``False``。

    ``reason``（如 ``'user_receipt'``）落 ``meta_json.$.close_reason``（与
    ``fulfil_commitment`` 的 ``$.fulfil_note`` 同一写法），空值不写键。

    ``user_key``（2026-11-11 复核修复 L1）：归属谓词。传入时 WHERE 追加
    ``user_key=?``，即使调用方拿到别家的 ``commitment_id`` 也无法跨用户销账。
    缺省空串保持旧调用兼容（内部调用方一律应传）。
    """
    if not events.enabled():
        return False
    cid = str(commitment_id or "").strip()
    if not cid:
        return False
    if not events._ensure_ready():
        return False

    stamp = events._normalize_ts(now) or events._now_ts()
    reason_text = _clean_detail(reason)
    uk = str(user_key or "").strip()
    where = "commitment_id=? AND status='open'"
    if uk:
        where += " AND user_key=?"
    if reason_text:
        sql = (
            "UPDATE agent_commitments SET status='fulfilled', fulfilled_at=?, updated_at=?,"
            " meta_json=json_set(CASE WHEN json_valid(meta_json) THEN meta_json ELSE '{}' END,"
            " '$.close_reason', ?) WHERE " + where
        )
        params: list[object] = [stamp, stamp, reason_text, cid]
    else:
        sql = "UPDATE agent_commitments SET status='fulfilled', fulfilled_at=?, updated_at=? WHERE " + where
        params = [stamp, stamp, cid]
    if uk:
        params.append(uk)

    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            changed = conn.execute(sql, params).rowcount
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "commitment_close", exc)
        return False
    return changed > 0
