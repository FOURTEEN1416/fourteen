"""W10 · 真实备份、隔离恢复与发布失败门禁 — 备份/恢复行为契约。

覆盖任务 1+2：
- 应用数据 manifest 与一致快照（含非 git 的角色卡、向量源、状态与配置）
- SQLite WAL 未 checkpoint 时在线一致备份（.backup API）
- APP_DATABASE_URL 自定义路径优先级（与 api/runtime_config.get_database_url 平价）
- 缺项必须失败或明确 partial（退出码语义：0=complete / 2=partial / 1=failed）
- 恢复只落独立目录，校验 schema / 行数 / 双账号业务行归属后才算通过；拒绝原地覆盖
- dry-run 只读不改（前后文件哈希不变）
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
BACKUP_MANAGER = _PROJECT_ROOT / "deploy" / "backup_manager.py"
RESTORE_MANAGER = _PROJECT_ROOT / "deploy" / "restore_manager.py"

# 双账号（合成数据，生产键形态）
ACCT_A = "100:wxid_aaa@im.wechat"
ACCT_B = "200:wxid_bbb@im.wechat"
KEY_A = "wxid_aaa"
KEY_B = "wxid_bbb"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def backup_mod():
    return _load_module(BACKUP_MANAGER, "w10_backup_manager")


@pytest.fixture()
def restore_mod():
    return _load_module(RESTORE_MANAGER, "w10_restore_manager")


# ────────────────────────── 合成项目夹具 ──────────────────────────


def _mini_users_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, username TEXT);
        CREATE TABLE wechat_channel_sessions (id INTEGER PRIMARY KEY, owner_user_id INTEGER, peer_wxid TEXT, status TEXT);
        INSERT INTO users VALUES (1, 'a@t.local', 'alice'), (2, 'b@t.local', 'bob');
        INSERT INTO wechat_channel_sessions VALUES (1, 1, 'wxid_aaa', 'connected'), (2, 2, 'wxid_bbb', 'connected');
        """
    )
    con.commit()
    con.close()


def _memory_db(path: Path, *, wal: bool = False) -> None:
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT);
        CREATE TABLE user_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_key TEXT, fact TEXT);
        CREATE TABLE user_profile (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_key TEXT, field TEXT, value TEXT);
        CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT, content TEXT, status TEXT);
        """
    )
    con.executemany(
        "INSERT INTO chat_history (session_id, role, content) VALUES (?, ?, ?)",
        [(ACCT_A, "user", "A说1"), (ACCT_A, "assistant", "A收1"), (ACCT_B, "user", "B说1"), (ACCT_B, "user", "B说2")],
    )
    con.executemany(
        "INSERT INTO user_facts (session_id, user_key, fact) VALUES (?, ?, ?)",
        [(ACCT_A, KEY_A, "A的生日是1月1日"), (ACCT_B, KEY_B, "B在上班")],
    )
    con.executemany(
        "INSERT INTO user_profile (session_id, user_key, field, value) VALUES (?, ?, 'birthday', ?)",
        [(ACCT_A, KEY_A, "1月1日"), (ACCT_B, KEY_B, "2月2日")],
    )
    con.executemany(
        "INSERT INTO reminders (session_id, user_id, content, status) VALUES (?, ?, ?, 'active')",
        [(ACCT_A, KEY_A, "A的提醒"), (ACCT_B, KEY_B, "B的提醒")],
    )
    con.commit()
    con.close()


def _agent_plane_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, session_key TEXT, payload TEXT);
        INSERT INTO events (type, session_key, payload) VALUES ('memory_write', '100:wxid_aaa@im.wechat', '{}'),
                                                               ('memory_write', '200:wxid_bbb@im.wechat', '{}');
        """
    )
    con.commit()
    con.close()


