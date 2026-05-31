"""shisi 数据库迁移测试 — 12张表创建 + 幂等性验证"""

from pathlib import Path

import pytest

from shisi.migrations import (
    get_table_names,
    run_migrations,
    verify_tables,
)


class TestTableNames:
    """get_table_names() 返回所有预期表名"""

    EXPECTED_TABLES = {
        "characters", "affinity_records", "affinity_unlocks", "affinity_audit",
        "emotion_stage_state", "stickers", "character_stickers",
        "vital_signs_state", "memory_favorites", "memory_recycle_bin",
        "shisi_schema_version", "characters_v2",
    }

    def test_returns_all_tables(self):
        names = set(get_table_names())
        assert names == self.EXPECTED_TABLES, (
            f"Missing: {self.EXPECTED_TABLES - names}, "
            f"Unexpected: {names - self.EXPECTED_TABLES}"
        )

    def test_no_duplicates(self):
        names = get_table_names()
        assert len(names) == len(set(names)), "Duplicate table names"


class TestRunMigrations:
    """run_migrations() 建表 + 幂等性"""

    def test_creates_all_tables(self, tmp_db):
        run_migrations(tmp_db)
        result = verify_tables(tmp_db)
        for table, exists in result.items():
            assert exists, f"Table '{table}' was not created"

    def test_idempotent(self, tmp_db):
        """二次运行不抛异常"""
        run_migrations(tmp_db)
        run_migrations(tmp_db)  # no error
        result = verify_tables(tmp_db)
        assert all(result.values()), "Tables missing after 2nd migration"

    def test_uses_correct_db_path(self, tmp_db):
        applied = run_migrations(tmp_db)
        assert len(applied) > 0
        assert Path(tmp_db).exists(), "DB file not created"

    def test_creates_indexes(self, tmp_db):
        run_migrations(tmp_db)
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
            expected_prefixes = {
                "idx_affinity_records_cid",
                "idx_affinity_audit_cid",
                "idx_affinity_unlocks_cid",
                "idx_stickers_category",
                "idx_memory_fav_cid",
                "idx_memory_recycle_cid",
                "idx_chars_v2_active",
                "idx_chars_v2_updated",
                "idx_chars_v2_name",
            }
            for prefix in expected_prefixes:
                assert any(idx.startswith(prefix) for idx in indexes), \
                    f"Index starting with '{prefix}' not found"
        finally:
            conn.close()

    def test_schema_version_recorded(self, tmp_db):
        run_migrations(tmp_db)
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        try:
            row = conn.execute(
                "SELECT value FROM shisi_schema_version WHERE key='version'"
            ).fetchone()
            assert row is not None
            assert row[0] == "1.0"
        finally:
            conn.close()


class TestVerifyTables:
    """verify_tables() 检测表存在性"""

    def test_all_true_after_migration(self, tmp_db):
        run_migrations(tmp_db)
        result = verify_tables(tmp_db)
        assert all(result.values())

    def test_all_false_on_empty_db(self, tmp_db):
        """空数据库 → 全部 false"""
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.close()
        result = verify_tables(tmp_db)
        assert all(not v for v in result.values())

    def test_partial_tables(self, tmp_db):
        """部分表存在时能正确检测"""
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        conn.execute("CREATE TABLE characters (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        result = verify_tables(tmp_db)
        assert result["characters"] is True
        assert result["affinity_records"] is False
