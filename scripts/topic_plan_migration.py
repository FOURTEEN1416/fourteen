"""topic_plan → thread_plan 迁移 / 回滚（2026-11-13）。

背景：``topic_plan`` 表由 ``utils.topic_state`` 在 v1.58 创建；本批把计划
表 owner 收口到 ThreadState（``speaker/thread_state.py``）并改名为
``thread_plan``。``utils.topic_state._connect(ensure_schema=True)`` 会自动
做一次幂等迁移；本脚本用于**显式演练/回滚**（部署前在副本上先跑）。

用法：
    python scripts/topic_plan_migration.py status [--db PATH]
    python scripts/topic_plan_migration.py up     [--db PATH]
    python scripts/topic_plan_migration.py down   [--db PATH]

默认库：``data/sqlite.db``。脚本只动 thread_plan/topic_plan 两张表，不碰
topic_state/topic_meta；up 先复制再删旧表，down 反向复制再删新表。
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from utils.project_paths import project_path

THREAD_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS thread_plan (
    session_key  TEXT NOT NULL,
    character_id TEXT NOT NULL,
    topic        TEXT NOT NULL DEFAULT '',
    intent       TEXT NOT NULL DEFAULT '',
    planned_at   TEXT NOT NULL,
    next_at      TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    PRIMARY KEY (session_key, character_id)
)
"""
LEGACY_PLAN_DDL = THREAD_PLAN_DDL.replace("thread_plan", "topic_plan")
PLAN_COLS = (
    "session_key, character_id, topic, intent, planned_at, next_at, "
    "expires_at, consumed_at"
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _copy(conn: sqlite3.Connection, src: str, dst: str) -> int:
    before = conn.total_changes
    conn.execute(
        f"INSERT OR IGNORE INTO {dst} ({PLAN_COLS}) "
        f"SELECT {PLAN_COLS} FROM {src}"
    )
    return conn.total_changes - before


def up(conn: sqlite3.Connection) -> dict[str, int | bool]:
    """topic_plan → thread_plan；旧表存在才动作。"""
    tables = _tables(conn)
    if "thread_plan" not in tables:
        conn.execute(THREAD_PLAN_DDL)
    copied = 0
    if "topic_plan" in tables:
        copied = _copy(conn, "topic_plan", "thread_plan")
        conn.execute("DROP TABLE topic_plan")
    conn.commit()
    return {"copied": copied, "dropped_topic_plan": "topic_plan" in tables}


def down(conn: sqlite3.Connection) -> dict[str, int | bool]:
    """thread_plan → topic_plan；新表存在才动作。"""
    tables = _tables(conn)
    if "topic_plan" not in tables:
        conn.execute(LEGACY_PLAN_DDL)
    copied = 0
    if "thread_plan" in tables:
        copied = _copy(conn, "thread_plan", "topic_plan")
        conn.execute("DROP TABLE thread_plan")
    conn.commit()
    return {"copied": copied, "dropped_thread_plan": "thread_plan" in tables}


def status(conn: sqlite3.Connection) -> dict[str, int | bool]:
    tables = _tables(conn)
    result: dict[str, int | bool] = {
        "topic_plan": "topic_plan" in tables,
        "thread_plan": "thread_plan" in tables,
    }
    for name in ("topic_plan", "thread_plan"):
        if name in tables:
            result[f"{name}_rows"] = int(
                conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "up", "down"))
    parser.add_argument(
        "--db", default=str(project_path("data", "sqlite.db")),
        help="目标 SQLite 库路径（默认 data/sqlite.db）",
    )
    args = parser.parse_args()
    path = Path(args.db)
    if not path.is_file():
        print(f"DB not found: {path}")
        return 2
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        result = {"status": status, "up": up, "down": down}[args.action](conn)
    finally:
        conn.close()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
