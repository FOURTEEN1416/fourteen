"""迁移应用服务"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from shisi.infrastructure.migration.migration_runner import MigrationResult
from shisi.infrastructure.migration.migration_runner import run as run_migration
from shisi.infrastructure.migration.rollback_runner import RollbackResult
from shisi.infrastructure.migration.rollback_runner import run as run_rollback


@dataclass
class MigrationStatus:
    v2_table_exists: bool
    legacy_table_exists: bool
    v2_record_count: int
    legacy_record_count: int


class MigrationService:
    def execute(
        self,
        db_path: Path = Path("data/sqlite.db"),
        char_dir: Path = Path("data/characters"),
    ) -> MigrationResult:
        return run_migration(db_path, char_dir)

    def rollback(self, db_path: Path = Path("data/sqlite.db")) -> RollbackResult:
        return run_rollback(db_path)

    def status(self, db_path: Path = Path("data/sqlite.db")) -> MigrationStatus:
        if not db_path.exists():
            return MigrationStatus(
                v2_table_exists=False,
                legacy_table_exists=False,
                v2_record_count=0,
                legacy_record_count=0,
            )

        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            v2_exists = "characters_v2" in tables
            legacy_exists = "characters_legacy" in tables

            v2_count = 0
            if v2_exists:
                row = conn.execute("SELECT COUNT(*) FROM characters_v2").fetchone()
                v2_count = row[0]

            legacy_count = 0
            if legacy_exists:
                row = conn.execute("SELECT COUNT(*) FROM characters_legacy").fetchone()
                legacy_count = row[0]

            return MigrationStatus(
                v2_table_exists=v2_exists,
                legacy_table_exists=legacy_exists,
                v2_record_count=v2_count,
                legacy_record_count=legacy_count,
            )
        finally:
            conn.close()
