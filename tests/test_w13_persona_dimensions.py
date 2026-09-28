"""W13 · D11 五维度真落库 + trigger_message 隐私化 + 删号级联（红测先行）。

缺陷（任务书 D11 b+c，2026-09-28 裁决）：
1. ``persona_extractor/fusion.py`` 算出的 hexaco / dark_triad / mental_health /
   liwc / cognitive 五维度经 ``save_persona`` 恒报成功，但 ``user_persona``
   表没有这些列——五维度从未落库（只活在计算进程内存，多 worker 各自为政、
   重启全丢）；``/api/psych/mental-health`` 读回恒 None。
2. ``user_persona_snapshots.trigger_message`` 存了用户原话前 200 字（无 TTL）。
3. 删号（管理员删除 / 自服务注销同走生命周期作业）不触达这两张表——
   删号后画像与原话残留。

验收对照（任务书）：
- 写读 round-trip（五维度存取 + 跨实例回填 + to_dict 带出）；
- trigger_message 断言不含原文（新写为哈希 + 长度；存量迁移后为空）；
- 删号级联行数断言（目标 owner 删光、他人与无主 scope 保留）+ 回执计数；
- 迁移幂等（连跑两次列集合 / 行数 / applied 数不变）。

全部使用临时库，不触 ``data/``。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from persona_extractor.models import OceanTraits, UserPersona, UserPersonaSnapshot
from persona_extractor.persona_bank import UserPersonaBank
from shisi.migrations import run_migrations

SCOPE = "十四:user4:o9cq805ifq"
RAW_TRIGGER = "今晚我不想加班，心里很烦，别问我为什么"

DIMS = {
    "hexaco": {"honesty_humility": 0.4, "emotionality": 0.6, "extraversion": 0.5,
               "agreeableness": 0.7, "conscientiousness": 0.3, "openness": 0.8},
    "dark_triad": {"narcissism": 0.2, "machavellianism": 0.1, "psychopathy": 0.05},
    "mental_health": {"overall_risk": "low", "depression": {"total_score": 0.07},
                      "caveat": "关键词命中口径，非临床量表"},
    "liwc": {"analytic": 42.1, "clout": 55.0, "affect": 18.3},
    "cognitive": {"distortions": [{"kind": "catastrophizing", "hits": 2}],
                  "total_hits": 2},
}


def _bank(db_path: Path) -> UserPersonaBank:
    return UserPersonaBank(str(db_path))


def _close(bank: UserPersonaBank) -> None:
    conn = bank.get_connection()
    if conn is not None:
        conn.close()


def _persona(scope: str, **dims) -> UserPersona:
    payload = {
        "user_id": scope,
        "ocean": OceanTraits(openness=0.66),
        "snapshot_count": 3,
        "first_seen": "2026-09-20T10:00:00",
        "last_updated": "2026-09-28T10:00:00",
        **dims,
    }
    return UserPersona(**payload)


def _snap(trigger: str = RAW_TRIGGER) -> UserPersonaSnapshot:
    return UserPersonaSnapshot(
        timestamp="2026-09-28T10:00:00",
        trigger_message=trigger,
        source="rule",
    )


def _columns(db_path: Path, table: str) -> set[str]:
    with closing(sqlite3.connect(str(db_path))) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _legacy_schema(db_path: Path) -> None:
    """手工构造**旧结构**库（无五维度列），模拟存量生产 data/sqlite.db。"""
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_persona (
                user_id TEXT PRIMARY KEY,
                ocean_json TEXT NOT NULL,
                pad_json TEXT NOT NULL,
                style_json TEXT NOT NULL,
                snapshot_count INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_updated TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                ocean_json TEXT NOT NULL,
                pad_json TEXT NOT NULL,
                style_json TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT '',
                trigger_message TEXT DEFAULT ''
            )"""
        )
        conn.commit()


def _insert_snapshot(db_path: Path, scope: str, trigger: str) -> None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            """INSERT INTO user_persona_snapshots
               (user_id, timestamp, ocean_json, pad_json, style_json,
                confidence, source, trigger_message)
               VALUES (?, '2026-09-01T08:00:00', '{}', '{}', '{}', 0.5, 'rule', ?)""",
            (scope, trigger),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────
# 1. 五维度写读 round-trip（真写 + 跨实例回填 + to_dict 带出）
# ─────────────────────────────────────────────────────────────


def test_dimensions_persisted_as_json_columns(tmp_path):
    """save_persona 后五列在库里是可解析的 JSON，不是 NULL。"""
    db = tmp_path / "persona.sqlite"
    bank = _bank(db)
    assert bank.save_persona(_persona(SCOPE, **DIMS)) is True
    _close(bank)

    with closing(sqlite3.connect(str(db))) as conn:
        row = conn.execute(
            "SELECT hexaco_json, dark_triad_json, mental_health_json, "
            "liwc_json, cognitive_json FROM user_persona WHERE user_id = ?",
            (SCOPE,),
        ).fetchone()
    assert row is not None, "persona 行不存在"
    stored = dict(zip(DIMS, row, strict=True))
    for name, raw in stored.items():
        assert raw is not None, f"{name} 列未落库（恒 NULL = 缺陷未修）"
        assert json.loads(raw) == DIMS[name], f"{name} 落库内容与写入不一致"


