"""情感阶段 + 好感度 + 衰减 单元测试。"""

from datetime import datetime, timedelta

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
        last = datetime.now() - timedelta(days=2)
        assert decay.calculate_decay(50, last) == 0.0

    def test_decay_after_grace(self):
        decay = DecayEngine(decay_rate=0.5, grace_period_days=3)
        last = datetime.now() - timedelta(days=5)
        d = decay.calculate_decay(50, last)
        assert abs(d - 1.0) < 0.01

    def test_decay_capped_at_current(self):
        decay = DecayEngine(decay_rate=10, grace_period_days=0)
        last = datetime.now() - timedelta(days=10)
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
    def test_update(self):
        enhancer = AffinityEnhancer()
        val, unlocks = enhancer.update("c", 10, "chat")
        assert val == 10.0

    def test_clamp_max(self):
        enhancer = AffinityEnhancer()
        enhancer.update("c", 95, "big_positive")
        val, _ = enhancer.update("c", 10, "overflow")
        assert val <= 100.0

    def test_clamp_min(self):
        enhancer = AffinityEnhancer()
        val, _ = enhancer.update("c", -200, "big_negative")
        assert val >= 0.0

    def test_get_progress(self):
        enhancer = AffinityEnhancer()
        enhancer.update("c", 50, "chat")
        p = enhancer.get_progress("c")
        assert p["affinity"] == 50.0
        assert p["percentage"] == 50.0

    def test_apply_decay(self):
        enhancer = AffinityEnhancer()
        enhancer.update("c", 50)
        enhancer._last_interaction["c"] = datetime.now() - timedelta(days=5)
        decay = enhancer.apply_decay("c")
        assert decay > 0
        assert enhancer.get_value("c") < 50.0

    def test_unlock_integration(self):
        enhancer = AffinityEnhancer()
        val, unlocks = enhancer.update("c", 55, "chat")
        assert len(unlocks) >= 2
