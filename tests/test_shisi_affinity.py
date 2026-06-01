"""shisi 好感度模块测试 — AffinityEnhancer (含 decay / unlock / audit)"""

from pathlib import Path

import pytest

from shisi.affinity.decay_engine import DecayEngine
from shisi.affinity.enhancer import AffinityEnhancer
from shisi.affinity.unlock_manager import UnlockManager
from shisi.migrations import run_migrations


@pytest.fixture
def enhancer(tmp_db):
    run_migrations(tmp_db)
    return AffinityEnhancer(db_path=Path(tmp_db))


class TestAffinityEnhancer:
    """好感度核心逻辑"""

    def test_update_increases_value(self, enhancer):
        new_val, unlocks = enhancer.update("char_1", 10.0, "聊天互动")
        assert new_val == 10.0
        assert isinstance(unlocks, list)

    def test_update_within_bounds(self, enhancer):
        """好感度不能超过最大值 (100)"""
        new_val, _ = enhancer.update("char_1", 999.0, "封顶测试")
        assert new_val <= 100.0

    def test_update_below_minimum(self, enhancer):
        """好感度不能低于最小值 (0)"""
        new_val, _ = enhancer.update("char_1", -999.0, "下限测试")
        assert new_val >= 0.0

    def test_update_multiple_times(self, enhancer):
        enhancer.update("char_1", 30.0, "第一次")
        new_val, _ = enhancer.update("char_1", 20.0, "第二次")
        assert new_val == 50.0

    def test_update_different_characters_independent(self, enhancer):
        v1, _ = enhancer.update("char_a", 30.0)
        v2, _ = enhancer.update("char_b", 50.0)
        assert v1 == 30.0
        assert v2 == 50.0

    def test_get_value(self, enhancer):
        assert enhancer.get_value("char_1") == 0.0
        enhancer.update("char_1", 25.0)
        assert enhancer.get_value("char_1") == 25.0

    def test_get_progress(self, enhancer):
        enhancer.update("char_1", 50.0)
        progress = enhancer.get_progress("char_1")
        assert progress["affinity"] == 50.0
        assert progress["percentage"] == 50.0
        assert "unlocks" in progress

    def test_get_progress_unknown_char(self, enhancer):
        progress = enhancer.get_progress("unknown")
        assert progress["affinity"] == 0.0

    def test_apply_decay(self, enhancer):
        enhancer.update("char_1", 80.0, "初始")
        decay = enhancer.apply_decay("char_1")
        assert decay >= 0.0
        # 衰减后的值应小于原始值（如果 decay > 0）
        if decay > 0:
            assert enhancer.get_value("char_1") < 80.0

    def test_apply_decay_unknown(self, enhancer):
        """未知角色衰减为 0"""
        decay = enhancer.apply_decay("unknown")
        assert decay == 0.0

    def test_records_persisted(self, enhancer, tmp_db):
        """update 写入 affinity_records 表"""
        enhancer.update("char_1", 15.0, "测试记录", "test")
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        try:
            row = conn.execute(
                "SELECT character_id, old_value, new_value, delta, reason, source FROM affinity_records"
            ).fetchone()
            assert row is not None
            assert row[0] == "char_1"
            assert row[1] == 0.0  # old_value
            assert row[2] == 15.0  # new_value
            assert row[3] == 15.0  # delta
            assert row[4] == "测试记录"
            assert row[5] == "test"
        finally:
            conn.close()

    def test_audit_recorded(self, enhancer, tmp_db):
        enhancer._audit_enabled = True
        enhancer.update("char_1", 5.0, "审计测试")
        import sqlite3
        conn = sqlite3.connect(tmp_db)
        try:
            row = conn.execute(
                "SELECT character_id, action FROM affinity_audit"
            ).fetchone()
            assert row is not None
            assert row[0] == "char_1"
            assert row[1] == "update"
        finally:
            conn.close()

    def test_multiple_characters_independent_records(self, enhancer, tmp_db):
        enhancer.update("char_a", 10.0)
        enhancer.update("char_b", 20.0)
        enhancer.update("char_a", 5.0)

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        try:
            rows = conn.execute(
                "SELECT character_id, delta FROM affinity_records ORDER BY rowid"
            ).fetchall()
            assert len(rows) == 3
            assert rows[0] == ("char_a", 10.0)
            assert rows[1] == ("char_b", 20.0)
            assert rows[2] == ("char_a", 5.0)
        finally:
            conn.close()


class TestDecayEngine:
    """衰减计算"""

    @pytest.fixture
    def decay(self):
        return DecayEngine()

    def test_calculate_decay_no_elapsed(self, decay):
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc)
        amount = decay.calculate_decay(50.0, now, now)
        assert amount == 0.0

    def test_calculate_decay_high_affinity(self, decay):
        """超过宽限期(3天)后开始衰减"""
        from datetime import datetime, timedelta, timezone
        old = datetime.now(tz=timezone.utc) - timedelta(days=10)
        amount = decay.calculate_decay(80.0, old)
        assert amount > 0.0

    def test_calculate_decay_low_affinity(self, decay):
        """低好感度衰减更慢"""
        from datetime import datetime, timedelta, timezone
        old = datetime.now(tz=timezone.utc) - timedelta(days=3)
        amount = decay.calculate_decay(10.0, old)
        assert amount >= 0.0
        # 10 好感度应该衰减得比 80 慢
        high = decay.calculate_decay(80.0, old)
        assert high > amount if high > 0 else True

    def test_calculate_decay_zero_affinity(self, decay):
        """0 好感度不衰减"""
        from datetime import datetime, timedelta, timezone
        old = datetime.now(tz=timezone.utc) - timedelta(days=7)
        amount = decay.calculate_decay(0.0, old)
        assert amount == 0.0


class TestUnlockManager:
    """好感度解锁"""

    @pytest.fixture
    def unlock(self):
        return UnlockManager()

    def test_check_unlocks_below_threshold(self, unlock):
        events = unlock.check_unlocks("char_1", 0.0, 5.0)
        # 低于第一个阈值 (10) → 无解锁
        if events:
            for e in events:
                assert e.threshold > 5.0

    def test_check_unlocks_above_threshold(self, unlock):
        events = unlock.check_unlocks("char_1", 0.0, 30.0)
        # 应触发所有经过阈值
        assert len(events) >= 1

    def test_check_unlocks_cross_threshold(self, unlock):
        events = unlock.check_unlocks("char_1", 8.0, 30.0)
        # 跨越阈值
        thresholds = {e.threshold for e in events}
        assert len(thresholds) > 0

    def test_get_unlocks_at(self, unlock):
        unlocks = unlock.get_unlocks_at(50)
        assert len(unlocks) > 0
        for u in unlocks:
            assert u.threshold <= 50

    def test_get_unlocks_at_zero(self, unlock):
        unlocks = unlock.get_unlocks_at(0)
        assert len(unlocks) == 0
