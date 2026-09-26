"""后台单一运行时的**最小可信控制面**（SQLite，持久命令 + 租约 + 回执）。

**为什么存在**（2026-09-27 W3 根治，缺陷 A/B/C/G）：生产为多 uvicorn worker，
调度主（flock 选主）、微信连接器（per-slot poll flock，可能落在任意 worker）、
WS 端口（谁先 bind 谁驻留）三者**各自独立选主、互不知情**：

  - scheduler 在 A、连接器在 B、WS 在 C 时，A 的本地 holder/registry 全是空
    → 主动消息/提醒"通道不可用"判失败 → 3 次判死（功能整体不可达）；
  - run_api 的 wechat sender 对同 owner 的**全部 slot 逐一发送且不 break**
    （重复投递）；
  - 断连/停用只是进程内操作，别的 worker 的 poller 照跑、重启照恢复。

本模块用**一块 SQLite**（默认 ``data/runtime_plane.db``，复用既有 SQLite 能力，
不引入消息栈）把上述事实变成跨进程可见的持久状态：

  ╭ 表                      ═ 唯一职责
  ├ channel_desired_state    用户对 (owner, slot) 的**期望态**（connected /
  │                          disabled）—— 任何 worker 写，宿主 worker 读；
  │                          restore_on_boot / 自动启动**必须先查此表**（缺陷 B）
  ├ channel_presence         连接器宿主租约（pid + 心跳）—— 全局存活判据，
  │                          API worker 不得再用本地 registry 认定全局状态
  ├ outbound_commands        **出站投递命令 + 受理回执**：投递方只 enqueue，
  │                          资源宿主（连接器 / WS 持有者）CAS 认领、真实
  │                          投递、回写 accepted/failed 回执；等待方凭回执判
  │                          成败。一行只会被认领一次 ⇒ 同 owner 双 slot 天然
  │                          不混投（缺陷 C），"delivered=True 无确认"消失
  │                          （缺陷 E）。不承诺 exactly-once：回执=受理确认。
  ├ control_commands         控制命令（如 proactive_manual）—— master 消费
  ├ inbound_seen             入站 message_id 幂等账（claim→done/release）
  ├ followup_budget          追问日预算（跨重启，缺陷 J）
  ├ effect_dedup             工具副作用幂等（set_reminder 按 message_id 去重，
  │                          缺陷 I）

并发口径：所有写路径都是**单语句 CAS**（``UPDATE ... WHERE status='pending'``
判 ``rowcount``），SQLite ``timeout=30`` + busy 重试兜底；WAL 让多进程读写
不互阻。测试通过 :func:`set_db_path` 重定向到临时数据根。
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from utils.project_paths import project_path

logger = logging.getLogger("proactive.runtime_plane")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_desired_state (
    owner_id   INTEGER NOT NULL,
    slot       INTEGER NOT NULL,
    desired    TEXT    NOT NULL DEFAULT 'connected',
    updated_at REAL    NOT NULL,
    PRIMARY KEY (owner_id, slot)
);
CREATE TABLE IF NOT EXISTS channel_presence (
    owner_id   INTEGER NOT NULL,
    slot       INTEGER NOT NULL,
    host_pid   TEXT    NOT NULL,
    heartbeat  REAL    NOT NULL,
    PRIMARY KEY (owner_id, slot)
);
CREATE TABLE IF NOT EXISTS outbound_commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL,
    channel      TEXT    NOT NULL,
    session_key  TEXT    NOT NULL DEFAULT '',
    owner_id     INTEGER,
    slot         INTEGER,
    peer         TEXT    NOT NULL DEFAULT '',
    character_id TEXT    NOT NULL DEFAULT '',
    turn_id      TEXT    NOT NULL DEFAULT '',
    message      TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending',
    claimed_by   TEXT    NOT NULL DEFAULT '',
    claim_expires REAL   NOT NULL DEFAULT 0.0,
    attempts     INTEGER NOT NULL DEFAULT 0,
    fail_reason  TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    accepted_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_outbox_claim
    ON outbound_commands (status, channel, owner_id);
CREATE TABLE IF NOT EXISTS control_commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL,
    session_key  TEXT    NOT NULL DEFAULT '',
    payload      TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending',
    claimed_by   TEXT    NOT NULL DEFAULT '',
    claim_expires REAL   NOT NULL DEFAULT 0.0,
    attempts     INTEGER NOT NULL DEFAULT 0,
    result       TEXT    NOT NULL DEFAULT '',
    fail_reason  TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    accepted_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_control_claim
    ON control_commands (status, kind);
CREATE TABLE IF NOT EXISTS inbound_seen (
    message_id   TEXT PRIMARY KEY,
    session_key  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'claimed',
    ts           REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS followup_budget (
    session_key  TEXT NOT NULL,
    day          TEXT NOT NULL,
    used         INTEGER NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (session_key, day)
);
CREATE TABLE IF NOT EXISTS effect_dedup (
    dedup_key    TEXT PRIMARY KEY,
    result       TEXT NOT NULL DEFAULT '',
    ts           REAL NOT NULL
);
"""

