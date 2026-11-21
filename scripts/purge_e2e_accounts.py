#!/usr/bin/env python
"""e2e / 测试账号跨库清理（2026-11-21 主动线治理批）。

**背景**：生产/本地长期累积的 e2e、wbtest、TEST 账号没有命名空间约束，
历史 ASE 索引键会让调度器每 5 分钟为死会话烧决策 LLM；``api/lifecycle.py``
的删号链路在历史上也漏了 runtime_plane / 用户×角色快照 / 阶段 / 生理表。
本脚本是这些测试账号的**一次性清理入口**，不替代正常账号注销 API。

用法（默认 dry-run，只列清单零删除）::

    python scripts/purge_e2e_accounts.py --dry-run
    python scripts/purge_e2e_accounts.py --apply \
        --users-db data/users.db --sqlite-db data/sqlite.db \
        --agent-db data/agent_plane.db --runtime-db data/runtime_plane.db

安全纪律：
- **必须在服务冷停后执行**：``--apply`` 会先读 ``runtime_plane`` 的
  ``channel_presence`` 租约做启动检测，发现目标账号仍有活着的连接器宿主
  （或 presence 不可读、无法确认冷停）即拒绝执行——脚本进程停不掉其他
  worker 的连接器，带连接器运行时删除只会让 ``restore_on_boot`` 按磁盘
  凭证把已删账号的通道复活；
- ``--apply`` 前先对 4 个库做 SQLite online backup 到 ``data/archive/``，
  逐库 ``PRAGMA integrity_check``；同时备份会被改写的
  ``scheduler_config.json``（throttle 账本回滚依据）；
- 每个库单事务删除；任何异常回滚并把错误抛给调用方；
- 删除后重新扫描全部表，残留 >0 或**任何计数读失败**（-1）都以退出码 1
  报错（fail-closed：读不出残留 ≠ 零残留）；
- 幂等：二次执行目标集合为空，退出码 0；
- 产出 ``DELETION_LOG_ENTRY.md``（不含聊天正文/密钥）；
- ``--apply`` 的节流清除复用 ``api.lifecycle._purge_throttle_file``，
  需与服务一致的运行环境（``JWT_SECRET`` / 显式 ``AI_GF_ENV=dev``）；
  ``--dry-run`` 无此依赖。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sqlite3
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from proactive import target_eligibility  # noqa: E402
from utils.project_paths import project_path  # noqa: E402

logger = logging.getLogger("purge_e2e_accounts")


# ── 表清单（4 库）────────────────────────────────────────────────────────
#: users.db：账号归属行。**不得包含 `operator_audit`**：审计生命周期长于账号，
#: 删除账号必须保留审计链（见 `api/database.py::OperatorAudit` 的模型注释）。
USERS_TABLES: tuple[tuple[str, str], ...] = (
    ("user_sessions", "user_id"),
    ("consent_records", "user_id"),
    ("wechat_bindings", "user_id"),
    ("wechat_channel_sessions", "user_id"),
    ("wechat_peer_preferences", "owner_user_id"),
    ("user_active_characters", "user_id"),
    ("announcement_reads", "user_id"),
    ("operator_prefs", "user_id"),
    ("character_achievements", "character_id"),
)

#: users.db 中同时按 ``uid`` / ``uid:%`` 两种形态匹配的列：
#: 角色成就的 character_id 可能是裸卡 id 或 ``uid:card`` 形态。
USERS_PREFIX_COLUMNS: frozenset[str] = frozenset({"character_achievements"})

#: sqlite.db：会话键列（按 ``N:%`` 前缀）
SQLITE_SESSION_TABLES: tuple[tuple[str, str], ...] = (
    ("chat_history", "session_id"),
    ("reflections", "session_id"),
    ("reminders", "session_key"),
    ("pending_intents", "session_key"),
    ("memory_extraction_progress", "session_id"),
    ("user_facts", "user_key"),
    ("fact_deletion_watermarks", "user_key"),
    ("user_identity_map", "user_key"),
    ("user_profile", "user_key"),
    ("user_proactive_config", "user_key"),
    ("user_humanize", "user_key"),
    ("user_reply_mode", "user_key"),
    ("topic_state", "session_key"),
    ("thread_plan", "session_key"),
    ("topic_meta", "session_key"),
    ("user_character_settings", "user_key"),
)

#: sqlite.db：隔离键列（``N:%``；含 ``N:session::character`` 形态）
SQLITE_ISOLATION_TABLES: tuple[tuple[str, str], ...] = (
    ("emotion_stage_state", "character_id"),
    ("vital_signs_state", "character_id"),
    ("affinity_records", "character_id"),
    ("affinity_audit", "character_id"),
    ("affinity_unlocks", "character_id"),
)

#: sqlite.db：数字 user_id 直列
SQLITE_UID_TABLES: tuple[tuple[str, str], ...] = (
    ("user_character_overrides", "user_id"),
    ("user_persona", "user_id"),
    ("user_persona_snapshots", "user_id"),
)

#: sqlite.db：agent_core 影子账本（user_key + owner_user_id 双路）
AGENT_CORE_TABLES: tuple[str, ...] = (
    "agent_events", "agent_threads", "agent_commitments",
)


def _connect(path: Path, *, write: bool = False) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        if write:
            raise
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _count_where(
    conn: sqlite3.Connection, table: str, where: str, params: Iterable[Any],
) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {where}", tuple(params)
    ).fetchone()
    return int(row[0] or 0) if row else 0


# ── 目标发现 ────────────────────────────────────────────────────────────


def discover_targets(
    users_db: Path, explicit_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """返回测试命名空间账号 + 显式 ``--user-id``（去重）。"""
    targets: dict[int, dict[str, Any]] = {
        int(uid): {"id": int(uid), "username": "", "email": "", "explicit": True}
        for uid in (explicit_ids or [])
    }
    conn = _connect(users_db)
    if conn is None:
        if users_db.exists():
            # 库存在但读不了（被锁/损坏）：静默返回空清单会让 --apply 在
            # 目标未知的情況下继续走，属危险失败模式，必须 fail-closed。
            raise RuntimeError(f"users.db 不可读，拒绝清理: {users_db}")
        return []
    try:
        if _table_exists(conn, "users"):
            for row in conn.execute(
                "SELECT id, username, email FROM users ORDER BY id"
            ):
                uid = int(row["id"])
                username = str(row["username"] or "")
                email = str(row["email"] or "")
                if target_eligibility.is_test_account(username, email):
                    targets[uid] = {
                        "id": uid, "username": username, "email": email,
                        "explicit": uid in (explicit_ids or []),
                    }
    finally:
        conn.close()
    return [targets[k] for k in sorted(targets)]


def collect_session_keys(
    target: dict[str, Any], users_db: Path, sqlite_db: Path, runtime_db: Path,
    extra_keys: list[str] | None = None,
) -> list[str]:
    """收集该账号已知会话键（绑定 + 历史表 + 控制面），用于精确清理与验证。"""
    uid = int(target["id"])
    keys: set[str] = {str(k).strip() for k in (extra_keys or []) if str(k).strip()}
    conn = _connect(users_db)
    if conn is not None:
        try:
            if _table_exists(conn, "wechat_bindings"):
                for row in conn.execute(
                    "SELECT wxid FROM wechat_bindings WHERE user_id=?", (uid,)
                ):
                    peer = str(row["wxid"] or "").strip()
                    if peer:
                        keys.add(f"{uid}:{peer}")
            if _table_exists(conn, "wechat_peer_preferences"):
                for row in conn.execute(
                    "SELECT peer_wxid FROM wechat_peer_preferences "
                    "WHERE owner_user_id=?", (uid,)
                ):
                    peer = str(row["peer_wxid"] or "").strip()
                    if peer:
                        keys.add(f"{uid}:{peer}")
        finally:
            conn.close()

    conn = _connect(sqlite_db)
    if conn is not None:
        try:
            if _table_exists(conn, "chat_history"):
                for row in conn.execute(
                    "SELECT DISTINCT session_id FROM chat_history "
                    "WHERE session_id LIKE ?", (f"{uid}:%",),
                ):
                    if str(row["session_id"] or "").strip():
                        keys.add(str(row["session_id"]))
            for table, column in (
                ("user_character_settings", "user_key"),
                ("user_profile", "user_key"),
                ("topic_state", "session_key"),
            ):
                if _table_exists(conn, table):
                    for row in conn.execute(
                        f"SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE ?",
                        (f"{uid}:%",),
                    ):
                        if str(row[column] or "").strip():
                            keys.add(str(row[column]))
        finally:
            conn.close()

    conn = _connect(runtime_db)
    if conn is not None:
        try:
            if _table_exists(conn, "outbound_commands"):
                for row in conn.execute(
                    "SELECT DISTINCT session_key FROM outbound_commands "
                    "WHERE owner_id=? OR session_key LIKE ?", (uid, f"{uid}:%"),
                ):
                    if str(row["session_key"] or "").strip():
                        keys.add(str(row["session_key"]))
        finally:
            conn.close()
    return sorted(keys)


def collect_peers(target: dict[str, Any], users_db: Path) -> list[str]:
    """收集裸 peer 标识（ASE 索引/状态可能以裸 wxid 为键）。"""
    uid = int(target["id"])
    peers: set[str] = set()
    conn = _connect(users_db)
    if conn is None:
        return []
    try:
        if _table_exists(conn, "wechat_bindings"):
            for row in conn.execute(
                "SELECT wxid FROM wechat_bindings WHERE user_id=?", (uid,)
            ):
                peer = str(row["wxid"] or "").strip()
                if peer:
                    peers.add(peer)
        if _table_exists(conn, "wechat_peer_preferences"):
            for row in conn.execute(
                "SELECT peer_wxid FROM wechat_peer_preferences WHERE owner_user_id=?",
                (uid,),
            ):
                peer = str(row["peer_wxid"] or "").strip()
                if peer:
                    peers.add(peer)
    finally:
        conn.close()
    return sorted(peers)


# ── 清单 / 验证 ────────────────────────────────────────────────────────


def _target_counts(
    conn: sqlite3.Connection | None, target: dict[str, Any], keys: list[str],
) -> dict[str, int]:
    """单库受影响表计数（不含聊天正文）。"""
    if conn is None:
        return {}
    uid = int(target["id"])
    prefix = f"{uid}:%"
    out: dict[str, int] = {}

    for table, column in USERS_TABLES:
        if table in USERS_PREFIX_COLUMNS:
            out[table] = _count_where(
                conn, table, f"({column}=? OR {column} LIKE ?)", (uid, prefix)
            )
        else:
            out[table] = _count_where(conn, table, f"{column}=?", (uid,))

    for table, column in SQLITE_SESSION_TABLES:
        out[table] = _count_where(conn, table, f"{column} LIKE ?", (prefix,))
    for table, column in SQLITE_ISOLATION_TABLES:
        out[table] = _count_where(conn, table, f"{column} LIKE ?", (prefix,))
    for table, column in SQLITE_UID_TABLES:
        out[table] = _count_where(conn, table, f"{column}=?", (uid,))

    if keys:
        ph = ",".join("?" for _ in keys)
        for table, column in (
            ("user_character_settings", "user_key"),
            ("emotion_stage_state", "character_id"),
            ("vital_signs_state", "character_id"),
        ):
            if _table_exists(conn, table):
                out[table] = max(
                    out.get(table, 0),
                    _count_where(conn, table, f"{column} IN ({ph})", keys),
                )
        for table in AGENT_CORE_TABLES:
            if _table_exists(conn, table):
                cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
                clauses = [f"user_key LIKE ?"]
                params: list[Any] = [prefix]
                if "owner_user_id" in cols:
                    clauses.append("owner_user_id=?")
                    params.append(uid)
                out[table] = _count_where(
                    conn, table, " OR ".join(clauses), params
                )
        if _table_exists(conn, "daily_summaries"):
            clauses = " OR ".join("date LIKE ?" for _ in keys)
            out["daily_summaries"] = _count_where(
                conn, "daily_summaries", clauses,
                [f"{key}|%" for key in keys],
            )
        if _table_exists(conn, "memory_recycle_bin"):
            clauses = " OR ".join("character_id LIKE ?" for _ in keys)
            out["memory_recycle_bin"] = _count_where(
                conn, "memory_recycle_bin", clauses,
                [f"user_fact:{key}%" for key in keys],
            )
    return out


def inventory(
    target: dict[str, Any], keys: list[str],
    users_db: Path, sqlite_db: Path, agent_db: Path, runtime_db: Path,
    characters_dir: Path | None = None, knowledge_dir: Path | None = None,
) -> dict[str, dict[str, int]]:
    """返回 {库: {该库实际存在的受影响表: 行数}}（0 行也保留，便于审计）。

    fail-closed：库文件存在但连接/查询失败时记 ``_read_error: -1``，
    绝不静默跳过——验证阶段读不出残留必须按残留处理。
    """
    result: dict[str, dict[str, int]] = {}
    uid = int(target["id"])

    users_tables = {table for table, _ in USERS_TABLES}
    conn = _connect(users_db)
    if conn is None:
        if users_db.exists():
            result["users.db"] = {"_read_error": -1}
    else:
        try:
            all_counts = _target_counts(conn, target, keys)
            result["users.db"] = {
                table: all_counts.get(table, 0)
                for table in users_tables if _table_exists(conn, table)
            }
            if _table_exists(conn, "users"):
                result["users.db"]["users"] = _count_where(
                    conn, "users", "id=?", (uid,)
                )
        except sqlite3.Error as e:
            logger.warning("users.db 残留计数失败: %s", e)
            result["users.db"] = {"_read_error": -1}
        finally:
            conn.close()

    sqlite_tables = {
        table for table, _ in SQLITE_SESSION_TABLES
    } | {
        table for table, _ in SQLITE_ISOLATION_TABLES
    } | {
        table for table, _ in SQLITE_UID_TABLES
    } | set(AGENT_CORE_TABLES) | {"daily_summaries", "memory_recycle_bin"}
    conn = _connect(sqlite_db)
    if conn is None:
        if sqlite_db.exists():
            result["sqlite.db"] = {"_read_error": -1}
    else:
        try:
            all_counts = _target_counts(conn, target, keys)
            result["sqlite.db"] = {
                table: all_counts.get(table, 0)
                for table in sqlite_tables if _table_exists(conn, table)
            }
        except sqlite3.Error as e:
            logger.warning("sqlite.db 残留计数失败: %s", e)
            result["sqlite.db"] = {"_read_error": -1}
        finally:
            conn.close()

    conn = _connect(agent_db)
    if conn is None:
        if agent_db.exists():
            result["agent_plane.db"] = {"_read_error": -1}
    else:
        try:
            if _table_exists(conn, "event_ledger"):
                # channel_state 事件的键形是 `channel:<uid>:<slot>`，不能只按
                # `uid:%` 前缀计数，否则删号后残留且零残留扫描看不见。
                result["agent_plane.db"] = {
                    "event_ledger": _count_where(
                        conn, "event_ledger",
                        "(session_key LIKE ? OR session_key LIKE ?)",
                        (f"{uid}:%", f"channel:{uid}:%"),
                    )
                }
        except sqlite3.Error as e:
            logger.warning("agent_plane.db 残留计数失败: %s", e)
            result["agent_plane.db"] = {"_read_error": -1}
        finally:
            conn.close()

    # runtime_plane 用唯一 owner 计数（表多且键形复杂）
    try:
        from proactive import runtime_plane

        result["runtime_plane.db"] = {
            "rows": runtime_plane.count_owner(uid, keys)
        }
    except Exception:  # noqa: BLE001
        result["runtime_plane.db"] = {"rows": -1}

    # 私有角色实例（config/characters 卡文件 + 知识 + 精确 cid 成就行）
    result["character_instances"] = _character_instances_inventory(
        uid,
        characters_dir or project_path("config", "characters"),
        knowledge_dir or project_path("data", "knowledge"),
        users_db,
    )
    return result


def _ase_owner_keys(uid: int, keys: list[str], peers: list[str]) -> list[str]:
    """按 owner 枚举 ASE 索引键：索引里可能留有 DB 已无行引用的孤儿键。"""
    from proactive import ase_hub

    owned = {str(k).strip() for k in [*keys, *peers, str(uid)] if str(k).strip()}
    try:
        raw = ase_hub._read_index_raw()  # noqa: SLF001 —— 只读枚举，删除走公开 API
    except Exception:  # noqa: BLE001
        return sorted(owned)
    for key in raw:
        key = str(key).strip()
        if not key:
            continue
        left, sep, _rest = key.partition(":")
        if sep and left.isdigit() and int(left) == uid:
            owned.add(key)
    return sorted(owned)


def _ase_inventory(uid: int, keys: list[str], peers: list[str]) -> dict[str, int]:
    """ASE 持久化状态/索引残留计数（`data/ase_states/index.json`）。"""
    from proactive import ase_hub

    try:
        return {
            "index_rows": int(
                ase_hub.count_user_states(_ase_owner_keys(uid, keys, peers))
            )
        }
    except Exception:  # noqa: BLE001
        return {"index_rows": -1}


def _wechat_inventory(uid: int) -> dict[str, int]:
    """微信通道残留：进程内连接器 + `data/wechat_sessions/<uid>` 磁盘目录。"""
    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import get_registry

    try:
        connectors = len(get_registry().list_for_user(uid))
    except Exception:  # noqa: BLE001
        connectors = -1
    return {
        "connectors": connectors,
        "disk_dirs": int((channel_paths.sessions_root() / str(uid)).exists()),
    }


def _throttle_count(scheduler_config: Path, uid: int, keys: list[str]) -> int:
    """节流账本残留计数（与生命周期 verify 同口径）。"""
    explicit = {str(k) for k in keys} | {str(uid)}

    def _owned(key: str) -> bool:
        if key in explicit:
            return True
        left, sep, _ = str(key).partition(":")
        return bool(sep) and left.isdigit() and int(left) == uid

    if not scheduler_config.exists():
        return 0
    try:
        data = json.loads(scheduler_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 读失败不等于零残留：返回 -1 让验证侧按非零处理。
        return -1
    throttle = data.get("throttle") or {}
    n = 0
    for section in (
        "llm_proactive_next_ok", "deliver_fail_counts", "deliver_fail_counts_ts",
        "disabled_event_day", "wait_window_event_day", "quiet_event_day",
        "model_degraded_event_day", "deliver_circuit_open",
    ):
        seg = throttle.get(section)
        if isinstance(seg, dict):
            n += sum(1 for k in seg if _owned(str(k)))
    gate = throttle.get("gate_skip_event_day")
    if isinstance(gate, dict):
        n += sum(1 for k in gate if _owned(str(k).rpartition("|")[0]))
    sent = throttle.get("important_dates_sent")
    if isinstance(sent, list):
        n += sum(
            1 for x in sent
            if len(str(x).split("|")) >= 2 and _owned(str(x).split("|")[1])
        )
    return n


# ── 备份 ────────────────────────────────────────────────────────────────


def backup_databases(paths: list[Path], archive_dir: Path) -> dict[str, str]:
    """SQLite online backup + integrity_check；返回 {源路径: 备份路径}。"""
    archive_dir.mkdir(parents=True, exist_ok=True)
    backups: dict[str, str] = {}
    for src in paths:
        if not src.exists():
            continue
        dest = archive_dir / src.name
        src_conn = sqlite3.connect(str(src), timeout=15)
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
            status = str(
                dest_conn.execute("PRAGMA integrity_check").fetchone()[0]
            )
            if status != "ok":
                raise RuntimeError(f"备份 integrity_check 失败: {src} -> {status}")
        finally:
            dest_conn.close()
            src_conn.close()
        backups[str(src)] = str(dest)
    return backups


def backup_json_config(config_path: Path, archive_dir: Path) -> str | None:
    """备份会被 `_purge_throttle` 改写的 JSON 配置文件（回滚完整性所需）。"""
    if not config_path.exists():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / config_path.name
    shutil.copy2(config_path, dest)
    return str(dest)


# ── 删除 ────────────────────────────────────────────────────────────────


def _delete_users_db(
    conn: sqlite3.Connection, target: dict[str, Any], keys: list[str],
) -> dict[str, int]:
    uid = int(target["id"])
    counts: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, column in USERS_TABLES:
            if not _table_exists(conn, table):
                continue
            if table in USERS_PREFIX_COLUMNS:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE ({column}=? OR {column} LIKE ?)",
                    (uid, f"{uid}:%"),
                )
            else:
                cur = conn.execute(f"DELETE FROM {table} WHERE {column}=?", (uid,))
            counts[table] = int(cur.rowcount or 0)
        if _table_exists(conn, "announcements"):
            conn.execute(
                "UPDATE announcements SET created_by=NULL WHERE created_by=?", (uid,)
            )
        if _table_exists(conn, "invite_codes"):
            conn.execute("UPDATE invite_codes SET created_by=NULL WHERE created_by=?", (uid,))
            conn.execute("UPDATE invite_codes SET used_by=NULL WHERE used_by=?", (uid,))
        if _table_exists(conn, "users"):
            cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
            counts["users"] = int(cur.rowcount or 0)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def _delete_sqlite_db(
    conn: sqlite3.Connection, target: dict[str, Any], keys: list[str],
) -> dict[str, int]:
    uid = int(target["id"])
    prefix = f"{uid}:%"
    counts: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, column in SQLITE_SESSION_TABLES:
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (prefix,))
            counts[table] = int(cur.rowcount or 0)
        for table, column in SQLITE_ISOLATION_TABLES:
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (prefix,))
            counts[table] = int(cur.rowcount or 0)
        for table, column in SQLITE_UID_TABLES:
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(f"DELETE FROM {table} WHERE {column}=?", (uid,))
            counts[table] = int(cur.rowcount or 0)
        for table in AGENT_CORE_TABLES:
            if not _table_exists(conn, table):
                continue
            cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
            clauses = ["user_key LIKE ?"]
            params: list[Any] = [prefix]
            if "owner_user_id" in cols:
                clauses.append("owner_user_id=?")
                params.append(uid)
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {' OR '.join(clauses)}", tuple(params)
            )
            counts[table] = int(cur.rowcount or 0)
        if keys:
            ph = ",".join("?" for _ in keys)
            for table, column in (
                ("user_character_settings", "user_key"),
                ("emotion_stage_state", "character_id"),
                ("vital_signs_state", "character_id"),
            ):
                if _table_exists(conn, table):
                    conn.execute(
                        f"DELETE FROM {table} WHERE {column} IN ({ph})", tuple(keys)
                    )
            if _table_exists(conn, "daily_summaries"):
                clauses = " OR ".join("date LIKE ?" for _ in keys)
                conn.execute(
                    f"DELETE FROM daily_summaries WHERE {clauses}",
                    tuple(f"{key}|%" for key in keys),
                )
            if _table_exists(conn, "memory_recycle_bin"):
                clauses = " OR ".join("character_id LIKE ?" for _ in keys)
                conn.execute(
                    f"DELETE FROM memory_recycle_bin WHERE {clauses}",
                    tuple(f"user_fact:{key}%" for key in keys),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def _iter_owned_cards(uid: int, characters_dir: Path) -> list[tuple[str, Path]]:
    """枚举 ``config/characters/*.json`` 中归属该 uid 的私有卡（cid, 路径）。

    归属判定复用 W1 唯一 owner ``card_owner_key``（公共模板/无主卡一律保留），
    与 ``api/lifecycle.py::_character_instances_purge`` 同一语义。单卡 JSON
    损坏时跳过（与 lifecycle 同款，不因一张坏卡阻塞整批清理）。
    """
    # 从轻量 utils 模块取真源：经 api.routers 导入会拉起整个服务端
    # import 链（auth_jwt 启动 fail-closed），脚本场景不可用。
    from utils.character_helpers import card_owner_key

    out: list[tuple[str, Path]] = []
    if not characters_dir.exists():
        return out
    owner = str(uid)
    for path in sorted(characters_dir.glob("*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if card_owner_key(card) == owner:
            out.append((str(card.get("id") or path.stem), path))
    return out


def _character_instances_inventory(
    uid: int, characters_dir: Path, knowledge_dir: Path, users_db: Path,
) -> dict[str, int]:
    """私有角色实例残留计数：卡文件 / 知识文件 / 成就行（精确 cid）。"""
    cards = _iter_owned_cards(uid, characters_dir)
    cids = [cid for cid, _ in cards]
    knowledge = sum(
        1 for cid in cids if (knowledge_dir / f"{cid}.json").exists()
    )
    achievements = 0
    if cids:
        conn = _connect(users_db)
        if conn is None:
            achievements = -1 if users_db.exists() else 0
        else:
            try:
                ph = ",".join("?" for _ in cids)
                achievements = _count_where(
                    conn, "character_achievements",
                    f"character_id IN ({ph})", cids,
                )
            except sqlite3.Error:
                achievements = -1
            finally:
                conn.close()
    return {"cards": len(cards), "knowledge": knowledge, "achievements": achievements}


def _purge_character_instances(
    uid: int, characters_dir: Path, knowledge_dir: Path, users_db: Path,
) -> dict[str, int]:
    """删除该账号的私有角色实例（与 lifecycle `_character_instances_purge` 同 owner）。

    卡 id 与 uid 无关（如 ``7:card``），``character_achievements`` 里的成就行
    必须按**精确 cid** 删除——``uid:%`` 前缀匹配覆盖不到这类行。
    """
    cards = _iter_owned_cards(uid, characters_dir)
    removed_cards = 0
    removed_knowledge = 0
    for cid, path in cards:
        path.unlink(missing_ok=True)
        removed_cards += 1
        kpath = knowledge_dir / f"{cid}.json"
        if kpath.exists():
            kpath.unlink(missing_ok=True)
            removed_knowledge += 1
    removed_ach = 0
    cids = [cid for cid, _ in cards]
    if cids:
        conn = _connect(users_db, write=True)
        if conn is not None:
            try:
                if _table_exists(conn, "character_achievements"):
                    ph = ",".join("?" for _ in cids)
                    cur = conn.execute(
                        f"DELETE FROM character_achievements "
                        f"WHERE character_id IN ({ph})",
                        cids,
                    )
                    removed_ach = int(cur.rowcount or 0)
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()
    return {
        "cards": removed_cards,
        "knowledge": removed_knowledge,
        "achievements": removed_ach,
    }


def _assert_cold_shutdown(plans: list[dict[str, Any]]) -> str | None:
    """--apply 冷停闸门：目标账号仍有活着连接器宿主（或无法确认）即拒绝。

    脚本进程只能停掉本进程内的连接器；其他 worker 的宿主靠
    ``runtime_plane.channel_presence`` 心跳租约跨进程可见。presence 读失败
    时按「无法确认冷停」处理（fail-closed），绝不带连接器运行时删除。
    """
    from proactive import runtime_plane

    live: list[str] = []
    for plan in plans:
        uid = int(plan["target"]["id"])
        try:
            slots = runtime_plane.live_slots(uid)
        except Exception as e:  # noqa: BLE001
            return (
                f"无法读取通道 presence（{type(e).__name__}: {e}），"
                f"不能确认服务已冷停，拒绝 --apply"
            )
        if slots:
            live.append(f"uid={uid} slots={sorted(slots)}")
    if live:
        return (
            "检测到活跃微信通道宿主（" + "; ".join(live) + "）；"
            "必须先冷停服务（systemctl stop / 本地停进程）再执行 --apply"
        )
    return None


def _remove_wechat_channels(uid: int) -> dict[str, int]:
    """停掉该账号在跑连接器并删除磁盘通道目录（先停后删，防重建）。

    与 `api/lifecycle.py::_wechat_channels_purge` 同一 owner：
    `connector_registry.purge_user` 落期望态并断开其他 worker，
    `channel_paths.remove_user_sessions` 删除 credentials/state/qrcode。
    """
    from proactive import runtime_plane
    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import get_registry

    # 控制面期望态是跨进程停用信号：脚本进程通常没有连接器对象，
    # 只调 purge_user 不会触达别的 worker，必须先把 desired 落为 disabled。
    for slot in range(channel_paths.MAX_CHANNELS_PER_USER):
        try:
            runtime_plane.set_desired(uid, slot, "disabled")
            runtime_plane.presence_release(uid, slot)
        except Exception as e:  # noqa: BLE001
            logger.warning("写通道停用期望态失败 uid=%s slot=%s: %s", uid, slot, e)
    connectors = 0
    try:
        connectors = int(get_registry().purge_user(uid))
    except Exception as e:  # noqa: BLE001
        logger.warning("通道连接器停用失败 uid=%s: %s", uid, e)
        connectors = -1
    disk = channel_paths.remove_user_sessions(uid)
    return {"connectors": connectors, "disk_dirs": int(bool(disk))}


def _purge_ase_states(uid: int, keys: list[str], peers: list[str]) -> int:
    """删除 ASE 状态文件与 index.json 索引项（与生命周期同 owner）。"""
    from proactive import ase_hub

    return int(ase_hub.purge_user_states(_ase_owner_keys(uid, keys, peers)))


def _delete_agent_ledger(agent_db: Path, target: dict[str, Any], keys: list[str]) -> int:
    from shisi.agent_plane.event_ledger import EventLedger

    ledger = EventLedger(str(agent_db))
    return int(ledger.purge_owner_sessions(int(target["id"]), keys) or 0)


def _purge_throttle(
    config_path: Path, target: dict[str, Any], keys: list[str],
    peers: list[str] | None = None,
) -> int:
    from proactive.scheduler import ProactiveScheduler

    original = ProactiveScheduler._CONFIG_PATH
    ProactiveScheduler._CONFIG_PATH = Path(config_path)
    try:
        from api.lifecycle import _purge_throttle_file

        return int(
            _purge_throttle_file(
                int(target["id"]), keys, peers or []
            ).get("entries", 0)
        )
    finally:
        ProactiveScheduler._CONFIG_PATH = original


# ── DELETION_LOG 文本 ───────────────────────────────────────────────────


def deletion_log_entry(
    targets: list[dict[str, Any]], inventory_data: dict[str, Any],
    backups: dict[str, str], archive_dir: Path, residual: int,
    config_backup: str | None = None,
) -> str:
    now = datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = [
        f"## [{now[:10]}] e2e/测试命名空间账号清理批（脚本 `scripts/purge_e2e_accounts.py`）",
        "",
        "### 依据",
        "- 主动线治理审计：历史 ASE 索引键含 e2e/wbtest 账号，死会话每 5 分钟",
        "  写 vital_signs、烧决策 LLM 与 post-draft judge；测试账号无命名空间约束。",
        "- 本批为**一次性数据清理**，不改变正常账号注销 API 的语义。",
        "",
        "### 目标账号",
    ]
    for t in targets:
        lines.append(
            f"- user_id={t['id']}，username=`{t.get('username') or '(显式指定)'}`"
        )
    lines += ["", "### 删除范围（4 库 + 控制面/通道/ASE/私有角色实例逐表计数）"]
    for db_label, tables in inventory_data.items():
        lines.append(f"- **{db_label}**")
        for table, count in sorted(tables.items()):
            lines.append(f"  - `{table}`: {count}")
    lines += [
        "",
        f"### 备份（`{archive_dir}`）",
    ]
    for src, dest in sorted(backups.items()):
        lines.append(f"- `{src}` → `{dest}`（integrity_check=ok）")
    if config_backup:
        lines.append(
            f"- `scheduler_config.json` → `{config_backup}`（JSON 配置快照；"
            "含将被清除的 throttle 账本段）"
        )
    lines += [
        "",
        "### 验证",
        f"- 删除后跨表残留扫描：**{residual}**（0 = 零残留；-1 = 读数失败按残留处理）",
        "- 二次执行：目标集合为空（幂等）。",
        "- 不含聊天正文、密钥或 token；仅计数与账号 id。",
        "",
        "**Reversible**: 从上述备份恢复 4 库与 `scheduler_config.json` 即可回滚"
        "（恢复前先停服务）。",
        "",
    ]
    return "\n".join(lines)


# ── 主入口 ──────────────────────────────────────────────────────────────


def _default_paths(args: argparse.Namespace) -> dict[str, Path]:
    from shisi.agent_plane.event_ledger import EventLedger
    from shisi.memory.legacy import structured_memory as sm_mod

    users = Path(args.users_db) if args.users_db else (
        target_eligibility.default_users_db() or project_path("data", "users.db")
    )
    memory = Path(args.sqlite_db) if args.sqlite_db else Path(sm_mod.default_db_path())
    agent = Path(args.agent_db) if args.agent_db else Path(EventLedger.default_path())
    runtime = Path(args.runtime_db) if args.runtime_db else project_path(
        "data", "runtime_plane.db"
    )
    characters = Path(args.characters_dir) if args.characters_dir else project_path(
        "config", "characters"
    )
    knowledge = Path(args.knowledge_dir) if args.knowledge_dir else project_path(
        "data", "knowledge"
    )
    return {
        "users": users, "memory": memory, "agent": agent, "runtime": runtime,
        "characters": characters, "knowledge": knowledge,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理 e2e/测试命名空间账号（默认 dry-run）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只列清单（默认）")
    parser.add_argument("--apply", action="store_true", help="执行备份+删除")
    parser.add_argument("--user-id", action="append", type=int, default=[],
                        help="显式账号 id（可重复）")
    parser.add_argument("--session-key", action="append", default=[],
                        help="显式会话键（可重复，补充发现）")
    parser.add_argument("--users-db")
    parser.add_argument("--sqlite-db")
    parser.add_argument("--agent-db")
    parser.add_argument("--runtime-db")
    parser.add_argument("--scheduler-config")
    parser.add_argument("--archive-dir")
    parser.add_argument("--characters-dir", help="角色卡目录（默认 config/characters）")
    parser.add_argument("--knowledge-dir", help="知识目录（默认 data/knowledge）")
    args = parser.parse_args(argv)

    paths = _default_paths(args)
    scheduler_config = Path(args.scheduler_config) if args.scheduler_config else None
    if scheduler_config is None:
        from proactive.scheduler import ProactiveScheduler

        scheduler_config = Path(ProactiveScheduler._CONFIG_PATH)
    archive_root = Path(args.archive_dir) if args.archive_dir else project_path(
        "data", "archive"
    )

    try:
        targets = discover_targets(paths["users"], args.user_id)
    except RuntimeError as e:
        print(f"[purge-e2e][FAIL] {e}", file=sys.stderr)
        return 2
    if not targets:
        print("[purge-e2e] 未发现 e2e/测试命名空间账号（目标集合为空）。")
        return 0

    # 控制面 DB 路径必须指向本次清理的目标文件（runtime_plane 是全局单例）
    from proactive import runtime_plane

    runtime_plane.set_db_path(paths["runtime"])

    plans: list[dict[str, Any]] = []
    for target in targets:
        keys = collect_session_keys(
            target, paths["users"], paths["memory"], paths["runtime"], args.session_key,
        )
        peers = collect_peers(target, paths["users"])
        plans.append({"target": target, "keys": keys, "peers": peers})

    # 冷停闸门：带连接器运行时删除 = 删完即被 restore_on_boot 复活，拒绝执行。
    if args.apply:
        blocker = _assert_cold_shutdown(plans)
        if blocker:
            print(f"[purge-e2e][FAIL] {blocker}", file=sys.stderr)
            runtime_plane.set_db_path(None)
            return 2

    print(f"[purge-e2e] 目标 {len(plans)} 个；模式={'apply' if args.apply else 'dry-run'}")
    total_inventory: dict[str, dict[str, int]] = {}
    for plan in plans:
        target, keys, peers = plan["target"], plan["keys"], plan["peers"]
        print(f"\n[target] user_id={target['id']} username={target.get('username') or '-'}")
        print(f"  session_keys={keys}")
        print(f"  peers={peers}")
        inv = inventory(
            target, keys, paths["users"], paths["memory"], paths["agent"],
            paths["runtime"], paths["characters"], paths["knowledge"],
        )
        inv["ase_states"] = _ase_inventory(int(target["id"]), keys, peers)
        inv["wechat_channels"] = _wechat_inventory(int(target["id"]))
        inv["scheduler_config.json"] = {
            "throttle_entries": _throttle_count(scheduler_config, target["id"], keys)
        }
        plan["inventory"] = inv
        for db_label, tables in inv.items():
            print(f"  {db_label}:")
            for table, count in sorted(tables.items()):
                print(f"    {table}: {count}")
        for db_label, tables in inv.items():
            bucket = total_inventory.setdefault(db_label, {})
            for table, count in tables.items():
                bucket[table] = bucket.get(table, 0) + int(count)

    if not args.apply:
        print(
            "\n[purge-e2e] dry-run：零删除、零备份。"
            "确认清单后加 --apply 执行（先备份到 data/archive/）。"
        )
        entry = deletion_log_entry(
            [p["target"] for p in plans], total_inventory, {}, archive_root, -1,
        )
        print("\n----- DELETION_LOG 预览 -----\n" + entry)
        runtime_plane.set_db_path(None)
        return 0

    # 1) 备份（4 库 + 会被改写的 scheduler_config.json）
    archive_dir = archive_root / f"purge_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backups = backup_databases(
        [paths["users"], paths["memory"], paths["agent"], paths["runtime"]],
        archive_dir,
    )
    config_backup = backup_json_config(scheduler_config, archive_dir)
    print(
        f"\n[purge-e2e] 备份完成 {len(backups)} 个库"
        f"{' + scheduler_config.json' if config_backup else ''} → {archive_dir}"
    )

    # 2) 分库事务删除
    for plan in plans:
        target, keys, peers = plan["target"], plan["keys"], plan["peers"]
        uid = int(target["id"])
        # 先停连接器再删磁盘通道目录，防止进程内对象按旧路径重建凭证。
        _remove_wechat_channels(uid)
        # 私有角色实例：卡文件 + 知识文件 + 精确 cid 成就行（lifecycle 同 owner）。
        _purge_character_instances(
            uid, paths["characters"], paths["knowledge"], paths["users"]
        )
        _purge_ase_states(uid, keys, peers)
        conn = _connect(paths["users"], write=True)
        if conn is not None:
            try:
                _delete_users_db(conn, target, keys)
            finally:
                conn.close()
        conn = _connect(paths["memory"], write=True)
        if conn is not None:
            try:
                _delete_sqlite_db(conn, target, keys)
            finally:
                conn.close()
        if paths["agent"].exists():
            _delete_agent_ledger(paths["agent"], target, keys)
        runtime_plane.purge_owner(uid, keys)
        _purge_throttle(scheduler_config, target, keys, peers)
        print(f"[purge-e2e] 已删除 user_id={target['id']}")

    # 3) 零残留验证
    residual = 0
    for plan in plans:
        target, keys, peers = plan["target"], plan["keys"], plan["peers"]
        inv = inventory(
            target, keys, paths["users"], paths["memory"], paths["agent"],
            paths["runtime"], paths["characters"], paths["knowledge"],
        )
        inv["ase_states"] = _ase_inventory(int(target["id"]), keys, peers)
        inv["wechat_channels"] = _wechat_inventory(int(target["id"]))
        inv["scheduler_config.json"] = {
            "throttle_entries": _throttle_count(scheduler_config, target["id"], keys)
        }
        for tables in inv.values():
            # -1 = 读数失败：必须按残留处理，不能被 `> 0` 过滤成假通过。
            for value in tables.values():
                ivalue = int(value)
                if ivalue:
                    residual += abs(ivalue)
    if residual:
        print(f"[purge-e2e][FAIL] 删除后仍有 {residual} 行残留，见上清单。", file=sys.stderr)
        runtime_plane.set_db_path(None)
        return 1

    # 4) DELETION_LOG 文本
    entry = deletion_log_entry(
        [p["target"] for p in plans], total_inventory, backups, archive_dir, residual,
        config_backup=config_backup,
    )
    # 稳定入口（archive 根）+ 本次备份目录各留一份：root 供人与 DELETION_LOG
    # 引用，时间戳目录随备份一起可追溯。
    (archive_root / "DELETION_LOG_ENTRY.md").write_text(entry, encoding="utf-8")
    (archive_dir / "DELETION_LOG_ENTRY.md").write_text(entry, encoding="utf-8")
    print(
        f"\n[purge-e2e] 零残留验证通过；DELETION_LOG 条目："
        f"{archive_root / 'DELETION_LOG_ENTRY.md'}"
    )
    runtime_plane.set_db_path(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
