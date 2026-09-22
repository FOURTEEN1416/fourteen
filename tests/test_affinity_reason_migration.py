"""v1.38 遗留①收口：好感度审计镜像旧刻度标记的启动迁移回归（2026-09-22）。

根因：df59752~1766b2e 之间写侧以旧标记 `user_scheduler_persist`（points 刻度
判据）写过 shisi 刻度的行，读侧回放按 points 误换算一次（50→10）。
启动迁移 `migrate_affinity_mirror_reason` 把存量旧标记统一转为新标记（直取），
必须幂等、只动旧标记行、且在 AffinityEnhancer 构造（回放）之前生效。
"""

from __future__ import annotations

import sqlite3

import pytest

from shisi.affinity import enhancer as enh_mod
from shisi.api.registry import migrate_affinity_mirror_reason
from shisi.migrations import run_migrations


def _seed(db, rows):
    """rows: (character_id, new_value, reason)；建真表后直插镜像行。"""
    run_migrations(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO affinity_records (character_id, old_value, new_value, delta, reason) "
            "VALUES (?, 0, ?, 0, ?)",
            rows,
        )
        conn.commit()


def _reasons(db):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT character_id, reason FROM affinity_records ORDER BY id"
        ).fetchall()


def _mk_enhancer(db, tmp_path, monkeypatch):
    from utils import affinity_state

    monkeypatch.setattr(affinity_state, "_PATH", tmp_path / "affinity_state.json")
    return enh_mod.AffinityEnhancer(db_path=db)


def test_migration_converts_old_marker_and_replay_stops_converting(tmp_path, monkeypatch):
    """预置混刻度行 → 迁移 → 全部转 shisi 标记，回放不再 ÷5。

    对照先行：迁移**前**旧标记行 50.0 被回放换算成 points_to_shisi(50)（=10），
    钉住"误换算"是真实存在的现状，而非纸面主张。
    """
    db = tmp_path / "audit.db"
    _seed(db, [
        ("u1::c1", 50.0, enh_mod.MIRROR_REASON_POINTS),
        ("u2::c1", 40.0, enh_mod.MIRROR_REASON_SHISI),
        ("u3::c1", 60.0, None),
    ])

    before = _mk_enhancer(db, tmp_path, monkeypatch)
    mis_read = enh_mod._scale_points_to_shisi(50.0, before._min, before._max)
    assert before._values["u1::c1"] == pytest.approx(mis_read), (
        f"迁移前旧标记行应被按 points 误换算（50→{mis_read}），此对照不成立则本用例失去意义"
    )

    n = migrate_affinity_mirror_reason(db)
    assert n == 1, "只有 1 行旧标记行，迁移须报真实行数"
    assert _reasons(db) == [
        ("u1::c1", enh_mod.MIRROR_REASON_SHISI),
        ("u2::c1", enh_mod.MIRROR_REASON_SHISI),
        ("u3::c1", None),
    ], "迁移只许改旧标记行；新标记与无 reason 裸行不得被波及"

    after = _mk_enhancer(db, tmp_path, monkeypatch)
    assert after._values["u1::c1"] == pytest.approx(50.0), "迁移后回放直取 shisi 值，不再换算"
    assert after._values["u2::c1"] == pytest.approx(40.0)
    assert after._values["u3::c1"] == pytest.approx(60.0)


def test_migration_idempotent_second_run_noop(tmp_path):
    """幂等：二次启动 0 行生效、不报错、值不变。"""
    db = tmp_path / "audit.db"
    _seed(db, [
        ("u1::c1", 50.0, enh_mod.MIRROR_REASON_POINTS),
        ("u2::c1", 30.0, enh_mod.MIRROR_REASON_POINTS),
    ])
    assert migrate_affinity_mirror_reason(db) == 2
    snap = _reasons(db)
    assert migrate_affinity_mirror_reason(db) == 0
    assert _reasons(db) == snap


def test_migration_tolerates_missing_table(tmp_path):
    """表不存在（全新库未跑建表迁移）→ 告警降级返回 0，不抛不阻塞启动。"""
    assert migrate_affinity_mirror_reason(tmp_path / "fresh.db") == 0


def test_setup_shisi_migrates_before_enhancer_replay(tmp_path, monkeypatch):
    """启动接线：setup_shisi 构造 AffinityEnhancer 时旧标记行必须**已被迁移**。

    行为断言：以 spy 子类在 enhancer 构造时刻查表——此刻残留旧标记行数须为 0
    （若迁移误挂在构造之后，回放会先按 points 换算，此断言转红）。
    """
    from shisi.api import registry as reg_mod

    db = tmp_path / "audit.db"
    _seed(db, [("u1::c1", 50.0, enh_mod.MIRROR_REASON_POINTS)])
    monkeypatch.setattr(reg_mod, "run_migrations", lambda _db: None)

    seen: dict[str, int] = {}

    class _SpyEnhancer(reg_mod.AffinityEnhancer):
        def __init__(self, db_path=None):
            with sqlite3.connect(db) as conn:
                seen["points_left"] = conn.execute(
                    "SELECT COUNT(*) FROM affinity_records WHERE reason = ?",
                    (enh_mod.MIRROR_REASON_POINTS,),
                ).fetchone()[0]
            super().__init__(db_path=db_path)

    monkeypatch.setattr(reg_mod, "AffinityEnhancer", _SpyEnhancer)
    reg = reg_mod.setup_shisi(app=None, db_path=db)

    assert seen["points_left"] == 0, "enhancer 回放前必须已完成标记迁移"
    assert reg.affinity_enhancer._values["u1::c1"] == pytest.approx(50.0)
