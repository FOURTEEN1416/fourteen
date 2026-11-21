"""tool_call_log 退役迁移（2026-11-21 可观测性批）。

背景：审计时 ``tool_call_log`` 全仓零读写（仅 ``structured_memory.py`` 建表语句与
``docs/CODEMAPS/DATABASE.md`` 提及），生产/本地均 0 行。该表的能力已被
``event_ledger`` 的 ``tool_call`` / ``tool_result`` 事件取代（含 call_id、
成功/失败、耗时与回放能力），按 ``docs/DELETION_LOG.md`` 登记退役。

用法（先冷停服务；本脚本不在登记批执行）::

    python scripts/retire_tool_call_log.py --dry-run
    python scripts/retire_tool_call_log.py --apply

安全保证：
- 默认 dry-run，只读检查表是否存在、行数多少，不写库；
- ``--apply`` 先用 SQLite backup API 备份整库到
  ``data/backups/tool_call_log_retire_<UTC时间戳>/``，再 DROP 表；
- 表不存在视为已退役（幂等），不重复备份/删除。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许从仓库任意 CWD 直接运行
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.project_paths import project_path  # noqa: E402

TABLE = "tool_call_log"


def _table_exists(conn: sqlite3.Connection, table: str = TABLE) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str = TABLE) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def retire_tool_call_log(db_path: Path, *, apply: bool = False) -> dict:
    """检查/删除 ``tool_call_log``；返回可序列化的执行结果。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return {"status": "missing_db", "db": str(db_path), "applied": False}

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        if not _table_exists(conn):
            return {"status": "already_retired", "db": str(db_path), "applied": False}
        rows = _row_count(conn)
        if not apply:
            return {
                "status": "dry_run",
                "db": str(db_path),
                "table": TABLE,
                "rows": rows,
                "applied": False,
            }

        # 真删前整库冷备：任何误判都可从备份恢复。
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = project_path("data", "backups", f"tool_call_log_retire_{stamp}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / db_path.name
        dst = sqlite3.connect(str(backup_path))
        try:
            conn.backup(dst)
        finally:
            dst.close()

        conn.execute(f"DROP TABLE {TABLE}")  # noqa: S608
        conn.commit()
        if _table_exists(conn):
            raise RuntimeError("DROP 后表仍存在")
        return {
            "status": "dropped",
            "db": str(db_path),
            "table": TABLE,
            "rows": rows,
            "backup": str(backup_path),
            "applied": True,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="退役 tool_call_log（默认 dry-run）")
    parser.add_argument(
        "--db", default=str(project_path("data", "sqlite.db")), help="sqlite.db 路径"
    )
    parser.add_argument(
        "--apply", action="store_true", help="真删表（默认 dry-run，只读检查）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="显式 dry-run（默认行为，兼容写法）"
    )
    args = parser.parse_args(argv)
    result = retire_tool_call_log(
        Path(args.db), apply=bool(args.apply and not args.dry_run)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