def test_dimension_round_trip_cross_instance_backfill(tmp_path):
    """新 bank 实例（另一进程形态）从库回填五维度；to_dict 带出。"""
    db = tmp_path / "persona.sqlite"
    bank = _bank(db)
    assert bank.save_persona(_persona(SCOPE, **DIMS)) is True
    _close(bank)

    bank2 = _bank(db)
    try:
        loaded = bank2._load_from_db(SCOPE)
        assert loaded is not None, "跨实例 _load_from_db 未回填"
        for name in DIMS:
            assert getattr(loaded, name) == DIMS[name], f"{name} 回填缺失或值不符"
            d = loaded.to_dict()
            assert d.get(name) == DIMS[name], f"to_dict 未带出 {name}"
        # get_persona 缓存路径同样带维度
        cached = bank2.get_persona(SCOPE)
        assert cached is not None and cached.hexaco == DIMS["hexaco"]
    finally:
        _close(bank2)


def test_dimensions_none_round_trip(tmp_path):
    """无五维度的画像照常写读，五字段保持 None（不写 'null' 串、不炸）。"""
    db = tmp_path / "persona.sqlite"
    bank = _bank(db)
    assert bank.save_persona(_persona(SCOPE)) is True
    loaded = bank._load_from_db(SCOPE)
    _close(bank)
    assert loaded is not None
    for name in DIMS:
        assert getattr(loaded, name) is None


# ─────────────────────────────────────────────────────────────
# 2. trigger_message 隐私化（新写为哈希 + 长度）
# ─────────────────────────────────────────────────────────────


def test_new_snapshot_stores_hash_not_text(tmp_path):
    db = tmp_path / "persona.sqlite"
    bank = _bank(db)
    try:
        assert bank.add_snapshot(_snap(), SCOPE) is True
        snaps = bank.get_recent_snapshots(SCOPE)
        assert snaps, "快照应可读回"
        stored = snaps[0].trigger_message
        assert "今晚我不想加班" not in stored, "原文残留"
        assert RAW_TRIGGER not in stored
        assert stored.startswith("sha256:"), f"新写入应为哈希格式，实得 {stored!r}"
        assert f"len:{len(RAW_TRIGGER)}" in stored
    finally:
        _close(bank)


def test_long_trigger_hash_covers_archived_window(tmp_path):
    """超 200 字原话：哈希与长度基于存档窗口（前 200 字），不存任何原文。"""
    db = tmp_path / "persona.sqlite"
    raw = "机" * 300
    bank = _bank(db)
    try:
        assert bank.add_snapshot(_snap(trigger=raw), SCOPE) is True
        stored = bank.get_recent_snapshots(SCOPE)[0].trigger_message
        assert "机" not in stored
        assert "len:200" in stored
    finally:
        _close(bank)


def test_empty_trigger_stays_empty(tmp_path):
    db = tmp_path / "persona.sqlite"
    bank = _bank(db)
    try:
        assert bank.add_snapshot(_snap(trigger=""), SCOPE) is True
        assert bank.get_recent_snapshots(SCOPE)[0].trigger_message == ""
    finally:
        _close(bank)


# ─────────────────────────────────────────────────────────────
# 3. 迁移：补列 + 存量原话清空 + 幂等
# ─────────────────────────────────────────────────────────────


def test_migration_adds_dimension_columns_to_legacy_db(tmp_path):
    db = tmp_path / "persona.sqlite"
    _legacy_schema(db)
    run_migrations(db)
    cols = _columns(db, "user_persona")
    for col in ("hexaco_json", "dark_triad_json", "mental_health_json",
                "liwc_json", "cognitive_json"):
        assert col in cols, f"迁移未补列 {col}"


def test_migration_purges_legacy_trigger_texts_keeps_hashed(tmp_path):
    db = tmp_path / "persona.sqlite"
    _legacy_schema(db)
    _insert_snapshot(db, "legacy_plain", "我的原话ABC请勿留存")
    _insert_snapshot(db, "already_hashed", "sha256:abcdef0123456789|len:6")
    _insert_snapshot(db, "empty_ok", "")

    run_migrations(db)

    with closing(sqlite3.connect(str(db))) as conn:
        rows = dict(
            conn.execute(
                "SELECT user_id, trigger_message FROM user_persona_snapshots"
            ).fetchall()
        )
    assert rows["legacy_plain"] == "", "存量原话未被迁移清空"
    assert rows["already_hashed"].startswith("sha256:"), "已是哈希格式的行被误清"
    assert rows["empty_ok"] == ""

    # 幂等：连跑两次，清空结果不变、行数不变
    run_migrations(db)
    with closing(sqlite3.connect(str(db))) as conn:
        rows2 = dict(
            conn.execute(
                "SELECT user_id, trigger_message FROM user_persona_snapshots"
            ).fetchall()
        )
    assert rows2 == rows


