"""agent_core 会话级影子账本 —— 三表 + 事件写入（B-1）。

**边界**（逐条对应已批准实施契约）：

- 三表 ``agent_events`` / ``agent_threads`` / ``agent_commitments`` 落在
  ``data/sqlite.db``（与 ``topic_state`` / ``character_settings`` 同库），**只新增
  三表九索引**，不 ALTER / 不建 trigger / 不碰既有 42 张表。本模块是这套 schema
  的唯一 owner（``threads`` / ``commitments`` 通过 ``from agent_core import events``
  复用 ``_DB_PATH`` 与连接层，**禁止** ``from agent_core.events import _DB_PATH``，
  否则 monkeypatch 打不中）。
- **开关零行为**：``AGENT_CORE_EVENTS`` 每次调用现读环境（不缓存）；未开时写接口
  首行短路，不打开文件、不建表、不执行任何 SQL。
- **fail-open**：所有写路径 ``try/except Exception``（不吞 BaseException），异常只
  计数 + 首次 WARNING 后降 debug，绝不 re-raise、绝不改返回值。
- **只写不读**：B-1 写侧零 SELECT（幂等去重靠确定性 ``event_id`` + ``INSERT OR IGNORE``）。
- **隔离**：``user_key`` = 完整会话键（authority ``shisi/memory/legacy/
  structured_memory.py::user_key_from_session``，禁止剥 owner）；
  ``owner_user_id`` = ``utils.session_key.owner_of(session_key)``。任一不可得 →
  整条不落库（不可归属宁丢不串）。
- **时钟**：``utils.local_time.now_local()`` 的北京 naive 墙钟
  ``%Y-%m-%d %H:%M:%S``；排序一律按 ``id``/``rowid``，禁止按 ``created_at``。
- **写延迟预算**：``_WRITE_BUSY_TIMEOUT_MS=50``（原 300ms 会阻塞事件循环并拖慢该
  worker 全部并发请求）+ 建表锁 ``_SCHEMA_LOCK_TIMEOUT_S=0.05``（绝不在
  ``threading.Lock`` 上无超时阻塞）；丢事件即计数，不重试。

残余风险如实声明：挂载点跑在 asyncio 事件循环线程，50ms 仍是同步阻塞，锁竞争
最坏 +50ms（进程首写含 DDL 约 +100ms）；队列 / 独立写线程方案留 B-2 按实测决定。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import closing, suppress
from datetime import datetime

from utils import inbound_context
from utils.local_time import now_local
from utils.project_paths import project_path
from utils.session_key import owner_of

logger = logging.getLogger("agent_core.events")

#: 落库路径 —— 可被 monkeypatch（线程/承诺模块必须走 ``events._DB_PATH`` 读它）。
_DB_PATH = project_path("data", "sqlite.db")

_WRITE_BUSY_TIMEOUT_MS = 50
_READ_BUSY_TIMEOUT_MS = 3000
_SCHEMA_LOCK_TIMEOUT_S = 0.05

_TS_FMT = "%Y-%m-%d %H:%M:%S"
_TS_INPUT_FMTS = (_TS_FMT, "%Y-%m-%d %H:%M")

_SWITCH_ENV = "AGENT_CORE_EVENTS"
_TRUTHY = frozenset({"1", "true", "yes"})
_NAMESPACE_URL = uuid.NAMESPACE_URL

#: 三表九索引。⚠️ ``rowid`` **绝不能出现在 CREATE INDEX 列清单**（实测
#: ``no such column: rowid`` 会让整个 executescript 抛错 ⇒ 三表永远建不出来 ⇒
#: 全链静默零事件）。``agent_threads.last_event_at`` 必须在 DDL 里；
#: ``open_thread`` 的 UPDATE 依赖它（实测 ``no such column: last_event_at``）。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    user_key TEXT NOT NULL,
    session_key TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL,
    character_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'assistant',
    source TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_scope
    ON agent_events(owner_user_id, user_key, session_key, id);
CREATE INDEX IF NOT EXISTS idx_agent_events_turn
    ON agent_events(user_key, session_key, turn_id, id);
CREATE INDEX IF NOT EXISTS idx_agent_events_kind
    ON agent_events(kind, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_events_thread
    ON agent_events(thread_id, id);

CREATE TABLE IF NOT EXISTS agent_threads (
    thread_id TEXT PRIMARY KEY,
    user_key TEXT NOT NULL,
    session_key TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL,
    character_id TEXT NOT NULL DEFAULT '',
    topic TEXT DEFAULT '',
    last_utterance TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'awaiting_user', 'closed')),
    opened_at TEXT NOT NULL,
    last_event_at TEXT NOT NULL DEFAULT '',
    closed_at TEXT DEFAULT '',
    close_reason TEXT DEFAULT '',
    turn_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    meta_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_threads_scope
    ON agent_threads(owner_user_id, user_key, session_key, status);
CREATE INDEX IF NOT EXISTS idx_agent_threads_open
    ON agent_threads(status, last_event_at DESC);

CREATE TABLE IF NOT EXISTS agent_commitments (
    commitment_id TEXT PRIMARY KEY,
    user_key TEXT NOT NULL,
    session_key TEXT NOT NULL DEFAULT '',
    owner_user_id INTEGER NOT NULL,
    character_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'remind',
    detail TEXT NOT NULL DEFAULT '',
    due_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'fulfilled', 'expired', 'cancelled')),
    source_turn_id TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fulfilled_at TEXT DEFAULT '',
    meta_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_commitments_open
    ON agent_commitments(owner_user_id, user_key, status, due_at);
CREATE INDEX IF NOT EXISTS idx_agent_commitments_session
    ON agent_commitments(user_key, session_key, status);
CREATE INDEX IF NOT EXISTS idx_agent_commitments_ref
    ON agent_commitments(user_key, ref);
"""

