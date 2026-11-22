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

**判定顺序**（先便宜后昂贵；2026-11-22 M12 拆强弱标记后重排）：
1. 无 owner 段 / 无法解析的键 → ``invalid_session_key``；
2. 键命中 e2e/测试命名空间（强标记或弱前缀）→ ``test_namespace``
   （不需要读 users 库；键是系统分配的命名空间，弱前缀键同样按测试键）；
3. users 行不存在或 ``last_login_at`` 为空 → ``never_logged_in``；
   账号名/邮箱命中**强标记**（wbtest/e2etest/smoke、保留测试域）→
   ``test_namespace``（确定性，弱前缀不放这里——见 5）；
4. 微信键：有活跃 ``wechat_bindings`` → 合格（弱前缀账号有绑定同样放行）；
   否则 → ``wechat_unbound``
   （2026-11-21 用户裁决「无绑定即跳过」——stale peer 不再烧决策 LLM）；
5. web 键：有在线租约或 30 分钟内入站 → 合格（**活跃豁免**：弱前缀真实
   账号不得单凭用户名/邮箱判死）；不活跃且账号名/邮箱命中**弱前缀**
   （test/qa/e2e + 分隔符、``test.`` 等子域）→ ``test_namespace``；
   否则无绑定 → ``web_unbound_idle``，有绑定 → ``web_session_idle``。
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

#: 测试命名空间**强标记**前缀（大小写不敏感）：wbtest/e2etest/smoke 后接
#: 分隔符、**纯数字**或串尾均确定性命中。生产病灶 ``wbtest1027``（前缀后
#: 直接跟数字）在旧正则（只认分隔符/串尾）下漏判，生产清理窗口只能靠显式
#: ``--user-id`` 兜底。真实英文词不含这些前缀，无负向保护顾虑。
_STRONG_TEST_PREFIX_RE = re.compile(
    r"^(?:wbtest|e2etest|smoke)(?=$|[-_#.]|\d)", re.IGNORECASE
)

#: 测试命名空间**弱前缀**（大小写不敏感）：test/qa/e2e 必须带分隔符或到串尾
#: （``test_user`` / ``qa-1`` / ``e2e`` 命中；``testimony`` / ``quality`` 等
#: 真实词不误杀）。弱前缀**单独不足以判死账号**——真实用户名可能就叫
#: ``test_user``、企业邮箱也可能落在 ``test.`` 子域，账号级判定必须结合
#: 活跃证据（见 ``skip_reason`` 判定序，2026-11-22 M12）。
_WEAK_TEST_PREFIX_RE = re.compile(
    r"^(?:test|qa|e2e)(?=$|[-_#.])", re.IGNORECASE
)

#: 保留测试域（IANA 保留 TLD / 约定测试域，不可能属真实用户）→ 强标记。
_RESERVED_TEST_DOMAINS = frozenset({"test.local", "example.test", "test.invalid"})

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
    def has_strong_test_marker(self) -> bool:
        """强标记（wbtest/e2etest/smoke、保留测试域）→ 确定性测试账号。"""
        user = str(self.username or "").strip()
        strong_mail, _ = _email_test_marker(self.email)
        return bool(user and _STRONG_TEST_PREFIX_RE.match(user)) or strong_mail

    @property
    def has_weak_test_marker(self) -> bool:
        """弱前缀（test/qa/e2e 用户名、test. 等邮箱子域）命中。

        是否据此跳过由 ``skip_reason`` 结合活跃证据裁决（M12：活跃真实账号
        不得单凭名字判死）。
        """
        user = str(self.username or "").strip()
        _, weak_mail = _email_test_marker(self.email)
        return bool(user and _WEAK_TEST_PREFIX_RE.match(user)) or weak_mail

    @property
    def is_test_account(self) -> bool:
        """旧口径兼容面（强 ∪ 弱，不含活跃证据）。

        ⚠️ 资格门判定序请用上面两个细分属性；本属性供
        ``scripts/purge_e2e_accounts.py`` 等清理面沿用「宁可多清」口径。
        """
        return self.has_strong_test_marker or self.has_weak_test_marker


def clear_cache() -> None:
    """清空账号缓存（测试隔离 / 账号删除后立即生效）。"""
    with _cache_lock:
        _account_cache.clear()


def _namespace_candidates(raw: str) -> set[str]:
    """键/名的候选判定面：原串 + 会话键 peer 段 + 冒号分段（均去空白）。"""
    candidates: set[str] = {raw}
    try:
        parsed = session_key_mod.parse(raw)
        candidates.add(parsed.peer)
        candidates.update(part for part in parsed.peer.split(":") if part)
    except Exception:  # noqa: BLE001 —— 解析失败只按原串判断
        pass
    candidates.update(part for part in raw.split(":") if part)
    return {str(c).strip() for c in candidates if str(c).strip()}


