"""shisi 数据库迁移测试 — 15张表创建 + 幂等性验证（W13 起纳管 user_persona 两表）"""

from pathlib import Path

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
        "vital_signs_state", "memory_favorites", "memory_forwards", "memory_recycle_bin",
        "shisi_schema_version", "characters_v2",
        "user_persona", "user_persona_snapshots",
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


class TestConcurrentRunMigrations:
    """多 worker 并发启动下 run_migrations 必须安全。

    生产实锤（2026-09-28 部署 ffa6d68→9141afa）：uvicorn 4 worker 同时经
    `setup_shisi(run_migrate=True)` 调 run_migrations，两个 worker 在
    `_migrate_user_persona_dimensions` 的 PRAGMA 读列与 ALTER 之间被并发
    worker 插队提交 → `sqlite3.OperationalError: duplicate column name:
    hexaco_json`；宽 except 吞掉后该 worker 的 /api/shisi/* 整组缺失。
    同族失败还有 `database is locked`（写锁互踩）。
    旧 schema = user_persona 无五维度列（W13 生产存量库真实形状）。
    """

    OLD_USER_PERSONA_DDL = """
        CREATE TABLE user_persona (
            user_id TEXT PRIMARY KEY,
            ocean_json TEXT NOT NULL,
            pad_json TEXT NOT NULL,
            style_json TEXT NOT NULL,
            snapshot_count INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL,
            last_updated TEXT NOT NULL
        )
    """

    def _seed_old_schema_db(self, path: str) -> None:
        import sqlite3

        conn = sqlite3.connect(path)
        conn.execute(self.OLD_USER_PERSONA_DDL)
        conn.commit()
        conn.close()

    def test_concurrent_workers_all_succeed(self, tmp_path):
        """旧 schema 库上 4 路并发 run_migrations：零异常，终态列全。"""
        import sqlite3
        import threading

        from persona_extractor.persona_bank import PERSONA_DIMENSION_COLUMNS

        for round_no in range(3):
            db = str(tmp_path / f"race_{round_no}.db")
            self._seed_old_schema_db(db)
            errors: list[str] = []
            barrier = threading.Barrier(4)

            def worker(
                _barrier: threading.Barrier = barrier,
                _db: str = db,
                _errors: list[str] = errors,
            ) -> None:
                try:
                    _barrier.wait()
                    run_migrations(_db)
                except Exception as e:  # noqa: BLE001
                    _errors.append(f"{type(e).__name__}: {e}")

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors, f"round {round_no}: 并发迁移失败 {errors}"
            conn = sqlite3.connect(db)
            try:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(user_persona)")}
                assert set(PERSONA_DIMENSION_COLUMNS) <= cols, (
                    f"round {round_no}: 五维度列未补齐: {PERSONA_DIMENSION_COLUMNS}"
                )
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                conn.close()

    def test_already_migrated_db_concurrent_rerun(self, tmp_path):
        """全列库并发重跑（重启场景）：幂等且零异常。"""
        import threading

        db = str(tmp_path / "rerun.db")
        run_migrations(db)
        errors: list[str] = []
        barrier = threading.Barrier(4)

        def worker() -> None:
            try:
                barrier.wait()
                run_migrations(db)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"全列库并发重跑失败 {errors}"

    def test_run_migrations_serialization_guard(self):
        """结构守卫：run_migrations 必须整段包单一写事务（BEGIN IMMEDIATE +
        COMMIT/ROLLBACK），连接须 isolation_level=None 且带 busy 超时——
        失去串行化即重现 2026-09-28 生产 duplicate column 竞态。"""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "shisi" / "migrations.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_migrations"
        )
        body = ast.get_source_segment(src, fn)
        assert body is not None
        assert '"BEGIN IMMEDIATE"' in body, "迁移必须整段包 BEGIN IMMEDIATE 写事务"
        assert '"COMMIT"' in body and '"ROLLBACK"' in body, "提交/回滚必须成对"
        assert "isolation_level=None" in body, "手工事务须关闭隐式隔离"
        assert "timeout=" in body, "连接须带 busy 等待超时"