def _fill_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def synth_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    (root / "config").mkdir()
    # 应用检出标记（live-root 守卫的判定依据）
    (root / "main.py").write_text("# app entry\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    _mini_users_db(data / "users.db")
    _memory_db(data / "sqlite.db")
    _agent_plane_db(data / "agent_plane.db")
    _fill_file(data / "chroma_db" / "chroma.sqlite3", "chroma-marker")
    _fill_file(root / "config" / "characters" / "card_a.json", '{"id": "card_a", "name": "甲"}')
    _fill_file(root / "config" / "characters" / "card_b.json", '{"id": "card_b", "name": "乙"}')
    _fill_file(data / "knowledge" / "card_a.json", "[]")
    _fill_file(data / "scheduler_config.json", '{"throttle": {}}')
    _fill_file(data / "wechat_state.json", '{"ok": true}')
    (data / "wechat_sessions").mkdir()
    _fill_file(data / "wechat_sessions" / "chan_a.json", '{"session": "a"}')
    (data / "ase_states").mkdir()
    _fill_file(data / "ase_states" / "index.json", "{}")
    return root


def _tree_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _run_backup(root: Path, out_dir: Path, *extra: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("APP_DATABASE_URL", None)
    env.pop("DATABASE_URL", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(BACKUP_MANAGER), "--project-root", str(root), "--output-dir", str(out_dir), *extra],
        capture_output=True, text=True, env=env, timeout=180,
    )


def _latest_backup_dir(out_dir: Path) -> Path:
    runs = sorted(d for d in out_dir.iterdir() if d.is_dir() and d.name.startswith("backup-"))
    assert runs, f"no backup dir created under {out_dir}"
    return runs[-1]


def _restore(backup_dir: Path, target: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESTORE_MANAGER), "--backup-dir", str(backup_dir), "--target", str(target), *extra],
        capture_output=True, text=True, timeout=180,
    )


def _count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


# ────────────────────────── 核心 round-trip ──────────────────────────


