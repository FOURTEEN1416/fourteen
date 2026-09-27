"""W9 维护脚本纯计划对象化（C 缺陷）：dry-run / audit-only 不得有任何写副作用。

钉住：
1. ax_clean_profiles --dry-run：库文件字节不变、不建表、不 seed 账本、不写报告；
   库文件缺失/缺表 → 只报不建（且不得创建库文件本身）；
2. ax_clean_profiles --apply：真实清洗 + seed + 报告；
3. deploy/seed --audit-only：不 mkdir、不同步角色、不写 _index.json；
   正常构建路径保持 W10 缺卡阻断门禁。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
# ax_clean_profiles
# ─────────────────────────────────────────────────────────────


@pytest.fixture()
def dirty_users_db(tmp_path):
    """带垃圾画像/事实的 users.db（真实 schema 同构）。"""
    db = tmp_path / "users.db"
    with closing(sqlite3.connect(str(db))) as conn:
        conn.executescript(
            """
            CREATE TABLE user_profile (
                user_key TEXT PRIMARY KEY, nickname TEXT DEFAULT '',
                birthday TEXT DEFAULT '', occupation TEXT DEFAULT '',
                location TEXT DEFAULT '', preferences TEXT DEFAULT '[]',
                commitments TEXT DEFAULT '[]', notes TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'general',
                confidence REAL NOT NULL DEFAULT 0.5, source TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                access_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO user_profile(user_key, nickname, birthday, occupation, preferences)
            VALUES ('1:a@im.wechat', '这是一个超过十二个字的长昵称', '不知道', '无',
                    '["叫我", "喜欢猫", ""]');
            INSERT INTO user_facts(fact, user_key) VALUES ('叫我', '1:a@im.wechat');
            INSERT INTO user_facts(fact, user_key) VALUES ('喜欢猫', '1:a@im.wechat');
            """
        )
        conn.commit()
    return db


def _file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_ax_dry_run_has_zero_write_side_effects(dirty_users_db, tmp_path, monkeypatch):
    import scripts.ax_clean_profiles as ax

    agent_db = tmp_path / "agent_plane.db"
    before = _file_hash(dirty_users_db)
    monkeypatch.setattr(ax, "REPORT_PATH", tmp_path / "report.json")

    rc = ax.main_with_args(
        users_db=dirty_users_db, agent_db=agent_db, apply=False, dry_run=True
    )

    assert rc == 0
    assert _file_hash(dirty_users_db) == before, "dry-run 修改了库文件"
    assert not agent_db.exists(), "dry-run 创建/写入了账本库"
    assert not (tmp_path / "report.json").exists(), "dry-run 写了报告文件"


def test_ax_dry_run_missing_db_reports_without_creating(tmp_path, monkeypatch):
    import scripts.ax_clean_profiles as ax

    missing = tmp_path / "nope.db"
    monkeypatch.setattr(ax, "REPORT_PATH", tmp_path / "report.json")
    rc = ax.main_with_args(
        users_db=missing, agent_db=tmp_path / "agent.db", apply=False, dry_run=True
    )
    assert rc == 0
    assert not missing.exists(), "dry-run 创建了缺失的库文件"
    assert not (tmp_path / "report.json").exists()


def test_ax_dry_run_missing_table_reports_without_schema_init(tmp_path, monkeypatch):
    """缺表 + dry-run：只报错，不初始化 schema（旧行为会在 dry 下建表）。"""
    import scripts.ax_clean_profiles as ax

    db = tmp_path / "bare.db"
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute("CREATE TABLE other(x)")
        conn.commit()
    before = _file_hash(db)
    monkeypatch.setattr(ax, "REPORT_PATH", tmp_path / "report.json")

    ax.main_with_args(
        users_db=db, agent_db=tmp_path / "agent.db", apply=False, dry_run=True
    )
    assert _file_hash(db) == before, "dry-run 缺表时改写了库文件（schema 初始化泄漏）"
    # 也不得新建 user_profile 表
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "user_profile" not in tables


def test_ax_apply_cleans_and_seeds_and_reports(dirty_users_db, tmp_path, monkeypatch):
    import scripts.ax_clean_profiles as ax

    agent_db = tmp_path / "agent_plane.db"
    monkeypatch.setattr(ax, "REPORT_PATH", tmp_path / "report.json")
    rc = ax.main_with_args(
        users_db=dirty_users_db, agent_db=agent_db, apply=True, dry_run=False
    )
    assert rc == 0
    with closing(sqlite3.connect(str(dirty_users_db))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_key='1:a@im.wechat'"
        ).fetchone()
        assert row["nickname"] == "", "长昵称未被清空"
        assert row["birthday"] == "" and row["occupation"] == ""
        prefs = json.loads(row["preferences"])
        assert prefs == ["喜欢猫"]
        garbage = conn.execute(
            "SELECT status FROM user_facts WHERE fact='叫我'"
        ).fetchone()
        assert garbage["status"] == "archived"
        keep = conn.execute(
            "SELECT status FROM user_facts WHERE fact='喜欢猫'"
        ).fetchone()
        assert keep["status"] == "active"
    assert (tmp_path / "report.json").exists(), "--apply 未写报告"
    # seed：账本库被创建且有画像基线事件
    assert agent_db.exists()


# ─────────────────────────────────────────────────────────────
# deploy/seed.py — audit-only 纯只读
# ─────────────────────────────────────────────────────────────


def test_seed_audit_only_is_read_only(tmp_path, monkeypatch, capsys):
    import deploy.seed as seed_mod

    chars = tmp_path / "characters"
    presets = tmp_path / "presets"
    monkeypatch.setattr(seed_mod, "CHARS_DIR", chars)
    monkeypatch.setattr(seed_mod, "PRESETS_DIR", presets)

    import sys
    import types

    def _boom(*a, **k):
        raise AssertionError("audit-only 不得执行角色文件同步")

    stub = types.ModuleType("scripts.sync_character_files")
    stub.main = _boom
    monkeypatch.setitem(sys.modules, "scripts.sync_character_files", stub)

    # 缺卡 → 审计结论非 0（W10 门禁兼容；判定本身零写入）
    with pytest.raises(SystemExit) as exc:
        seed_mod.main_with_args(audit_only=True, rebuild=False, verify=False)
    assert exc.value.code != 0
    assert not presets.exists(), "audit-only 创建了 presets 目录"
    assert not (presets / "_index.json").exists()
    assert not chars.exists() or not any(chars.glob("*.json")), "audit-only 写了角色目录"

    # 有卡 → 审计通过且零写入
    chars.mkdir(parents=True, exist_ok=True)
    (chars / "demo.json").write_text("{}", encoding="utf-8")
    rc = seed_mod.main_with_args(audit_only=True, rebuild=False, verify=False)
    assert rc == 0
    assert not (presets / "_index.json").exists(), "audit-only 写了预设索引"


def test_seed_build_writes_preset_index_and_blocks_on_missing_cards(
    tmp_path, monkeypatch
):
    import deploy.seed as seed_mod

    chars = tmp_path / "characters"
    presets = tmp_path / "presets"
    chars.mkdir()
    presets.mkdir()
    monkeypatch.setattr(seed_mod, "CHARS_DIR", chars)
    monkeypatch.setattr(seed_mod, "PRESETS_DIR", presets)

    import sys
    import types

    def _boom(*a, **k):
        raise AssertionError("测试路径不执行真实同步")

    stub = types.ModuleType("scripts.sync_character_files")
    stub.main = _boom
    monkeypatch.setitem(sys.modules, "scripts.sync_character_files", stub)
    monkeypatch.setattr(seed_mod, "build_knowledge_indexes", lambda rebuild=False: ([], 0.0))

    # 缺卡 → 构建路径阻断（W10 门禁保持）
    with pytest.raises(SystemExit):
        seed_mod.main_with_args(audit_only=False, rebuild=False, verify=False)

    # 有卡 → 构建路径写 _index.json
    (chars / "demo.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "演示",
                "description": "d" * 10,
                "tags": ["t"],
                "first_mes": "hi",
            }
        ),
        encoding="utf-8",
    )
    rc = seed_mod.main_with_args(audit_only=False, rebuild=False, verify=False)
    assert rc == 0
    assert (presets / "_index.json").exists()
