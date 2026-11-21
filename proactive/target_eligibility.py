"""主动线目标集合资格门（2026-11-21 主动线治理批）。

**问题**（生产审计实况）：每 5 分钟 per-user tick 的目标集合来自 ASEHub 的
``known_user_keys`` 与历史 ``ase_index.json``，其中包含已删除/从未登录的账号
与 e2e 测试键（生产取证：user 9 无任何 ``wechat_bindings``，但索引里仍有
``9:web:wbtest-1``）。这些键仍会先写 ``vital_signs_state``、再烧决策 LLM 与
post-draft judge，最后 web outbox 无人认领、45s 超时、1h 后标
``expired_unclaimed``。

**职责**：在**决策之前**给出确定性的跳过理由（空串=合格）。只读 users /
chat_history / runtime_plane 三处，不写任何业务表；异常一律 fail-open（返回
空串），资格门故障不能把主动线整体打哑。

**判定顺序**（先便宜后昂贵）：
1. 无 owner 段 / 无法解析的键 → ``invalid_session_key``；
2. 键命中 e2e/测试命名空间 → ``test_namespace``（不需要读 users 库）；
3. users 行不存在或 ``last_login_at`` 为空 → ``never_logged_in``；
   账号名/邮箱命中测试命名空间 → ``test_namespace``；
4. 微信键：有活跃 ``wechat_bindings`` → 合格；否则 → ``wechat_unbound``
   （2026-11-21 用户裁决「无绑定即跳过」——stale peer 不再烧决策 LLM）；
5. web 键：有在线租约或 30 分钟内入站 → 合格；否则无绑定 → ``web_unbound_idle``，
   有绑定 → ``web_session_idle``。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import session_key as session_key_mod
from utils.local_time import now_utc_naive
from utils.project_paths import project_path, resolve_project_path

logger = logging.getLogger("proactive.target_eligibility")

#: 测试命名空间前缀（大小写不敏感；必须带分隔符或到串尾，避免 testimony 误杀）。
_TEST_PREFIX_RE = re.compile(
    r"^(?:wbtest|e2etest|e2e|test|qa|smoke)(?=$|[-_#.])", re.IGNORECASE
)

#: web 会话「近期入站」窗口（分钟）。超过该窗口且无在线租约 = 死会话。
WEB_RECENT_INBOUND_MINUTES = 30.0

#: 账号查询缓存（秒）。users 行极少变，避免每 tick 每键查库。
_CACHE_TTL_SECONDS = 30.0

_cache_lock = threading.Lock()
_account_cache: dict[tuple[str, int], tuple[float, AccountInfo | None]] = {}


@dataclass(frozen=True)
class AccountInfo:
    """users.db 中与资格门相关的最小投影。"""

    exists: bool
    last_login_at: str = ""
    has_binding: bool = False
    username: str = ""
    email: str = ""

    @property
    def is_test_account(self) -> bool:
        return is_test_account(self.username, self.email)


def clear_cache() -> None:
    """清空账号缓存（测试隔离 / 账号删除后立即生效）。"""
    with _cache_lock:
        _account_cache.clear()


def is_test_namespace(value: str) -> bool:
    """会话键 / 用户名 / 邮箱是否命中 e2e 测试命名空间。

    约定前缀与既有 ``wbtest`` / ``TEST`` 名单归一：大小写不敏感，前缀后必须是
    ``- _ # .`` 之一或串尾（``wbtest-1`` / ``TEST_abc`` / ``e2e`` 均命中）。
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    candidates: set[str] = {raw}
    try:
        parsed = session_key_mod.parse(raw)
        candidates.add(parsed.peer)
        candidates.update(part for part in parsed.peer.split(":") if part)
    except Exception:  # noqa: BLE001 —— 解析失败只按原串判断
        pass
    candidates.update(part for part in raw.split(":") if part)
    return any(
        _TEST_PREFIX_RE.match(str(candidate).strip()) for candidate in candidates
    )


def is_test_account(username: str, email: str = "") -> bool:
    """账号名/邮箱是否属于测试命名空间（含 ``@test.local`` 域）。"""
    user = str(username or "").strip()
    mail = str(email or "").strip().lower()
    if user and _TEST_PREFIX_RE.match(user):
        return True
    if "@" in mail:
        local, _, domain = mail.partition("@")
        if local and _TEST_PREFIX_RE.match(local):
            return True
        if domain in {"test.local", "example.test", "test.invalid"}:
            return True
        if domain.startswith(("test.", "e2e.", "qa.")):
            return True
    return False


def _sqlite_path_from_url(url: str) -> Path | None:
    """从 SQLAlchemy URL 取 SQLite 文件路径；非 SQLite 返回 None。"""
    raw = str(url or "").strip()
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if raw.startswith(prefix):
            path = raw[len(prefix):].split("?", 1)[0]
            if path.startswith("/") and not Path(path).exists() and ":" in path[:3]:
                # Windows 盘符：/D:/... → D:/...
                path = path.lstrip("/")
            return resolve_project_path(path)
    return None