def test_migration_idempotent_columns_rows_and_applied(tmp_path):
    db = tmp_path / "persona.sqlite"
    _legacy_schema(db)
    with closing(sqlite3.connect(str(db))) as conn:
        conn.execute(
            "INSERT INTO user_persona (user_id, ocean_json, pad_json, style_json,"
            " snapshot_count, first_seen, last_updated)"
            " VALUES ('s1', '{}', '{}', '{}', 1, 't', 't')"
        )
        conn.commit()

    applied1 = run_migrations(db)
    cols1 = _columns(db, "user_persona")
    with closing(sqlite3.connect(str(db))) as conn:
        n1 = conn.execute("SELECT COUNT(*) FROM user_persona").fetchone()[0]

    applied2 = run_migrations(db)
    cols2 = _columns(db, "user_persona")
    with closing(sqlite3.connect(str(db))) as conn:
        n2 = conn.execute("SELECT COUNT(*) FROM user_persona").fetchone()[0]

    assert cols1 == cols2, "二次迁移改变了列集合（非幂等）"
    assert n1 == n2 == 1, "二次迁移改变了数据行数"
    assert len(applied1) == len(applied2), "二次迁移 applied 数变化"


def test_migration_on_fresh_db_creates_full_persona_schema(tmp_path):
    db = tmp_path / "fresh.sqlite"
    run_migrations(db)
    cols = _columns(db, "user_persona")
    assert {"user_id", "ocean_json", "pad_json", "style_json", "snapshot_count",
            "first_seen", "last_updated", "hexaco_json", "dark_triad_json",
            "mental_health_json", "liwc_json", "cognitive_json"} <= cols
    assert "trigger_message" in _columns(db, "user_persona_snapshots")


# ─────────────────────────────────────────────────────────────
# 4. 删号级联（生命周期作业 persona_profile step）
# ─────────────────────────────────────────────────────────────

UID_A, UID_B = 7, 9
SCOPE_A_WX = f"十四:{UID_A}:peerA@im.wechat"
SCOPE_A_WEB = f"十四:{UID_A}:web:abcd1234"
SCOPE_B = f"十四:{UID_B}:peerB@im.wechat"
SCOPE_UNOWNED = "default"


@pytest.fixture
def cascade_env(tmp_path, monkeypatch):
    """users.db（两账号）+ persona 库（A 两形态 scope / B / 无主 default）。"""
    from api import lifecycle as lifecycle_mod
    from api.database import Base, User

    root = tmp_path / "w13cascade"
    root.mkdir()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(root / 'users.db').as_posix()}"
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed():
        async with maker() as db:
            db.add(User(id=UID_A, email="a@example.com", username="a",
                        hashed_password="x" * 60))
            db.add(User(id=UID_B, email="b@example.com", username="b",
                        hashed_password="x" * 60))
            await db.commit()

    asyncio.run(_create_all())
    asyncio.run(_seed())

    for d in ("chars", "kn", "chroma"):
        (root / d).mkdir()

    persona_db = root / "sqlite.db"
    bank = _bank(persona_db)
    for scope in (SCOPE_A_WX, SCOPE_A_WEB, SCOPE_B, SCOPE_UNOWNED):
        assert bank.save_persona(_persona(scope)) is True
        assert bank.add_snapshot(_snap(), scope) is True
    _close(bank)

    monkeypatch.setattr(lifecycle_mod, "_default_db_factory", lambda: maker)
    monkeypatch.setattr(lifecycle_mod, "job_root", lambda: root / "jobs")

    yield {
        "root": root,
        "persona_db": persona_db,
        "empty_agent_db": root / "agent.db",
    }
    asyncio.run(engine.dispose())


async def test_delete_account_cascades_persona_scopes(cascade_env):
    from api import lifecycle

    env = cascade_env
    job = await lifecycle.delete_account_everywhere(
        UID_A,
        characters_dir=env["root"] / "chars",
        knowledge_dir=env["root"] / "kn",
        sqlite_db=env["persona_db"],
        agent_db=env["empty_agent_db"],
        chroma_dir=env["root"] / "chroma",
    )
    assert job["completed"] is True, json.dumps(job, ensure_ascii=False)[:2000]

    receipt = job["steps"]["persona_profile"]["receipt"]
    assert receipt["user_persona"] == 2, "回执计数：A 的两个 scope 应各删 1 行"
    assert receipt["user_persona_snapshots"] == 2

    with closing(sqlite3.connect(str(env["persona_db"]))) as conn:
        persona_left = {
            r[0] for r in conn.execute("SELECT user_id FROM user_persona").fetchall()
        }
        snap_left = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT user_id FROM user_persona_snapshots"
            ).fetchall()
        }
    assert SCOPE_A_WX not in persona_left, "A 的微信形态 scope 残留"
    assert SCOPE_A_WEB not in persona_left, "A 的 web 形态 scope 残留"
    assert SCOPE_B in persona_left, "他人 scope 被误删"
    assert SCOPE_UNOWNED in persona_left, "无主 scope 被误删"
    assert snap_left == {SCOPE_B, SCOPE_UNOWNED}, f"快照归属残留/误删：{snap_left}"
