"""回滚运行器"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RollbackResult:
    success: bool
    message: str
    backup_used: str | None = None


def run(db_path: Path = Path("data/sqlite.db")) -> RollbackResult:
    backups = sorted(db_path.parent.glob("sqlite.db.backup.*"))
    if not backups:
        return RollbackResult(success=False, message="未找到备份文件，无法回滚")

    latest_backup = backups[-1]

    try:
        conn = sqlite3.connect(str(latest_backup))
        conn.execute("SELECT 1")
        conn.close()
    except Exception as e:  # noqa: BLE001
        return RollbackResult(success=False, message=f"备份文件损坏: {e}")

    shutil.copy(latest_backup, db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DROP TABLE IF EXISTS characters_v2")
        conn.commit()
    finally:
        conn.close()

    return RollbackResult(
        success=True,
        message="回滚完成",
        backup_used=str(latest_backup),
    )