class TestFullRoundTrip:
    def test_backup_creates_manifest_with_all_components(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        proc = _run_backup(synth_project, out_dir)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        run_dir = _latest_backup_dir(out_dir)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "complete"
        comps = manifest["components"]
        for name in ("users_db", "memory_db", "agent_plane_db", "vector_store",
                     "character_cards", "knowledge_index", "runtime_state"):
            assert name in comps, f"manifest 缺组件 {name}: {sorted(comps)}"
            assert comps[name]["status"] == "ok", f"{name} 状态={comps[name]['status']}"
        # 非 git 的角色卡/向量源/状态都被覆盖
        assert (run_dir / "character_cards.tar.gz").exists()
        assert (run_dir / "vector_store.tar.gz").exists()
        assert (run_dir / "runtime_state.tar.gz").exists()
        assert (run_dir / "users.db").exists()

    def test_manifest_records_table_and_ownership_counts(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        assert _run_backup(synth_project, out_dir).returncode == 0
        manifest = json.loads((_latest_backup_dir(out_dir) / "manifest.json").read_text(encoding="utf-8"))
        mem = manifest["components"]["memory_db"]
        assert mem["tables"]["chat_history"] == 4
        assert mem["tables"]["user_facts"] == 2
        ownership = mem["ownership"]
        # 双账号各自的行数被分别记录（业务行归属）
        assert ownership["chat_history"][ACCT_A] == 2
        assert ownership["chat_history"][ACCT_B] == 2
        assert ownership["user_facts"][ACCT_A] == 1
        assert ownership["user_facts"][ACCT_B] == 1

    def test_restore_to_isolated_dir_and_verify(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        assert _run_backup(synth_project, out_dir).returncode == 0
        run_dir = _latest_backup_dir(out_dir)
        target = tmp_path / "restore-target"
        proc = _restore(run_dir, target)
        assert proc.returncode == 0, proc.stderr + proc.stdout

        restored_mem = sqlite3.connect(target / "data" / "sqlite.db")
        assert restored_mem.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # 双账号业务行对等
        assert _count(restored_mem, "chat_history") == 4
        rows_a = restored_mem.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = ?", (ACCT_A,)
        ).fetchone()[0]
        rows_b = restored_mem.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = ?", (ACCT_B,)
        ).fetchone()[0]
        assert (rows_a, rows_b) == (2, 2)
        facts = dict(restored_mem.execute("SELECT user_key, COUNT(*) FROM user_facts GROUP BY user_key"))
        assert facts == {KEY_A: 1, KEY_B: 1}
        reminders = dict(restored_mem.execute("SELECT user_id, COUNT(*) FROM reminders GROUP BY user_id"))
        assert reminders == {KEY_A: 1, KEY_B: 1}
        restored_mem.close()

        restored_users = sqlite3.connect(target / "data" / "users.db")
        assert _count(restored_users, "wechat_channel_sessions") == 2  # 通道元数据
        restored_users.close()
        assert _count(sqlite3.connect(target / "data" / "agent_plane.db"), "events") == 2

        # 角色卡与向量源恢复
        assert sorted(p.name for p in (target / "config" / "characters").glob("*.json")) == ["card_a.json", "card_b.json"]
        assert (target / "data" / "chroma_db" / "chroma.sqlite3").exists()
        assert (target / "data" / "scheduler_config.json").exists()
        assert (target / "data" / "wechat_sessions" / "chan_a.json").exists()

    def test_restore_verifier_detects_tampered_row(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        assert _run_backup(synth_project, out_dir).returncode == 0
        run_dir = _latest_backup_dir(out_dir)
        target = tmp_path / "restore-target"
        assert _restore(run_dir, target).returncode == 0
        # 篡改恢复产物的一行 → 再次校验必须失败（退出非 0）
        con = sqlite3.connect(target / "data" / "sqlite.db")
        con.execute("DELETE FROM chat_history WHERE session_id = ?", (ACCT_B,))
        con.commit()
        con.close()
        proc = _restore(run_dir, target, "--verify-only")
        assert proc.returncode != 0
        assert "chat_history" in (proc.stdout + proc.stderr)


# ────────────────────────── 一致性与特殊情形 ──────────────────────────


class TestBackupConsistency:
    def test_wal_not_checkpointed_is_included(self, tmp_path: Path) -> None:
        """WAL 已 commit 未 checkpoint 的数据必须进备份（打开连接模拟在线写入者）。"""
        root = tmp_path / "proj"
        data = root / "data"
        data.mkdir(parents=True)
        (root / "config" / "characters").mkdir(parents=True)
        _fill_file(root / "config" / "characters" / "card_a.json", '{"id": "card_a"}')
        _mini_users_db(data / "users.db")
        _agent_plane_db(data / "agent_plane.db")
        _fill_file(data / "chroma_db" / "chroma.sqlite3", "chroma-marker")
        _fill_file(data / "scheduler_config.json", "{}")
        db = data / "sqlite.db"
        _memory_db(db, wal=True)
        # 打开一个在线写入者，提交但不断开（不触发 checkpoint）
        writer = sqlite3.connect(db)
        writer.execute("INSERT INTO chat_history (session_id, role, content) VALUES (?, 'user', 'WAL热数据')", (ACCT_A,))
        writer.commit()

        out_dir = tmp_path / "backups"
        try:
            proc = _run_backup(root, out_dir)
            assert proc.returncode == 0, proc.stderr + proc.stdout
            run_dir = _latest_backup_dir(out_dir)
            restored = sqlite3.connect(run_dir / "sqlite.db")
            hot = restored.execute(
                "SELECT COUNT(*) FROM chat_history WHERE content = 'WAL热数据'"
            ).fetchone()[0]
            restored.close()
            assert hot == 1, "WAL 中未 checkpoint 的已提交数据丢失"
        finally:
            writer.close()

    def test_custom_app_database_url_takes_priority(self, synth_project: Path, tmp_path: Path) -> None:
        alt = tmp_path / "alt" / "prod_users.db"
        alt.parent.mkdir(parents=True)
        _mini_users_db(alt)
        con = sqlite3.connect(alt)
        con.execute("INSERT INTO users VALUES (3, 'c@t.local', 'carol')")
        con.commit()
        con.close()

        out_dir = tmp_path / "backups"
        proc = _run_backup(synth_project, out_dir, env_extra={"APP_DATABASE_URL": f"sqlite+aiosqlite:///{alt.as_posix()}"})
        assert proc.returncode == 0, proc.stderr + proc.stdout
        manifest = json.loads((_latest_backup_dir(out_dir) / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["components"]["users_db"]["source"] == str(alt), "必须按 APP_DATABASE_URL 取库，而非默认路径"
        restored = sqlite3.connect(_latest_backup_dir(out_dir) / "users.db")
        assert _count(restored, "users") == 3
        restored.close()

    def test_database_url_priority_matches_runtime_config(self, monkeypatch) -> None:
        """备份器的 DB URL 解析必须与 api/runtime_config.get_database_url 平价。"""
        rt_spec = importlib.util.spec_from_file_location(
            "w10_runtime_config", _PROJECT_ROOT / "api" / "runtime_config.py"
        )
        assert rt_spec is not None and rt_spec.loader is not None
        rt = importlib.util.module_from_spec(rt_spec)
        rt_spec.loader.exec_module(rt)
        bm = _load_module(BACKUP_MANAGER, "w10_bm_parity")

        cases: list[dict[str, str]] = [
            {},
            {"DATABASE_URL": "sqlite:///data/db.sqlite"},
            {"APP_DATABASE_URL": "sqlite+aiosqlite:///data/alt.db"},
            {"APP_DATABASE_URL": "sqlite+aiosqlite:///data/alt.db", "DATABASE_URL": "postgresql://u:p@h:5432/d"},
            {"DATABASE_URL": "postgresql://u:p@h:5432/d"},
            {"APP_DATABASE_URL": "  "},
            {"APP_DATABASE_URL": "sqlite:///C:/odd path/db.sqlite"},
        ]
        for env_vals in cases:
            monkeypatch.delenv("APP_DATABASE_URL", raising=False)
            monkeypatch.delenv("DATABASE_URL", raising=False)
            for k, v in env_vals.items():
                monkeypatch.setenv(k, v)
            # 同一 getenv 语义下两者必须给出同一 URL（含同步→异步驱动转换）
            assert bm.resolve_database_url(os.environ.get) == rt.get_database_url(), f"平价失败: {env_vals}"

    def test_missing_vector_dir_marks_partial_exit2(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        data = root / "data"
        data.mkdir(parents=True)
        (root / "config" / "characters").mkdir(parents=True)
        _fill_file(root / "config" / "characters" / "card_a.json", '{"id": "card_a"}')
        _mini_users_db(data / "users.db")
        _memory_db(data / "sqlite.db")
        out_dir = tmp_path / "backups"
        proc = _run_backup(root, out_dir)
        assert proc.returncode == 2, f"缺向量目录应 partial(exit 2)，实际 {proc.returncode}"
        manifest = json.loads((_latest_backup_dir(out_dir) / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "partial"
        assert manifest["components"]["vector_store"]["status"] == "absent"

    def test_required_component_missing_hard_fails(self, tmp_path: Path) -> None:
        root = tmp_path / "proj"
        (root / "data").mkdir(parents=True)
        _memory_db(root / "data" / "sqlite.db")  # 只有 memory，users_db 缺失
        out_dir = tmp_path / "backups"
        proc = _run_backup(root, out_dir)
        assert proc.returncode == 1, "users_db 缺失必须硬失败"
        manifest = json.loads((_latest_backup_dir(out_dir) / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
        assert manifest["components"]["users_db"]["status"] == "absent"

    def test_tar_failure_marks_component_failed_exit_nonzero(self, synth_project: Path, tmp_path: Path, monkeypatch) -> None:
        bm = _load_module(BACKUP_MANAGER, "w10_bm_tarfail")
        out_dir = tmp_path / "backups"

        real_make_tar = bm._make_tar

        def flaky_make_tar(src: Path, dest: Path) -> None:
            if src.name == "chroma_db":
                raise OSError("simulated tar member read failure")
            real_make_tar(src, dest)

        monkeypatch.setattr(bm, "_make_tar", flaky_make_tar)
        code, manifest = bm.run_backup(
            project_root=synth_project, output_dir=out_dir, allow_partial=False, dry_run=False,
        )
        assert code == 1
        assert manifest["components"]["vector_store"]["status"] == "failed"
        assert manifest["status"] == "failed"

    def test_dry_run_mutates_nothing(self, synth_project: Path, tmp_path: Path) -> None:
        before = _tree_hashes(synth_project)
        out_dir = tmp_path / "backups"
        proc = _run_backup(synth_project, out_dir, "--dry-run")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        after = _tree_hashes(synth_project)
        assert before == after, "dry-run 改动了源文件"
        assert not out_dir.exists() or not any(out_dir.iterdir()), "dry-run 不得产出备份文件"
        plan = json.loads(proc.stdout.strip().splitlines()[-1])
        assert plan["dry_run"] is True

    def test_manifest_checksums_match_artifacts(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        assert _run_backup(synth_project, out_dir).returncode == 0
        run_dir = _latest_backup_dir(out_dir)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        for name, comp in manifest["components"].items():
            if comp.get("status") != "ok":
                continue
            artifact = run_dir / comp["artifact"]
            assert artifact.exists(), f"{name} 的产物 {comp['artifact']} 不存在"
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            assert comp["sha256"] == digest, f"{name} sha256 与 manifest 不符"

    def test_no_sqlite3_cli_dependency(self) -> None:
        """根治手段：备份核心用 stdlib 备份 API，不再依赖 sqlite3 CLI 二进制。"""
        src = BACKUP_MANAGER.read_text(encoding="utf-8")
        # 不允许以子进程方式调用 sqlite3 CLI（引号字符串形态）
        assert '"sqlite3"' not in src and "'sqlite3'" not in src
        # 备份必须走 Python 备份 API（在线一致快照）
        assert ".backup(" in src

    def test_test_mode_connection_check_only(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        proc = _run_backup(synth_project, out_dir, "--test")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not out_dir.exists() or not any(out_dir.iterdir()), "--test 不得产出备份"

    def test_retention_cleanup_removes_old_runs(self, synth_project: Path, tmp_path: Path) -> None:
        out_dir = tmp_path / "backups"
        out_dir.mkdir(parents=True)
        old = out_dir / "backup-20000101-000000"
        old.mkdir()
        (old / "manifest.json").write_text("{}", encoding="utf-8")
        # 把旧目录 mtime 拨到 40 天前
        old_ts = old.stat().st_mtime - 40 * 86400
        os.utime(old, (old_ts, old_ts))
        proc = _run_backup(synth_project, out_dir)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert not old.exists(), "超保留期的旧备份目录未被清理"


# ────────────────────────── 恢复守卫 ──────────────────────────


class TestRestoreGuards:
    def _prepare(self, synth_project: Path, tmp_path: Path) -> Path:
        out_dir = tmp_path / "backups"
        assert _run_backup(synth_project, out_dir).returncode == 0
        return _latest_backup_dir(out_dir)

    def test_refuses_live_project_root(self, synth_project: Path, tmp_path: Path) -> None:
        run_dir = self._prepare(synth_project, tmp_path)
        proc = _restore(run_dir, synth_project)  # 原地覆盖生产 = 必须拒绝
        assert proc.returncode != 0
        assert "live" in (proc.stdout + proc.stderr).lower() or "原地" in (proc.stdout + proc.stderr)

    def test_refuses_nonempty_target_without_force(self, synth_project: Path, tmp_path: Path) -> None:
        run_dir = self._prepare(synth_project, tmp_path)
        target = tmp_path / "nonempty"
        target.mkdir()
        (target / "keep.txt").write_text("x", encoding="utf-8")
        proc = _restore(run_dir, target)
        assert proc.returncode != 0

    def test_missing_manifest_fails(self, synth_project: Path, tmp_path: Path) -> None:
        run_dir = self._prepare(synth_project, tmp_path)
        (run_dir / "manifest.json").unlink()
        proc = _restore(run_dir, tmp_path / "t")
        assert proc.returncode != 0

    def test_bad_db_in_backup_fails_verification(self, synth_project: Path, tmp_path: Path) -> None:
        run_dir = self._prepare(synth_project, tmp_path)
        # 把备份内的 memory 库换成一个损坏文件
        (run_dir / "sqlite.db").write_bytes(b"this is not a database" * 32)
        proc = _restore(run_dir, tmp_path / "t2")
        assert proc.returncode != 0
        assert "integrity" in (proc.stdout + proc.stderr).lower() or "损坏" in (proc.stdout + proc.stderr)


# ────────────────────────── backup.sh 包装层 ──────────────────────────


class TestBackupShellWrapper:
    def test_backup_sh_delegates_to_manager(self) -> None:
        src = (_PROJECT_ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")
        assert "backup_manager.py" in src, "backup.sh 必须委托 backup_manager.py（唯一样板）"

    def test_shell_scripts_syntax_ok(self) -> None:
        for name in ("backup.sh", "restore.sh", "setup.sh", "start.sh", "deploy.sh"):
            path = _PROJECT_ROOT / "deploy" / name
            if not path.exists():
                continue
            # 仓库相对路径 + cwd 锚定：绝对反斜杠路径在 WSL bash 启动器（WindowsApps
            # \bash.exe）下会被吞反斜杠（D:\... → D:Desktop...）报 127；相对路径对
            # Git Bash / WSL / Linux CI 通吃。
            proc = subprocess.run(
                ["bash", "-n", f"deploy/{name}"],
                capture_output=True, text=True, timeout=30, cwd=str(_PROJECT_ROOT),
            )
            assert proc.returncode == 0, f"{name} 语法错误: {proc.stderr}"
