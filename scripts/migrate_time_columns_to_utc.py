"""时间列 UTC 迁移脚本 —— 把历史北京墙钟列逐表减 8 小时（2026-11-21）。

背景：全库曾存在三套时钟——
1. ``chat_history`` / ``user_facts`` / ``affinity_audit``：SQLite ``CURRENT_TIMESTAMP``
   写入的 UTC naive（本次**不动**）；
2. ``topic_state`` / ``thread_plan`` / ``topic_meta`` / ``user_profile`` /
   ``reminders.trigger_time`` / ``pending_intents.expires_at`` / ``agent_core`` /
   ``characters_v2.created_at``（旧写侧显式持久化本地 now）：
   北京墙钟 naive（本次迁到 UTC naive，逐列 -8h）；
3. ``event_ledger``：带 ``+08:00`` 的 aware 本地 ISO（本次改为 aware UTC，
   只换算时区、不改真实时刻）；
4. ``memory_recycle_bin.deleted_at / restore_before``：**混列**——写侧
   `delete_fact` 曾用北京墙钟成对写入，而 DDL 默认 ``datetime('now')`` 是
   UTC，同一表两种口径并存。按「配对识别、从宽保留」处理，见
   :func:`_migrate_recycle_bin_pairs`。

用法（先冷停服务，再执行）::

    python scripts/migrate_time_columns_to_utc.py --dry-run
    python scripts/migrate_time_columns_to_utc.py --apply
    python scripts/migrate_time_columns_to_utc.py --allow-non-beijing-host --apply
    python scripts/migrate_time_columns_to_utc.py --rollback data/backups/time_caliber_xxx

宿主时区前提（2026-11-22 L9 修复）：逐列 ``-8h`` 的前提是**旧值=北京墙钟
（UTC+8）**。旧写侧是宿主 ``datetime.now()``（无时区保证）——若产生这些
存量行的宿主不在 UTC+8，``-8h`` 会系统性多减，且幂等标记会阻止事后纠正。
故 ``--apply`` 前校验宿主 UTC 偏移：非 ``+08:00`` 默认**拒绝执行**；确认
存量值确为北京墙钟（例如库从 +8 宿主整体迁移而来）时，显式加
``--allow-non-beijing-host`` 覆盖。``--dry-run`` 报告会写明该假设与当前
宿主偏移。

安全保证（两阶段：先全部备份，后逐库 apply，失败按备份恢复）：
- ``--dry-run`` 默认模式，不写库、不建备份；
- ``--apply`` 先经 SQLite backup API 复制 ``data/sqlite.db`` 与
  ``data/agent_plane.db`` 到 ``data/backups/time_caliber_<UTC时间戳>/``，
  全部备份成功后才逐库迁移；``migrate_sqlite_db`` 内部 commit 后，任一库
  失败都会用备份覆盖恢复**所有**已触及的库，确保不留半迁移态；
- 每个库写入幂等标记 ``time_caliber_migrations``，重复 ``--apply`` 直接跳过；
- 更新前后逐表行数比对，不一致即抛错回滚；
- 备份目录内生成 ``manifest.json`` 与 ``rollback.py``（等价于
  ``--rollback <备份目录>``）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 允许从仓库任意 CWD 直接运行
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.project_paths import project_path  # noqa: E402

MIGRATION_NAME = "time_caliber_utc_v1"
_UTC8_SHIFT = timedelta(hours=8)
_TS_FMT = "%Y-%m-%d %H:%M:%S"

#: 北京墙钟的 UTC 偏移（分钟）。逐列 -8h 的前提是旧值=北京墙钟，
#: 见 :func:`ensure_beijing_wall_clock_host`。
_BEIJING_OFFSET_MINUTES = 8 * 60


def host_utc_offset_minutes() -> int:
    """宿主当前 UTC 偏移（分钟，含夏令时）。

    用 ``datetime.now().astimezone()`` 取本地 aware 时间的偏移——Windows 无
    ``time.tzset``，该写法跨平台一致且随 DST 自动正确。
    """
    return int(datetime.now().astimezone().utcoffset().total_seconds() // 60)


def ensure_beijing_wall_clock_host(
    offset_minutes: int, *, allow_override: bool = False
) -> None:
    """``--apply`` 前置校验：宿主非 UTC+8 时拒绝执行（2026-11-22 L9）。

    旧写侧 ``datetime.now()`` 落的是**宿主墙钟**（无时区保证）；本脚本按
    北京墙钟解释并统一 -8h。宿主不在 UTC+8 时该假设不成立——再减 8h 会
    系统性多减，且幂等标记会阻止事后纠正。确认存量值确为北京墙钟（如库
    从 +8 宿主整体迁来）时，显式 ``allow_override=True``（CLI 对应
    ``--allow-non-beijing-host``）放行。
    """
    if offset_minutes == _BEIJING_OFFSET_MINUTES or allow_override:
        return
    raise RuntimeError(
        f"宿主时区非 UTC+8（当前 UTC 偏移 {offset_minutes} 分钟），"
        "逐列 -8h 的「旧值=北京墙钟」假设可能不成立；如确认存量值确为"
        "北京墙钟，请显式加 --allow-non-beijing-host 重试"
    )


#: ``data/sqlite.db`` 中按北京墙钟写入的列（逐表 -8h；前提=宿主 UTC+8，
#: 非 +8 宿主必须先过 :func:`ensure_beijing_wall_clock_host`）。
SQLITE_TABLE_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("topic_state", ("last_recalled_at", "last_active_at", "created_at", "updated_at", "expires_at")),
    # 遗留表：v1.72 迁移前的 topic_plan（部分存量库仍可能保留，存在才迁移）。
    ("topic_plan", ("planned_at", "next_at", "expires_at", "consumed_at")),
    ("thread_plan", ("planned_at", "next_at", "expires_at", "consumed_at")),
    ("topic_meta", ("last_llm_at",)),
    ("user_profile", ("updated_at",)),
    ("reminders", ("trigger_time", "delivered_at")),
    ("pending_intents", ("expires_at", "updated_at")),
    ("agent_events", ("created_at",)),
    ("agent_threads", ("opened_at", "last_event_at", "closed_at", "created_at", "updated_at")),
    ("agent_commitments", ("due_at", "expires_at", "created_at", "updated_at", "fulfilled_at")),
    # characters_v2.created_at：旧写侧（sqlite_repository.save）显式持久化
    # 本地 now，直接 -8h；updated_at 是 aware UTC ISO，不进清单（shift 对
    # aware 值原样跳过，双保险）。新写侧已改 now_utc_naive，靠幂等标记挡住
    # 二次平移。
    ("characters_v2", ("created_at",)),
)

#: 回收站表（混列，**不走**上面的均匀 -8h 清单）：``delete_fact`` 把
#: ``restore_before = deleted_at + retain_days`` 成对写入（旧代码为北京墙钟），
#: DDL 默认 ``datetime('now')``（UTC）只作用于未显式赋值的插入路径 → 存量
#: 数据两种口径混存。处理策略见 :func:`_migrate_recycle_bin_pairs`。
RECYCLE_BIN_TABLE = "memory_recycle_bin"
RECYCLE_BIN_COLUMNS = ("deleted_at", "restore_before")

#: ``pending_intents.updated_at`` 是混合列：插入时未显式赋值 → ``created_at``
#: 的 UTC 默认值；更新分支才写本地墙钟。仅当它晚于 created_at 时才按本地迁移，
#: 避免把从未更新的行再减 8 小时。
_PENDING_INTENT_UPDATED_WHERE = "updated_at > created_at"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_marker(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS time_caliber_migrations (
            migration TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )


def _has_marker(conn: sqlite3.Connection) -> bool:
    """只读探测幂等标记；**不得**在 dry-run 建表（否则 dry-run 也会写库）。"""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='time_caliber_migrations'"
    ).fetchone()
    if not exists:
        return False
    row = conn.execute(
        "SELECT 1 FROM time_caliber_migrations WHERE migration = ?",
        (MIGRATION_NAME,),
    ).fetchone()
    return row is not None


def _markers_present(db_path: Path) -> bool:
    """库不存在或已有迁移标记 → 不需要再迁移（只读，不建表）。"""
    if not db_path.exists():
        return True
    conn = _connect(db_path)
    try:
        return _has_marker(conn)
    finally:
        conn.close()


@contextmanager
def _migration_lock():
    """多 worker 启动时的跨进程锁：避免两个进程同时减 8 小时。"""
    from utils import json_state

    with json_state._file_lock(  # noqa: SLF001 —— 复用唯一跨进程锁原语
        project_path("data", "backups", "time_caliber")
    ):
        yield


def ensure_applied(
    sqlite_db: Path | None = None,
    agent_plane_db: Path | None = None,
    backup_root: Path | None = None,
    allow_non_beijing_host: bool = False,
) -> dict[str, Any]:
    """启动期接线：未迁移则加锁执行一次；已迁移/空库直接返回。"""
    sqlite_db = Path(sqlite_db) if sqlite_db else Path(project_path("data", "sqlite.db"))
    agent_plane_db = (
        Path(agent_plane_db)
        if agent_plane_db
        else Path(project_path("data", "agent_plane.db"))
    )
    backup_root = (
        Path(backup_root)
        if backup_root
        else Path(project_path("data", "backups"))
    )
    if _markers_present(sqlite_db) and _markers_present(agent_plane_db):
        return {"status": "already_applied"}
    with _migration_lock():
        if _markers_present(sqlite_db) and _markers_present(agent_plane_db):
            return {"status": "already_applied"}
        result = apply_with_backup(
            sqlite_db,
            agent_plane_db,
            backup_root,
            allow_non_beijing_host=allow_non_beijing_host,
        )
        return {"status": "applied", **result}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def shift_naive_text(value: object, *, reverse: bool = False) -> str:
    """北京墙钟文本 ↔ UTC 文本（同格式；``T`` 分隔的 ISO 保留 ``T``）。

    - ``reverse=False``：减 8 小时（本地 → UTC，迁移方向）；
    - ``reverse=True``：加 8 小时（UTC → 本地，人工回滚辅助）。

    空值返回空串；畸形值原样返回（由调用方计数，不静默丢行）。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    use_t = "T" in raw
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if dt.tzinfo is not None:
        # 该列按契约只应有 naive；aware 值原样保留，避免二次换算。
        return raw
    shifted = dt + _UTC8_SHIFT if reverse else dt - _UTC8_SHIFT
    if use_t:
        return shifted.isoformat(timespec="seconds")
    return shifted.strftime(_TS_FMT)


