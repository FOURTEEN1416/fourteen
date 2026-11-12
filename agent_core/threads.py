"""agent_core 线程层 —— 「这一轮我在说什么」的会话级单例（B-1）。

**线程身份**：``thread_id = uuid5(NAMESPACE_URL, f"{user_key}|{session_key}")``，
**不含 character_id** —— 入站 hook 早于角色解析，而两处解析器实现不同
（``user_scheduler.py:432-448`` 手写 ``self._bindings`` 遍历 vs
``wechat_connector.py:2036-2045`` 走 ``utils.character_resolver``），本会话未证明
恒等；因此线程身份只按 (user × session)，角色归属以**事件行**为准（修「线程身份
漂移」：同一轮因两处解析器不一致落成两条线程）。

**调用方**：``open_thread`` 唯一调用方 = 入站 hook；``close_thread`` B-1 无生产
调用方（为 B-2 预留，仅测试覆盖）。

表由 :mod:`agent_core.events` 唯一拥有；本模块只保管句、写那一行。
"""

from __future__ import annotations

import uuid
from contextlib import closing

from agent_core import events

_NAMESPACE_URL = uuid.NAMESPACE_URL


def thread_id_for(user_key: str, session_key: str = "") -> str:
    """(user, session) → 稳定 thread_id（纯函数，便于回放侧交叉引用）。"""
    uk = events._user_key_from_session(user_key) or str(user_key or "").strip()
    sk = str(session_key or "").strip() or uk
    if not uk or not sk:
        return ""
    return str(uuid.uuid5(_NAMESPACE_URL, f"{uk}|{sk}"))


def open_thread(
    user_key: str = "",
    session_key: str = "",
    character_id: str = "",
    *,
    topic: str = "",
    last_utterance: str = "",
) -> str:
    """打开/续开线程并记一次 turn；返回 ``thread_id``（关闭或不可归属 → 空串）。

    ``INSERT OR IGNORE`` 建行后**单条 UPDATE**：``status='open'``、
    清空 ``closed_at`` / ``close_reason``（重开）、刷新 ``last_event_at`` /
    ``updated_at``、``turn_count + 1``。两语句各自 autocommit，无长事务。
    """
    if not events.enabled():
        return ""
    scope = events._resolve_scope(user_key=user_key, session_key=session_key)
    if scope is None:
        events._note_failure("unattributable", "thread_open")
        return ""
    uk, sk, owner_id = scope
    tid = thread_id_for(uk, sk)
    if not tid:
        return ""
    if not events._ensure_ready():
        return ""
    now = events._now_ts()
    cid = str(character_id or "")
    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO agent_threads (thread_id, user_key, session_key,"
                " owner_user_id, character_id, topic, last_utterance, status, opened_at,"
                " last_event_at, closed_at, close_reason, turn_count, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, '', '', 0, ?, ?)",
                (
                    tid,
                    uk,
                    sk,
                    owner_id,
                    cid,
                    str(topic or ""),
                    str(last_utterance or ""),
                    now,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                # B-2：UPDATE 回填 character_id —— 入站时角色尚未解析（传空），
                # 后续解析到角色再 open 同一线程时补写；传空保持原值，不抹掉旧值。
                "UPDATE agent_threads SET status='open', closed_at='', close_reason='',"
                " character_id=CASE WHEN ? <> '' THEN ? ELSE character_id END,"
                " last_event_at=?, updated_at=?, turn_count=turn_count+1"
                " WHERE thread_id=? AND user_key=?",
                (cid, cid, now, now, tid, uk),
            )
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "thread_open", exc)
        return ""
    return tid


def close_thread(
    thread_id: str = "",
    *,
    user_key: str = "",
    session_key: str = "",
    reason: str = "",
) -> bool:
    """关闭线程；仅 ``open`` / ``awaiting_user`` 可转，重复关闭 → ``False``。"""
    if not events.enabled():
        return False
    tid = str(thread_id or "").strip()
    if not tid:
        return False
    uk = str(user_key or "").strip() or events._user_key_from_session(session_key)
    if not uk:
        return False
    if not events._ensure_ready():
        return False
    now = events._now_ts()
    try:
        with closing(events.connect(busy_timeout_ms=events._WRITE_BUSY_TIMEOUT_MS)) as conn:
            cursor = conn.execute(
                "UPDATE agent_threads SET status='closed', closed_at=?, close_reason=?,"
                " updated_at=? WHERE thread_id=? AND user_key=? AND status<>'closed'",
                (now, str(reason or ""), now, tid, uk),
            )
            changed = cursor.rowcount
    except Exception as exc:  # noqa: BLE001 —— fail-open
        step = "busy" if events._is_busy_error(exc) else "shadow_failed"
        events._note_failure(step, "thread_close", exc)
        return False
    return changed > 0
