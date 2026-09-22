"""一次性清理（仅本机 dev 库）：affinity_records 里旧夹具写进宿主库的测试污染行。

背景：conftest 隔离夹具（`isolate_runtime_state_files`，2026-09-22 块E）落地之前，
既有夹具把测试键（如 `alice::default`）的调度器镜像行写进了开发机真实的
`data/sqlite.db`——该文件 gitignored，污染不被 git 发现。本机实测 21 行、值全 0.0
（两刻度等价，零实际影响，但属仓库卫生残留）。

用法（在项目根）：
    python scripts/clean_local_affinity_test_rows.py            # dry-run，只列出候选行
    python scripts/clean_local_affinity_test_rows.py --apply    # 先 cp 备份 db，再删除

⚠️ 只作用于本机 `data/sqlite.db`（或 --db 指定的路径）。生产库永远不跑本脚本——
生产 `user_scheduler_persist` 行实测为 0，且刻度标记统一由启动迁移
（`shisi.api.registry.migrate_affinity_mirror_reason`）负责。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shisi.affinity.enhancer import MIRROR_REASON_POINTS, MIRROR_REASON_SHISI  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "data" / "sqlite.db"), help="目标库（默认本机 data/sqlite.db）")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只 dry-run 列出）")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"[skip] 库不存在: {db}")
        return 0

    mirrors = (MIRROR_REASON_POINTS, MIRROR_REASON_SHISI)
    placeholders = ",".join("?" * len(mirrors))
    sql_sel = f"SELECT id, character_id, new_value, reason, created_at FROM affinity_records WHERE reason IN ({placeholders})"
    with closing(sqlite3.connect(str(db))) as conn:
        rows = conn.execute(sql_sel, mirrors).fetchall()
        print(f"[scan] {db} 中调度器镜像残留行: {len(rows)}")
        for r in rows:
            print("   ", r)
        if not rows or not args.apply:
            print("[dry-run] 未做任何修改；确认后用 --apply 执行删除" if rows else "[ok] 无残留")
            return 0
        backup = db.with_suffix(db.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        conn.commit()  # 释放 WAL 未提交事务后由 cp 等效备份整库
    shutil.copy2(db, backup)
    with closing(sqlite3.connect(str(db))) as conn, conn:
        n = conn.execute(f"DELETE FROM affinity_records WHERE reason IN ({placeholders})", mirrors).rowcount
    print(f"[done] 备份 -> {backup.name}；删除 {n} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