_db_lock = threading.Lock()
_db_path: Path | None = None


def set_db_path(path: str | Path | None) -> None:
    """重定向控制面 DB（测试沙箱 / 装配层注入）。传 None 恢复默认。"""
    global _db_path
    with _db_lock:
        _db_path = Path(path) if path is not None else None
        if path is None:
            _initialized.discard(str(_default_path()))


def _default_path() -> Path:
    return project_path("data", "runtime_plane.db")


def db_path() -> Path:
    with _db_lock:
        return _db_path if _db_path is not None else _default_path()


_initialized: set[str] = set()
_init_guard = threading.Lock()


@contextlib.contextmanager
def _conn() -> Any:
    """短连接：退出时先 commit（异常回滚）再 **close**。

    ``sqlite3.Connection`` 自身的 ``__exit__`` 只提交/回滚不关闭 —— 直接
    ``with sqlite3.connect(...)`` 每次调用都漏一条连接。
    """
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    key = str(p)
    if key not in _initialized:
        with _init_guard:
            if key not in _initialized:
                raw = sqlite3.connect(str(p), timeout=30)
                try:
                    raw.executescript(_SCHEMA)
                    raw.commit()
                    with contextlib.suppress(sqlite3.OperationalError):
                        raw.execute("PRAGMA journal_mode=WAL")
                        raw.commit()
                finally:
                    raw.close()
                _initialized.add(key)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def host_id() -> str:
    """本进程在控制面上的身份（pid + 短随机后缀，区分 fork/多线程宿主）。"""
    return f"{os.getpid()}:{threading.get_ident():x}"


def _now() -> float:
    return time.time()


# ── desired state（用户对 (owner, slot) 的期望态） ────────────


def set_desired(owner_id: int, slot: int, desired: str) -> None:
    """写期望态：``connected`` / ``disabled``。任何 worker 可调（幂等 UPSERT）。"""
    assert desired in ("connected", "disabled"), desired
    with _conn() as conn:
        conn.execute(
            "INSERT INTO channel_desired_state (owner_id, slot, desired, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(owner_id, slot) DO UPDATE SET desired=excluded.desired, "
            "updated_at=excluded.updated_at",
            (int(owner_id), int(slot), desired, _now()),
        )
        conn.commit()


def get_desired(owner_id: int, slot: int) -> str:
    """读期望态。无记录 = ``connected``（未显式停用；与"从没建过通道"同默认，
    自动启动方另有 credentials 存在性判据）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT desired FROM channel_desired_state WHERE owner_id=? AND slot=?",
            (int(owner_id), int(slot)),
        ).fetchone()
    return str(row["desired"]) if row else "connected"


# ── presence（连接器宿主租约） ────────────────────────────────

PRESENCE_TTL = 90.0  # 心跳过期即视为宿主失联（连接器轮询周期远小于此）


def heartbeat(owner_id: int, slot: int, pid: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO channel_presence (owner_id, slot, host_pid, heartbeat) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(owner_id, slot) DO UPDATE SET host_pid=excluded.host_pid, "
            "heartbeat=excluded.heartbeat",
            (int(owner_id), int(slot), pid or host_id(), _now()),
        )
        conn.commit()


def presence_release(owner_id: int, slot: int) -> None:
    with _conn() as conn:
        conn.execute(
            "DELETE FROM channel_presence WHERE owner_id=? AND slot=?",
            (int(owner_id), int(slot)),
        )
        conn.commit()


def presence_live(owner_id: int, slot: int, ttl: float = PRESENCE_TTL) -> bool:
    """(owner, slot) 是否有**活着的**连接器宿主（跨进程全局判据）。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT heartbeat FROM channel_presence WHERE owner_id=? AND slot=?",
            (int(owner_id), int(slot)),
        ).fetchone()
    return bool(row) and (_now() - float(row["heartbeat"])) < ttl


