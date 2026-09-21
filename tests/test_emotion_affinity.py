"""情感阶段 + 好感度 + 衰减 单元测试。"""

import sys

sys.path.insert(0, ".")

from datetime import datetime, timedelta, timezone

import pytest

from shisi.affinity.decay_engine import DecayEngine
from shisi.affinity.enhancer import AffinityEnhancer
from shisi.affinity.unlock_manager import UnlockManager
from shisi.config import reset_config
from shisi.emotion_stage.stage_config import EmotionStageConfig
from shisi.emotion_stage.stage_engine import EmotionStageEngine


@pytest.fixture(autouse=True)
def reset_config_each():
    reset_config()


class TestEmotionStageConfig:
    def test_from_yaml(self):
        config = EmotionStageConfig.from_yaml()
        assert len(config.stages) == 4
        assert config.stages[0].name == "陌生"
        assert config.stages[-1].name == "羁绊"
        assert config.allow_backward is False

    def test_default(self):
        config = EmotionStageConfig.default()
        assert len(config.stages) == 4
        assert config.stages[2].name == "亲密"


class TestEmotionStageEngine:
    def test_evaluate_stages(self):
        engine = EmotionStageEngine()
        assert engine.evaluate("c1", 0).name == "陌生"
        assert engine.evaluate("c1", 30).name == "熟悉"
        assert engine.evaluate("c1", 60).name == "亲密"
        assert engine.evaluate("c1", 90).name == "羁绊"

    def test_boundary_values(self):
        engine = EmotionStageEngine()
        assert engine.evaluate("c", 24.9).name == "陌生"
        assert engine.evaluate("c", 25).name == "熟悉"
        assert engine.evaluate("c", 49.9).name == "熟悉"
        assert engine.evaluate("c", 50).name == "亲密"
        assert engine.evaluate("c", 100).name == "羁绊"

    def test_backward_forbidden(self):
        engine = EmotionStageEngine()
        engine.evaluate("c", 60)
        stage = engine.evaluate("c", 10)
        assert stage.name == "亲密"

    def test_stage_change_event(self):
        engine = EmotionStageEngine()
        events = []
        engine.subscribe(lambda e: events.append(e))
        engine.evaluate("c", 0)
        engine.evaluate("c", 60)
        assert len(events) == 1
        assert events[0].old_stage == "陌生"
        assert events[0].new_stage == "亲密"

    def test_get_progress(self):
        engine = EmotionStageEngine()
        engine.evaluate("c", 60)
        p = engine.get_progress("c")
        assert p["current_stage"] == "亲密"
        assert p["affinity"] == 60.0
        assert "亲密话题" in p["features"]

    def test_multiple_characters_independent(self):
        engine = EmotionStageEngine()
        engine.evaluate("c1", 60)
        engine.evaluate("c2", 10)
        assert engine.get_current_stage("c1").name == "亲密"
        assert engine.get_current_stage("c2").name == "陌生"


class TestDecayEngine:
    def test_no_decay_within_grace(self):
        decay = DecayEngine()
        last = datetime.now(tz=timezone.utc) - timedelta(days=2)
        assert decay.calculate_decay(50, last) == 0.0

    def test_decay_after_grace(self):
        decay = DecayEngine(decay_rate=0.5, grace_period_days=3)
        last = datetime.now(tz=timezone.utc) - timedelta(days=5)
        d = decay.calculate_decay(50, last)
        assert abs(d - 1.0) < 0.01

    def test_decay_capped_at_current(self):
        decay = DecayEngine(decay_rate=10, grace_period_days=0)
        last = datetime.now(tz=timezone.utc) - timedelta(days=10)
        d = decay.calculate_decay(5, last)
        assert d <= 5.0


class TestUnlockManager:
    def test_unlock_at_threshold(self):
        mgr = UnlockManager()
        unlocks = mgr.check_unlocks("c", 24, 26)
        assert any(u.name == "个人话题" for u in unlocks)

    def test_no_unlock_below_threshold(self):
        mgr = UnlockManager()
        unlocks = mgr.check_unlocks("c", 10, 20)
        assert len(unlocks) == 0

    def test_multiple_unlocks(self):
        mgr = UnlockManager()
        unlocks = mgr.check_unlocks("c", 0, 55)
        names = [u.name for u in unlocks]
        assert "个人话题" in names
        assert "亲密话题" in names
        assert "专属表情包" in names

    def test_unlock_subscribe(self):
        mgr = UnlockManager()
        received = []
        mgr.subscribe(lambda cid, ev: received.append(ev))
        mgr.check_unlocks("c", 0, 55)
        assert len(received) >= 3