_SCHEMA_LOCK = threading.Lock()
_schema_ready: set[str] = set()
_warned: set[str] = set()
_fail_counts: dict[tuple[str, str], int] = {}
_warn_lock = threading.Lock()
_shadow_ready_logged = False


# ---------------------------------------------------------------------------
# 开关与身份
# ---------------------------------------------------------------------------


def enabled() -> bool:
    """``AGENT_CORE_EVENTS`` 现读（不缓存）：``1`` / ``true`` / ``yes`` → 开。"""
    return str(os.environ.get(_SWITCH_ENV, "") or "").strip().lower() in _TRUTHY


def turn_key(message_id: str = "") -> str:
    """**轮次身份唯一 owner**：全链 hook 只准调它拼 ``turn_id``。

    显式 ``message_id`` 优先，缺省读 :mod:`utils.inbound_context` 的请求级
    ContextVar；都没有（web / console / 主动线）返回空串 —— 调用方据此不做
    轮次级去重，宁可不 dedup，也不拿别的消息的键误吞本轮。
    """
    mid = str(message_id or "").strip()
    if not mid:
        mid = str(inbound_context.current_message_id() or "").strip()
    return f"m:{mid}" if mid else ""


def _user_key_from_session(session_id: str) -> str:
    """会话键 → 隔离 user_key（与 structured_memory 权威逐字一致）。

    authority：``shisi/memory/legacy/structured_memory.py::user_key_from_session``
    —— **返回完整会话键，禁止剥 owner**（`N:wxid` 本身才是隔离域）。
    """
    if not session_id:
        return ""
    return str(session_id).strip()


def _resolve_scope(*, user_key: str = "", session_key: str = "") -> tuple[str, str, int] | None:
    """``(user_key, session_key, owner_user_id)``；不可归属 → ``None``。

    兼容"只给 user_key"（user_key 本身就是完整会话键）与"只给 session_key"。
    显式 ``user_key`` 与 session 推出的键不一致 ⇒ 拒绝（防跨用户写）。
    """
    sk = str(session_key or "").strip() or str(user_key or "").strip()
    if not sk:
        return None
    uk = _user_key_from_session(sk)
    if not uk:
        return None
    explicit = str(user_key or "").strip()
    if explicit and explicit != uk:
        return None
    owner = owner_of(sk)
    if owner is None:
        return None
    return uk, sk, int(owner)


# ---------------------------------------------------------------------------
# 时钟
# ---------------------------------------------------------------------------


def _now_ts() -> str:
    """当前北京墙钟 ``%Y-%m-%d %H:%M:%S``（禁 CURRENT_TIMESTAMP 的 UTC）。"""
    return now_local().strftime(_TS_FMT)