def is_strong_test_marker(value: str) -> bool:
    """强标记：wbtest/e2etest/smoke 前缀（后接分隔符/纯数字/串尾）。

    确定性命中，不依赖活跃证据（M2：``wbtest1027`` 这类「前缀+纯数字」
    形态必须直接命中）。
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    return any(
        _STRONG_TEST_PREFIX_RE.match(c) for c in _namespace_candidates(raw)
    )


def is_weak_test_marker(value: str) -> bool:
    """弱前缀：test/qa/e2e + 分隔符/串尾。

    命中只代表「疑似测试命名」，是否据此跳过由调用方结合活跃证据裁决
    （M12：有绑定/租约/入站的真实账号不得单凭名字判死）。
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    return any(
        _WEAK_TEST_PREFIX_RE.match(c) for c in _namespace_candidates(raw)
    )


def is_test_namespace(value: str) -> bool:
    """会话键 / 用户名 / 邮箱是否命中 e2e 测试命名空间（强标记 ∪ 弱前缀）。

    约定前缀与既有 ``wbtest`` / ``TEST`` 名单归一：大小写不敏感。强标记后接
    分隔符/数字/串尾均命中（``wbtest-1`` / ``wbtest1027`` / ``smoke2``）；
    弱前缀必须带分隔符或到串尾（``TEST_abc`` / ``e2e`` 命中，
    ``testimony`` 不误杀）。**键面**判定用于系统生成的会话键，命中即测试键；
    **账号级**（用户名/邮箱）弱前缀命中须再结合活跃证据（见 ``skip_reason``）。
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    candidates = _namespace_candidates(raw)
    return any(
        _STRONG_TEST_PREFIX_RE.match(c) or _WEAK_TEST_PREFIX_RE.match(c)
        for c in candidates
    )


def _email_test_marker(email: str) -> tuple[bool, bool]:
    """邮箱的测试标记判定，返回 ``(strong, weak)``。

    local 部分按前缀规则；域名命中保留测试域 → 强；``test.`` / ``e2e.`` /
    ``qa.`` 子域前缀 → 弱（真实邮箱也可能落在这类子域，M12 降为弱标记）。
    """
    mail = str(email or "").strip().lower()
    if "@" not in mail:
        return False, False
    local, _, domain = mail.partition("@")
    strong = bool(local and _STRONG_TEST_PREFIX_RE.match(local)) or (
        domain in _RESERVED_TEST_DOMAINS
    )
    weak = bool(local and _WEAK_TEST_PREFIX_RE.match(local)) or domain.startswith(
        ("test.", "e2e.", "qa.")
    )
    return strong, weak


def is_test_account(username: str, email: str = "") -> bool:
    """账号名/邮箱是否属于测试命名空间（强 ∪ 弱，不含活跃证据）。

    ⚠️ 资格门判定序不要直接用本函数判死弱前缀账号（见 ``skip_reason``）；
    ``scripts/purge_e2e_accounts.py`` 等清理面沿用本口径（宁可多清）。
    """
    user = str(username or "").strip()
    strong_mail, weak_mail = _email_test_marker(email)
    if strong_mail or weak_mail:
        return True
    return bool(
        user
        and (
            _STRONG_TEST_PREFIX_RE.match(user) or _WEAK_TEST_PREFIX_RE.match(user)
        )
    )


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
    # M12（2026-11-22）：账号级判定拆强弱、重排判定序。强标记（wbtest/
    # e2etest/smoke、保留测试域）维持确定性命中；弱前缀（test_/qa_/e2e
    # 用户名、test. 等邮箱子域）**不再单凭名字判死**——旧判定序把
    # ``is_test_account`` 放在 web 租约/入站检查之前，纯 web 活跃的真实
    # 账号（用户名恰为 test_* 或邮箱挂 test. 子域）被整类静默跳过主动线
    # （skip 账本每日仅一条，难察觉）。取舍：弱前缀账号多做一次租约/入站
    # 读库，换取不误杀真实用户；强标记仍前置零读库判死。
    if account.has_strong_test_marker:
        return "test_namespace"

    if session_key_mod.is_wechat_key(key):
        # 2026-11-21 治理批：微信键必须核验活跃 wechat_bindings（用户裁决
        # 「无绑定即跳过」）。旧实现只要账号存在且登录过就对任意 is_wechat_key
        # 直接放行——绑定已解/从未绑定的 stale peer（验收实测
        # skip_reason('7:wxid_nonexistent@im.wechat') 返回空串）仍会烧决策
        # LLM 与 judge。判定保持在 web 租约闸之前（微信键不看 web 租约/入站）。
        # 微信键的活跃证据就是绑定本身：弱前缀账号有绑定同样放行（M12）。
        if not account.has_binding:
            return "wechat_unbound"
        return ""

    moment = now if now is not None else now_utc_naive()
    if getattr(moment, "tzinfo", None) is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    live = _web_lease_live(key)
    recent = _recent_inbound(key, memory_db, moment, recent_minutes)
    if live or recent:
        # 活跃豁免（M12）：有租约/入站的弱前缀账号是真实用户，放行。
        return ""
    if account.has_weak_test_marker:
        # 不活跃的弱前缀账号：按测试命名空间跳过（不烧决策 LLM）。
        return "test_namespace"
    if not account.has_binding:
        return "web_unbound_idle"
    return "web_session_idle"