def live_slots(owner_id: int, ttl: float = PRESENCE_TTL) -> list[int]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT slot FROM channel_presence WHERE owner_id=? AND heartbeat >= ?",
            (int(owner_id), _now() - ttl),
        ).fetchall()
    return [int(r["slot"]) for r in rows]


# ── outbound commands（投递命令 + 受理回执） ──────────────────

CLAIM_TTL = 60.0      # 认领后多久无回执即可被回收重投
PENDING_EXPIRE = 3600.0  # 无人认领的 pending 行多久判死（资源始终缺席）


def enqueue_send(
    *,
    kind: str,
    channel: str,
    session_key: str = "",
    owner_id: int | None = None,
    slot: int | None = None,
    peer: str = "",
    character_id: str = "",
    turn_id: str = "",
    message: str = "",
) -> int:
    """入队一条出站投递命令，返回命令 id。

    ``channel``: ``wechat`` / ``websocket``。``slot`` 可为 None —— 由**任一**
    活着的同 owner 连接器宿主 CAS 认领（认领者即投递目标），天然杜绝
    "同 owner 全部 slot 逐一发送"的混投（缺陷 C）。
    """
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO outbound_commands (kind, channel, session_key, owner_id, "
            "slot, peer, character_id, turn_id, message, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
            (kind, channel, session_key,
             None if owner_id is None else int(owner_id),
             None if slot is None else int(slot),
             peer, character_id, turn_id, message, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def _reap_expired(conn: sqlite3.Connection, table: str, id_col: str = "id") -> None:
    """回收过期认领（宿主崩溃 → 重投）；过老 pending → 判死。"""
    conn.execute(
        f"UPDATE {table} SET status='pending', claimed_by='', claim_expires=0, "
        "attempts=attempts+1 WHERE status='claimed' AND claim_expires < ?",
        (_now(),),
    )
    conn.execute(
        f"UPDATE {table} SET status='failed', fail_reason='expired_unclaimed' "
        "WHERE status='pending' AND created_at < ?",
        (_now() - PENDING_EXPIRE,),
    )


def claim_next_send(
    *, channel: str, owner_id: int | None = None, slot: int | None = None,
) -> dict[str, Any] | None:
    """CAS 认领一条待投命令。

    微信侧连接器宿主调用：``claim_next_send(channel='wechat',
    owner_id=self.owner, slot=self.slot)`` —— 匹配"点名本 slot"或"未点名
    slot 且 owner 相符"的行；每行只会被一个宿主认领成功。
    """
    with _conn() as conn:
        _reap_expired(conn, "outbound_commands")
        where = ["status='pending'", "channel=?"]
        params: list[Any] = [channel]
        if owner_id is not None or slot is not None:
            conds = []
            if owner_id is not None:
                conds.append("(owner_id=? AND (slot IS NULL OR slot=?))")
                params.extend([int(owner_id), None if slot is None else int(slot)])
            if slot is not None and owner_id is None:
                conds.append("slot=?")
                params.append(int(slot))
            where.append("(" + " OR ".join(conds) + ")")
        row = conn.execute(
            f"SELECT id FROM outbound_commands WHERE {' AND '.join(where)} "
            "ORDER BY id ASC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        cid = int(row["id"])
        cur = conn.execute(
            "UPDATE outbound_commands SET status='claimed', claimed_by=?, "
            "claim_expires=?, slot=COALESCE(slot, ?), attempts=attempts+1 "
            "WHERE id=? AND status='pending'",
            (host_id(), _now() + CLAIM_TTL,
             None if slot is None else int(slot), cid),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        full = conn.execute(
            "SELECT * FROM outbound_commands WHERE id=?", (cid,)
        ).fetchone()
        return dict(full) if full else None


def complete_send(cmd_id: int, *, ok: bool, reason: str = "", slot: int | None = None) -> None:
    """回写受理回执：``ok=True`` → accepted（受理确认，非 exactly-once）。"""
    with _conn() as conn:
        conn.execute(
            "UPDATE outbound_commands SET status=?, fail_reason=?, accepted_at=?, "
            "slot=COALESCE(?, slot) WHERE id=?",
            ("accepted" if ok else "failed", reason[:300], _now(),
             None if slot is None else int(slot), int(cmd_id)),
        )
        conn.commit()


def send_status(cmd_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM outbound_commands WHERE id=?", (int(cmd_id),)
        ).fetchone()
    return dict(row) if row else None


def wait_for_receipt(cmd_id: int, timeout: float = 30.0, poll: float = 0.2) -> dict[str, Any] | None:
    """等待回执。返回最终行（status ∈ accepted/failed/…）；超时返回最后读数
    （通常仍 pending/claimed —— **调用方必须按"未确认"处理，不得判成功**）。"""
    deadline = _now() + timeout
    last: dict[str, Any] | None = None
    while _now() < deadline:
        last = send_status(cmd_id)
        if last and last.get("status") in ("accepted", "failed"):
            return last
        time.sleep(poll)
    return last


def recent_sends(kind_prefix: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """出站事实历史（含回执），跨 worker 统一入口（替代 hub 代理 sent_history）。"""
    with _conn() as conn:
        if kind_prefix:
            rows = conn.execute(
                "SELECT * FROM outbound_commands WHERE kind LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (kind_prefix + "%", int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM outbound_commands ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    return [dict(r) for r in rows]


# ── control commands（master 消费的控制命令，如手动主动发送） ──


def enqueue_control(kind: str, session_key: str = "", payload: str = "") -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO control_commands (kind, session_key, payload, status, created_at) "
            "VALUES (?,?,?,?,?)",
            (kind, session_key, payload, "pending", _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def claim_next_control(kind: str) -> dict[str, Any] | None:
    with _conn() as conn:
        _reap_expired(conn, "control_commands")
        row = conn.execute(
            "SELECT id FROM control_commands WHERE status='pending' AND kind=? "
            "ORDER BY id ASC LIMIT 1",
            (kind,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        cid = int(row["id"])
        cur = conn.execute(
            "UPDATE control_commands SET status='claimed', claimed_by=?, "
            "claim_expires=?, attempts=attempts+1 WHERE id=? AND status='pending'",
            (host_id(), _now() + max(CLAIM_TTL, 120.0), cid),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        full = conn.execute("SELECT * FROM control_commands WHERE id=?", (cid,)).fetchone()
        return dict(full) if full else None


def complete_control(cmd_id: int, *, ok: bool, result: str = "", reason: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE control_commands SET status=?, result=?, fail_reason=?, accepted_at=? "
            "WHERE id=?",
            ("accepted" if ok else "failed", result[:2000], reason[:300], _now(), int(cmd_id)),
        )
        conn.commit()


def control_status(cmd_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM control_commands WHERE id=?", (int(cmd_id),)).fetchone()
    return dict(row) if row else None


def wait_for_control(
    cmd_id: int, timeout: float = 90.0, poll: float = 0.3,
) -> dict[str, Any] | None:
    deadline = _now() + timeout
    last: dict[str, Any] | None = None
    while _now() < deadline:
        last = control_status(cmd_id)
        if last and last.get("status") in ("accepted", "failed"):
            return last
        time.sleep(poll)
    return last


# ── inbound idempotency（message_id 幂等账） ──────────────────


def inbound_claim(message_id: str, session_key: str = "") -> bool:
    """处理**前**认领。True=首次（可处理）；False=已见过（跳过）。

    调用方成功后 :func:`inbound_done`，异常时 :func:`inbound_release`
    （允许合法重试 —— 处理失败不该吞掉这条消息）。
    """
    mid = str(message_id or "").strip()
    if not mid:
        return True  # 无 id 可认领：不阻断（与既有内存去重兜底共存）
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO inbound_seen (message_id, session_key, status, ts) "
            "VALUES (?,?,'claimed',?)",
            (mid, session_key, _now()),
        )
        conn.commit()
        if cur.rowcount == 1:
            return True
        row = conn.execute(
            "SELECT status, ts FROM inbound_seen WHERE message_id=?", (mid,)
        ).fetchone()
        # 上一次尝试崩在中途（claimed 未转 done/failed）——行留着 claimed
        # 且超过认领 TTL 时，允许本次接管重试。
        if row and row["status"] == "claimed" and (_now() - float(row["ts"])) > CLAIM_TTL:
            conn.execute(
                "UPDATE inbound_seen SET status='claimed', ts=? WHERE message_id=? "
                "AND status='claimed'",
                (_now(), mid),
            )
            conn.commit()
            return True
        return False


def inbound_done(message_id: str) -> None:
    mid = str(message_id or "").strip()
    if not mid:
        return
    with _conn() as conn:
        conn.execute(
            "UPDATE inbound_seen SET status='done', ts=? WHERE message_id=?",
            (_now(), mid),
        )
        conn.commit()


def inbound_release(message_id: str) -> None:
    """处理失败 → 撤销认领（重试不再被去重挡住）。"""
    mid = str(message_id or "").strip()
    if not mid:
        return
    with _conn() as conn:
        conn.execute("DELETE FROM inbound_seen WHERE message_id=? AND status='claimed'", (mid,))
        conn.commit()


def inbound_seen(message_id: str) -> bool:
    mid = str(message_id or "").strip()
    if not mid:
        return False
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM inbound_seen WHERE message_id=?", (mid,)
        ).fetchone()
    return row is not None


# ── followup budget（追问日预算，跨重启，缺陷 J） ─────────────


def followup_used(session_key: str, day: str) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT used FROM followup_budget WHERE session_key=? AND day=?",
            (session_key, day),
        ).fetchone()
    return int(row["used"]) if row else 0


def followup_spend(session_key: str, day: str) -> int:
    """预算 +1，返回新值。"""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO followup_budget (session_key, day, used, updated_at) "
            "VALUES (?,?,1,?) ON CONFLICT(session_key, day) DO UPDATE SET "
            "used=used+1, updated_at=excluded.updated_at",
            (session_key, day, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT used FROM followup_budget WHERE session_key=? AND day=?",
            (session_key, day),
        ).fetchone()
    return int(row["used"]) if row else 1


# ── effect dedup（工具副作用幂等，如 set_reminder，缺陷 I） ───


def effect_once(dedup_key: str, producer) -> tuple[bool, Any]:
    """幂等执行副作用：首次调用 ``producer()`` 并记录结果；键已存在则直接
    返回既有结果 ``(False, stored)``，**不再执行**。

    ``dedup_key`` 建议 ``f"{tool}:{session_key}:{message_id}:{args_hash}"``。
    """
    key = str(dedup_key or "").strip()
    if not key:
        return True, producer()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO effect_dedup (dedup_key, result, ts) VALUES (?,'',?)",
            (key, _now()),
        )
        conn.commit()
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT result FROM effect_dedup WHERE dedup_key=?", (key,)
            ).fetchone()
            return False, (row["result"] if row and row["result"] else None)
    # 占位已插入，执行真实副作用；失败则撤销占位（允许重试）
    try:
        value = producer()
    except Exception:
        with _conn() as conn:
            conn.execute(
                "DELETE FROM effect_dedup WHERE dedup_key=? AND result=''", (key,)
            )
            conn.commit()
        raise
    with _conn() as conn:
        conn.execute(
            "UPDATE effect_dedup SET result=? WHERE dedup_key=?", (str(value), key)
        )
        conn.commit()
    return True, value