def default_users_db() -> Path | None:
    """users.db 真源：APP_DATABASE_URL > DATABASE_URL > data/users.db。"""
    url = os.environ.get("APP_DATABASE_URL", "").strip() or os.environ.get(
        "DATABASE_URL", ""
    ).strip()
    if url:
        return _sqlite_path_from_url(url)
    return project_path("data", "users.db")


def default_memory_db() -> Path | None:
    """chat_history 所在库（与 StructuredMemory 默认库同源）。"""
    try:
        from shisi.memory.legacy import structured_memory as sm_mod

        return Path(sm_mod.default_db_path())
    except Exception:  # noqa: BLE001
        return project_path("data", "sqlite.db")


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _read_account(uid: int, users_db: Path | None) -> AccountInfo | None:
    """读 users 行 + 绑定数；库不可用返回 None（调用方 fail-open）。"""
    path = users_db if users_db is not None else default_users_db()
    if path is None:
        return None
    cache_key = (str(path), int(uid))
    now = time.time()
    with _cache_lock:
        cached = _account_cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    conn = _connect_ro(path)
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT id, username, email, last_login_at FROM users WHERE id=?",
            (int(uid),),
        ).fetchone()
        if row is None:
            info = AccountInfo(exists=False)
        else:
            try:
                binding = conn.execute(
                    "SELECT COUNT(*) FROM wechat_bindings WHERE user_id=?",
                    (int(uid),),
                ).fetchone()
                has_binding = bool(binding and int(binding[0] or 0) > 0)
            except sqlite3.Error:
                has_binding = False
            info = AccountInfo(
                exists=True,
                last_login_at=str(row["last_login_at"] or ""),
                has_binding=has_binding,
                username=str(row["username"] or ""),
                email=str(row["email"] or ""),
            )
    except sqlite3.Error as exc:
        logger.debug("资格门读 users 失败（fail-open）: %s", exc)
        return None
    finally:
        conn.close()
    with _cache_lock:
        _account_cache[cache_key] = (now, info)
    return info


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _recent_inbound(
    session_key: str, memory_db: Path | None, now: datetime, minutes: float,
) -> bool:
    path = memory_db if memory_db is not None else default_memory_db()
    if path is None:
        return False
    conn = _connect_ro(path)
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT MAX(created_at) FROM chat_history "
            "WHERE session_id=? AND role='user'",
            (str(session_key),),
        ).fetchone()
        latest = _parse_dt(row[0] if row else "")
    except sqlite3.Error as exc:
        logger.debug("资格门读 chat_history 失败（按无入站处理）: %s", exc)
        return False
    finally:
        conn.close()
    if latest is None:
        return False
    delta = (now - latest).total_seconds()
    # 允许 5 分钟时钟偏移；未来时间同样视为近期（不误杀）
    return -300.0 <= delta <= float(minutes) * 60.0


def _web_lease_live(session_key: str) -> bool:
    try:
        from proactive import runtime_plane

        return bool(runtime_plane.session_live(session_key))
    except Exception:  # noqa: BLE001
        return False


def skip_reason(
    session_key: str,
    *,
    users_db: Path | None = None,
    memory_db: Path | None = None,
    now: datetime | None = None,
    recent_minutes: float = WEB_RECENT_INBOUND_MINUTES,
) -> str:
    """返回空串=合格；非空=确定性跳过理由（供 skip 账本 reason 使用）。"""
    key = str(session_key or "").strip()
    if not key:
        return "invalid_session_key"
    uid = session_key_mod.owner_of(key)
    if uid is None:
        return "invalid_session_key"
    # 纯键面命名空间检查不依赖 users 库（最便宜，且测试键不必读账号）
    if is_test_namespace(key):
        return "test_namespace"

    account = _read_account(int(uid), users_db)
    if account is None:
        # users 库不可用/非 SQLite：宁宽勿误，不因资格门故障停掉主动线
        return ""
    if not account.exists or not account.last_login_at:
        return "never_logged_in"
    if account.is_test_account:
        return "test_namespace"

    if session_key_mod.is_wechat_key(key):
        # 2026-11-21 治理批：微信键必须核验活跃 wechat_bindings（用户裁决
        # 「无绑定即跳过」）。旧实现只要账号存在且登录过就对任意 is_wechat_key
        # 直接放行——绑定已解/从未绑定的 stale peer（验收实测
        # skip_reason('7:wxid_nonexistent@im.wechat') 返回空串）仍会烧决策
        # LLM 与 judge。判定保持在 web 租约闸之前（微信键不看 web 租约/入站）。
        if not account.has_binding:
            return "wechat_unbound"
        return ""

    moment = now if now is not None else now_utc_naive()
    if getattr(moment, "tzinfo", None) is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    live = _web_lease_live(key)
    recent = _recent_inbound(key, memory_db, moment, recent_minutes)
    if live or recent:
        return ""
    if not account.has_binding:
        return "web_unbound_idle"
    return "web_session_idle"
