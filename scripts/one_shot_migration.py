#!/usr/bin/env python3
"""一步到位数据迁移CLI"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="一步到位数据迁移")
    parser.add_argument("--db-path", type=str, default="data/sqlite.db")
    parser.add_argument("--char-dir", type=str, default="data/characters")
    parser.add_argument("--rollback", action="store_true", help="执行回滚")
    parser.add_argument("--dry-run", action="store_true", help="仅预检不执行")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    char_dir = Path(args.char_dir)

    if args.dry_run:
        print("=== 预检模式 ===")
        char_files = list(char_dir.glob("*.json")) if char_dir.exists() else []
        print(f"角色文件数: {len(char_files)}")
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT COUNT(*) FROM characters").fetchone()
                print(f"旧数据库角色数: {row[0]}")
            except Exception as e:  # noqa: BLE001
                print(f"旧表读取失败: {e}")
            finally:
                conn.close()
        else:
            print("数据库不存在")
        return 0

    if args.rollback:
        from shisi.infrastructure.migration.rollback_runner import run as run_rollback
        result = run_rollback(db_path)
        print(f"回滚{'成功' if result.success else '失败'}: {result.message}")
        return 0 if result.success else 1

    from shisi.infrastructure.migration.migration_runner import run as run_migration
    result = run_migration(db_path, char_dir)
    print(f"\n迁移完成: {result.total_migrated} 成功, {result.total_failed} 失败")
    if result.active_character:
        print(f"活跃角色: {result.active_character}")
    if result.backup_path:
        print(f"备份: {result.backup_path}")
    return 0 if result.total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