def shift_event_ledger_iso(value: object, *, reverse: bool = False) -> str:
    """event_ledger ISO 文本 → aware UTC（``+00:00``）。

    历史值是 ``datetime.now().astimezone()`` 的 aware 本地 ISO；迁移只改时区
    表示，不改变真实时刻。回滚方向转回北京 ``+08:00``（审计辅助用）。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        # 旧行理论上不会出现 naive；按北京墙钟解释，宁保守不猜 UTC。
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    target = timezone(timedelta(hours=8)) if reverse else timezone.utc
    return dt.astimezone(target).isoformat(timespec="seconds")


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _parse_naive_text(value: object) -> datetime | None:
    """解析 naive 时间文本；空值/畸形/带时区一律返回 None（按不可识别处理）。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(raw, _TS_FMT)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return None
    return dt


def _migrate_recycle_bin_pairs(
    conn: sqlite3.Connection,
    *,
    reverse: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """回收站混列迁移：只平移「可配对」的行，无法识别的行**从宽保留**。

    判据：``restore_before - deleted_at`` 为**正整数天**（``delete_fact`` 写入时
    恒等于 ``retain_days`` 的整天间隔）→ 该行两列同出旧北京墙钟写侧 → 成对 -8h。

    其余行（缺值/畸形/间隔非整天，例如 DDL 默认 UTC ``deleted_at`` 搭配本地
    ``restore_before`` 的差值会多出 8 小时）一律**原样保留**，理由：

    - 迁移后出库比较（``purge_expired_recycle_bin``）用 UTC。未平移的北京墙钟
      ``restore_before`` 数值上比真实 UTC 时刻**大 8 小时** → 出库判定只会
      **更晚**，绝不提前删除（回收站只影响过期出库时间，宁晚勿早删）；
    - 反向错误（把本就是 UTC 的行再减 8h）会让出库**提前** 8 小时，属于不可
      接受的过早删除，故宁可少迁。
    - 前提：本脚本在冷停窗口、新 UTC 写侧代码上线**之前**执行——此刻所有满足
      「正整天间隔」的行都出自旧本地墙钟写侧；上线后的新 UTC 行由幂等标记
      挡住，不会被二次平移。
    """
    if not _table_exists(conn, RECYCLE_BIN_TABLE):
        return {
            "exists": False,
            "rows": 0,
            "pair_shifted": 0,
            "kept_unpaired": 0,
            "updates": 0,
        }
    select_cols = ", ".join(("id", *RECYCLE_BIN_COLUMNS))
    rows = conn.execute(f"SELECT {select_cols} FROM {RECYCLE_BIN_TABLE}").fetchall()
    shifted = 0
    kept = 0
    for row in rows:
        row_id = int(row[0])
        deleted_raw = str(row[1] or "").strip()
        restore_raw = str(row[2] or "").strip()
        deleted_dt = _parse_naive_text(deleted_raw)
        restore_dt = _parse_naive_text(restore_raw)
        if deleted_dt is None or restore_dt is None:
            kept += 1
            continue
        delta = restore_dt - deleted_dt
        if delta <= timedelta(0) or delta != timedelta(days=delta.days):
            kept += 1
            continue
        if dry_run:
            shifted += 1
            continue
        conn.execute(
            f"UPDATE {RECYCLE_BIN_TABLE} SET deleted_at = ?, restore_before = ? WHERE id = ?",
            (shift_naive_text(deleted_raw, reverse=reverse),
             shift_naive_text(restore_raw, reverse=reverse),
             row_id),
        )
        shifted += 1
    if not dry_run:
        count_after = _count_rows(conn, RECYCLE_BIN_TABLE)
        if count_after != len(rows):
            raise RuntimeError(
                f"行数比对失败 {RECYCLE_BIN_TABLE}: before={len(rows)} after={count_after}"
            )
    return {
        "exists": True,
        "rows": len(rows),
        "pair_shifted": shifted,
        "kept_unpaired": kept,
        "updates": shifted * len(RECYCLE_BIN_COLUMNS),
    }


def _iter_updates(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[str],
    *,
    reverse: bool,
    where: str = "",
) -> Iterable[tuple[int, str, str]]:
    """产出 ``(rowid, column, new_value)``；跳过空值/未变化值。"""
    existing = _table_columns(conn, table)
    usable = [c for c in columns if c in existing]
    if not usable:
        return
    select_cols = ", ".join(["rowid", *usable])
    sql = f"SELECT {select_cols} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    for row in conn.execute(sql).fetchall():
        rowid = int(row[0])
        for idx, column in enumerate(usable, start=1):
            old = row[idx]
            new = shift_naive_text(old, reverse=reverse)
            if new and new != str(old or ""):
                yield rowid, column, new


def migrate_sqlite_db(
    db_path: Path,
    *,
    dry_run: bool,
    reverse: bool = False,
) -> dict[str, Any]:
    """迁移单个 ``data/sqlite.db``；返回统计（dry-run 也返回预览计数）。

    事务覆盖所有表：任何异常 rollback，行数不一致 raise ``RuntimeError``。
    """
    result: dict[str, Any] = {
        "db": str(db_path),
        "exists": db_path.exists(),
        "applied": False,
        "already_applied": False,
        "tables": {},
    }
    if not db_path.exists():
        return result

    conn = _connect(db_path)
    try:
        if _has_marker(conn) and not reverse:
            result["already_applied"] = True
            return result

        table_stats: dict[str, Any] = {}
        total_updates = 0
        for table, columns in SQLITE_TABLE_SPECS:
            if not _table_exists(conn, table):
                table_stats[table] = {"exists": False, "updates": 0}
                continue
            count_before = _count_rows(conn, table)
            where = _PENDING_INTENT_UPDATED_WHERE if table == "pending_intents" else ""
            updates = list(
                _iter_updates(conn, table, columns, reverse=reverse, where=where)
            )
            table_stats[table] = {
                "exists": True,
                "rows": count_before,
                "updates": len(updates),
            }
            total_updates += len(updates)
            if dry_run:
                continue
            for rowid, column, new in updates:
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                    (new, rowid),
                )
            count_after = _count_rows(conn, table)
            if count_after != count_before:
                raise RuntimeError(
                    f"行数比对失败 {table}: before={count_before} after={count_after}"
                )

        # 混列表单独处理（配对识别、从宽保留），不进均匀 -8h 清单。
        recycle_stats = _migrate_recycle_bin_pairs(conn, reverse=reverse, dry_run=dry_run)
        table_stats[RECYCLE_BIN_TABLE] = recycle_stats
        total_updates += int(recycle_stats["updates"])

        result["tables"] = table_stats
        result["total_updates"] = total_updates
        if dry_run:
            return result

        _ensure_marker(conn)
        conn.execute(
            "INSERT OR REPLACE INTO time_caliber_migrations (migration, applied_at, detail) "
            "VALUES (?, ?, ?)",
            (
                MIGRATION_NAME,
                datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                json.dumps({"updates": total_updates}, ensure_ascii=False),
            ),
        )
        conn.commit()
        result["applied"] = True
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_agent_plane_db(db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    """迁移 ``data/agent_plane.db`` 的 ``event_ledger.created_at`` → aware UTC。"""
    result: dict[str, Any] = {
        "db": str(db_path),
        "exists": db_path.exists(),
        "applied": False,
        "already_applied": False,
        "updates": 0,
    }
    if not db_path.exists():
        return result

    conn = _connect(db_path)
    try:
        if _has_marker(conn):
            result["already_applied"] = True
            return result
        if not _table_exists(conn, "event_ledger"):
            # 无目标表的库也写标记，避免启动期 ensure_applied 每次都重跑。
            if not dry_run:
                _ensure_marker(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO time_caliber_migrations "
                    "(migration, applied_at, detail) VALUES (?, ?, ?)",
                    (
                        MIGRATION_NAME,
                        datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                        json.dumps({"updates": 0}, ensure_ascii=False),
                    ),
                )
                conn.commit()
                result["applied"] = True
            return result

        count_before = _count_rows(conn, "event_ledger")
        rows = conn.execute("SELECT rowid, created_at FROM event_ledger").fetchall()
        updates = [
            (int(row[0]), shift_event_ledger_iso(row[1]))
            for row in rows
            if str(row[1] or "").strip()
            and shift_event_ledger_iso(row[1]) != str(row[1] or "")
        ]
        result["updates"] = len(updates)
        if dry_run:
            return result
        for rowid, new in updates:
            conn.execute(
                "UPDATE event_ledger SET created_at = ? WHERE rowid = ?", (new, rowid)
            )
        count_after = _count_rows(conn, "event_ledger")
        if count_after != count_before:
            raise RuntimeError(
                f"行数比对失败 event_ledger: before={count_before} after={count_after}"
            )
        _ensure_marker(conn)
        conn.execute(
            "INSERT OR REPLACE INTO time_caliber_migrations (migration, applied_at, detail) "
            "VALUES (?, ?, ?)",
            (
                MIGRATION_NAME,
                datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                json.dumps({"updates": len(updates)}, ensure_ascii=False),
            ),
        )
        conn.commit()
        result["applied"] = True
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _backup_db(src: Path, dst: Path) -> None:
    """SQLite backup API 复制（兼容 WAL，比 shutil.copy 安全）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _restore_db(src: Path, dst: Path) -> None:
    """从备份恢复数据库（backup API 覆盖写入）。"""
    _backup_db(src, dst)


def _write_rollback_script(backup_dir: Path, script_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env python\n"
        '"""自动生成的回滚入口：python rollback.py"""\n'
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "repo = Path(__file__).resolve().parents[3]\n"
        "cmd = [sys.executable, str(repo / 'scripts' / 'migrate_time_columns_to_utc.py'),\n"
        "       '--rollback', str(Path(__file__).resolve().parent)]\n"
        "raise SystemExit(subprocess.call(cmd))\n",
        encoding="utf-8",
    )


def apply_with_backup(
    sqlite_db: Path,
    agent_plane_db: Path,
    backup_root: Path,
    *,
    allow_non_beijing_host: bool = False,
) -> dict[str, Any]:
    """备份两库 → 迁移两库 → 写 manifest/rollback.py。

    非 UTC+8 宿主默认拒绝（L9）：校验在任何备份/写库动作**之前**，拒绝时
    不留备份目录、不改库。
    """
    ensure_beijing_wall_clock_host(
        host_utc_offset_minutes(), allow_override=allow_non_beijing_host
    )
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"time_caliber_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    dbs = [sqlite_db, agent_plane_db]
    backups: dict[str, str] = {}
    for db in dbs:
        if db.exists():
            dst = backup_dir / db.name
            _backup_db(db, dst)
            backups[str(db)] = str(dst)

    manifest = {
        "migration": MIGRATION_NAME,
        "created_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "backups": backups,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_rollback_script(backup_dir, backup_dir / "rollback.py")

    try:
        sqlite_result = migrate_sqlite_db(sqlite_db, dry_run=False)
        plane_result = migrate_agent_plane_db(agent_plane_db, dry_run=False)
    except Exception:
        # 任一步失败整体回滚：sqlite 的 migrate_sqlite_db 已在内部 commit，
        # 仅回滚当前连接无法撤销；必须用备份覆盖恢复两库，避免半迁移。
        for original, backup in backups.items():
            _restore_db(Path(backup), Path(original))
        raise
    return {
        "backup_dir": str(backup_dir),
        "sqlite": sqlite_result,
        "agent_plane": plane_result,
    }


def rollback_from_backup(backup_dir: Path) -> list[str]:
    """按 manifest 恢复备份库（冷停状态下执行）。"""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少 manifest.json: {backup_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for original, backup in (manifest.get("backups") or {}).items():
        backup_path = Path(backup)
        if not backup_path.exists():
            raise FileNotFoundError(f"备份缺失: {backup_path}")
        _restore_db(backup_path, Path(original))
        restored.append(str(original))
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="时间列统一为 UTC 的迁移脚本")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只预览，不写库（默认）")
    mode.add_argument("--apply", action="store_true", help="备份后执行迁移")
    mode.add_argument("--rollback", metavar="BACKUP_DIR", help="从备份目录恢复")
    parser.add_argument(
        "--allow-non-beijing-host",
        action="store_true",
        help="宿主时区非 UTC+8 时仍强制执行（仅当确认存量旧值确为北京墙钟，"
        "例如库从 +8 宿主整体迁移而来）",
    )
    args = parser.parse_args(argv)

    sqlite_db = Path(project_path("data", "sqlite.db"))
    agent_plane_db = Path(project_path("data", "agent_plane.db"))
    backup_root = Path(project_path("data", "backups"))

    if args.rollback:
        restored = rollback_from_backup(Path(args.rollback))
        print(json.dumps({"status": "rolled_back", "restored": restored}, ensure_ascii=False, indent=2))
        return 0

    dry_run = not args.apply
    if dry_run:
        sqlite_result = migrate_sqlite_db(sqlite_db, dry_run=True)
        plane_result = migrate_agent_plane_db(agent_plane_db, dry_run=True)
        # L9：dry-run 报告必须写明 -8h 的「旧值=北京墙钟」假设与宿主当前偏移，
        # 让执行者在 --apply 前能发现宿主时区不符的情形。
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "assumption": (
                        "存量 naive 值按北京墙钟（UTC+8）解释、逐列 -8h；"
                        "旧写侧为宿主 datetime.now()，宿主非 UTC+8 时该假设"
                        "不成立，--apply 会被拒绝（可 --allow-non-beijing-host 显式覆盖）"
                    ),
                    "host_utc_offset_minutes": host_utc_offset_minutes(),
                    "sqlite": sqlite_result,
                    "agent_plane": plane_result,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    result = ensure_applied(
        sqlite_db,
        agent_plane_db,
        backup_root,
        allow_non_beijing_host=args.allow_non_beijing_host,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