class TestAffinityEnhancer:
    # ⚠️ 一律用 tmp_path 隔离库（2026-09-22）：审计回放恢复上线后，默认构造
    # 会读宿主 data/sqlite.db 的真实 affinity_records，宿主状态依赖性翻车
    # （同 edd3c47 channel-status 用例的教训）。
    def test_update(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        val, unlocks = enhancer.update("c", 10, "chat")
        assert val == 10.0

    def test_clamp_max(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        enhancer.update("c", 95, "big_positive")
        val, _ = enhancer.update("c", 10, "overflow")
        assert val <= 100.0

    def test_clamp_min(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        val, _ = enhancer.update("c", -200, "big_negative")
        assert val >= 0.0

    def test_get_progress(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        enhancer.update("c", 50, "chat")
        p = enhancer.get_progress("c")
        assert p["affinity"] == 50.0
        assert p["percentage"] == 50.0

    def test_apply_decay(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        enhancer.update("c", 50)
        enhancer._last_interaction["c"] = datetime.now(tz=timezone.utc) - timedelta(days=5)
        decay = enhancer.apply_decay("c")
        assert decay > 0
        assert enhancer.get_value("c") < 50.0

    def test_unlock_integration(self, tmp_path):
        enhancer = AffinityEnhancer(db_path=tmp_path / "a.db")
        val, unlocks = enhancer.update("c", 55, "chat")
        assert len(unlocks) >= 2


# ── 好感度审计回放恢复（2026-09-22 重启归零根治）────────────────────
# 缺陷：AffinityEnhancer._values 纯内存，重启后全部归 0——mapper.sync 差分
# 钳 ±3 每轮只能爬回 3 分、解锁重复触发、get_progress 恒显 ≈0。affinity_records
# 是本类逐次写入的审计日志，现作为恢复源。


def _make_records_db(tmp_path, rows):
    import sqlite3

    db = tmp_path / "aff.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE affinity_records ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " character_id TEXT NOT NULL,"
        " old_value REAL NOT NULL, new_value REAL NOT NULL,"
        " delta REAL NOT NULL, reason TEXT, source TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.executemany(
        "INSERT INTO affinity_records (character_id, old_value, new_value, delta, created_at) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


class TestAuditRestore:
    def test_restore_latest_per_key(self, tmp_path):
        db = _make_records_db(tmp_path, [
            ("u1::c1", 3.0, 6.0, 3.0, "2026-09-01 00:00:00"),
            ("u1::c1", 6.0, 9.0, 3.0, "2026-09-02 00:00:00"),
            ("u2::c1", 0.0, 4.0, 4.0, "2026-09-03 00:00:00"),
        ])
        enh = AffinityEnhancer(db_path=db)
        assert enh.get_value("c1", user_id="u1") == 9.0
        assert enh.get_value("c1", user_id="u2") == 4.0
        # _last_interaction 恢复为 aware UTC（DecayEngine 用 aware now 相减）
        last = enh._last_interaction["u1::c1"]
        assert last.tzinfo is not None
        assert last == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)

    def test_update_continues_from_restored_value(self, tmp_path):
        """恢复 60 分后 +3 更新得 63——而非从 0 起步（±3 爬坡根治）。"""
        db = _make_records_db(tmp_path, [
            ("u1::c1", 57.0, 60.0, 3.0, "2026-09-01 00:00:00"),
        ])
        enh = AffinityEnhancer(db_path=db)
        new, _unlocks = enh.update("c1", 3.0, reason="chat", user_id="u1")
        assert new == 63.0

    def test_restore_clamps_and_tolerates_bad_timestamp(self, tmp_path):
        db = _make_records_db(tmp_path, [
            ("u1::c1", 90.0, 500.0, 410.0, "not-a-date"),
        ])
        enh = AffinityEnhancer(db_path=db)
        assert enh.get_value("c1", user_id="u1") == 100.0  # clamp 到 max
        assert enh._last_interaction["u1::c1"].tzinfo is not None

    def test_decay_works_across_restart_with_naive_created_at(self, tmp_path):
        """30 天前最后互动（naive UTC created_at）恢复后 decay_all 真能衰减
        （修复前 _last_interaction 丢失：要么不衰减要么 naive 相减崩）。"""
        old = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        db = _make_records_db(tmp_path, [("u1::c1", 47.0, 50.0, 3.0, old)])
        enh = AffinityEnhancer(db_path=db)
        decayed = enh.decay_all()
        assert decayed > 0
        assert enh.get_value("c1", user_id="u1") < 50.0

    def test_missing_table_degrades_to_zero_start(self, tmp_path):
        enh = AffinityEnhancer(db_path=tmp_path / "empty.db")
        assert enh.get_value("c1", user_id="u1") == 0.0