def _normalize_ts(value: object) -> str:
    """归一 ``%Y-%m-%d %H:%M`` / ``%Y-%m-%d %H:%M:%S``；不可解析 → 空串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in _TS_INPUT_FMTS:
        try:
            return datetime.strptime(raw, fmt).strftime(_TS_FMT)
        except ValueError:
            continue
    return ""


# ---------------------------------------------------------------------------
# 可观测性（建表/写入失败不得静默）
# ---------------------------------------------------------------------------


def _note_failure(step: str, kind: str = "", exc: BaseException | None = None) -> None:
    """计数 + 每 ``(step, kind)`` 首次 WARNING，重复降 debug（不含会话键/正文）。"""
    key = (str(step or ""), str(kind or ""))
    with _warn_lock:
        _fail_counts[key] = _fail_counts.get(key, 0) + 1
        count = _fail_counts[key]
        first = key not in _warned
        if first:
            _warned.add(key)
    if first:
        message = ""
        if exc is not None:
            message = f"{type(exc).__name__}: {str(exc)[:120]}"
        logger.warning(
            "[agent_core][step=%s] kind=%s error_type=%s msg=%s",
            key[0],
            key[1],
            type(exc).__name__ if exc is not None else "",
            message,
        )
    else:
        logger.debug("[agent_core][step=%s] kind=%s repeat=%d", key[0], key[1], count)


def failure_count(step: str) -> int:
    """某 step 的失败累计次数（测试与运维断言用）。"""
    name = str(step or "")
    with _warn_lock:
        return sum(count for (step_name, _kind), count in _fail_counts.items() if step_name == name)


def _log_shadow_ready_once() -> None:
    global _shadow_ready_logged
    if _shadow_ready_logged:
        return
    with _warn_lock:
        if _shadow_ready_logged:
            return
        _shadow_ready_logged = True
    logger.info("[agent_core][step=shadow_ready]")


def _is_busy_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


# ---------------------------------------------------------------------------
# schema 与连接
# ---------------------------------------------------------------------------


def ensure_schema() -> bool:
    """幂等建三表九索引（一条短连接 executescript 后立即关闭；失败不置位）。"""
    key = str(_DB_PATH)
    if key in _schema_ready:
        return True
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            str(_DB_PATH),
            timeout=_WRITE_BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,
        )
        with suppress(sqlite3.Error):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
    except Exception as exc:  # noqa: BLE001 —— fail-open：建表失败只计数
        _note_failure("schema_failed", "", exc)
        return False
    finally:
        if conn is not None:
            with suppress(Exception):  # noqa: BLE001 —— 关闭失败不遮住主结果
                conn.close()
    _schema_ready.add(key)
    return True


def _ensure_ready() -> bool:
    """五条写路径各自先调它；锁等待有上限（0.05s），拿不到即放弃本次写。"""
    key = str(_DB_PATH)
    if key in _schema_ready:
        return True
    if not _SCHEMA_LOCK.acquire(timeout=_SCHEMA_LOCK_TIMEOUT_S):
        _note_failure("schema_locked", "", None)
        return False
    try:
        if key in _schema_ready:
            return True
        return ensure_schema()
    finally:
        _SCHEMA_LOCK.release()


def connect(*, ensure_schema: bool = False, busy_timeout_ms: int = 50) -> sqlite3.Connection:
    """短连接：``autocommit`` + ``Row`` + ``busy_timeout`` + ``synchronous=NORMAL``。

    写路径缺省 50ms；回放读路径显式传 3000。**调用方必须** ``closing(...)`` 包住
    （本模块内全部如此），连接生命周期不跨函数。
    """
    if ensure_schema:
        _ensure_ready()
    try:
        timeout_ms = max(int(busy_timeout_ms), 0)
    except (TypeError, ValueError):
        timeout_ms = _WRITE_BUSY_TIMEOUT_MS
    conn = sqlite3.connect(str(_DB_PATH), timeout=timeout_ms / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    with suppress(sqlite3.Error):
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# 事件写入（唯一写入口）
# ---------------------------------------------------------------------------


def record_event(
    kind: str,
    *,
    user_key: str = "",
    session_key: str = "",
    character_id: str = "",
    turn_id: str = "",
    thread_id: str = "",
    role: str = "assistant",
    source: str = "",
    payload: dict | None = None,
    dedupe_key: str = "",
    created_at: str | None = None,
) -> str:
    """追加一条事件；返回 ``event_id``（关闭 / 不可归属 / 失败 → 空串）。

    ``dedupe_key`` 非空 → ``event_id = uuid5(NAMESPACE_URL, f"agent_core:{user_key}|{dedupe_key}")``
    + ``INSERT OR IGNORE``（种子含 user_key，与 commitments 同格式），原子去重且
    写侧零 SELECT。
    """
    if not enabled():
        return ""
    scope = _resolve_scope(user_key=user_key, session_key=session_key)
    if scope is None:
        # B-2：不可归属必须可观测——否则「事件被丢」与「开关关着（首行短路）」
        # 在 failure_count 里完全同形，运维无法区分。
        _note_failure("unattributable", str(kind or ""))
        return ""
    uk, sk, owner_id = scope

    key = str(dedupe_key or "").strip()
    event_id = str(uuid.uuid5(_NAMESPACE_URL, f"agent_core:{uk}|{key}")) if key else str(uuid.uuid4())
    body = payload if isinstance(payload, dict) else {}
    try:
        payload_json = json.dumps(body, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 —— 序列化失败降级空对象，不丢事件
        payload_json = "{}"
    created_ts = _normalize_ts(created_at) or _now_ts()

    if not _ensure_ready():
        return ""
    try:
        with closing(connect(busy_timeout_ms=_WRITE_BUSY_TIMEOUT_MS)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO agent_events (event_id, user_key, session_key,"
                " owner_user_id, character_id, turn_id, thread_id, kind, role, source,"
                " payload_json, dedupe_key, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    uk,
                    sk,
                    owner_id,
                    str(character_id or ""),
                    str(turn_id or ""),
                    str(thread_id or ""),
                    str(kind or ""),
                    str(role or "assistant"),
                    str(source or ""),
                    payload_json,
                    key,
                    created_ts,
                ),
            )
    except Exception as exc:  # noqa: BLE001 —— fail-open：丢事件但可观测
        step = "busy" if _is_busy_error(exc) else "shadow_failed"
        _note_failure(step, str(kind or ""), exc)
        return ""
    _log_shadow_ready_once()
    return event_id
