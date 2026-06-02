"""
P0 测试套件：修复后关键模块的单元测试

覆盖:
- AffinityEnhancer.update() 边界（min/max 限幅 + 多角色隔离）
- EmotionStageEngine.evaluate() 阶段映射
- PersonaExtractor.set_user_id() 多用户隔离
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock


def _safe_unlink(path: str, retries: int = 5, delay: float = 0.1) -> None:
    """Windows 上 sqlite 文件可能被临时锁定, 重试删除。"""
    for _ in range(retries):
        try:
            if os.path.exists(path):
                os.unlink(path)
            return
        except PermissionError:
            time.sleep(delay)
    # 兜底: 留给 OS 清理


# ───────────────────────── AffinityEnhancer ─────────────────────────


class TestAffinityEnhancer:
    """AffinityEnhancer 行为测试。"""

    def _make_enhancer(self):
        from shisi.affinity.enhancer import AffinityEnhancer
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        return AffinityEnhancer(db_path=tmp.name), tmp.name

    def test_update_increments_affinity(self):
        enhancer, db = self._make_enhancer()
        try:
            new, unlocks = enhancer.update("char1", delta=5.0, reason="chat")
            assert new == 5.0
            assert enhancer.get_value("char1") == 5.0
            assert unlocks == []
        finally:
            _safe_unlink(db)

    def test_update_clamps_to_max(self):
        enhancer, db = self._make_enhancer()
        try:
            enhancer.update("char1", delta=50.0)
            new, _ = enhancer.update("char1", delta=200.0)
            assert new == 100.0, f"应被 max=100 限幅, 实际 {new}"
        finally:
            _safe_unlink(db)

    def test_update_clamps_to_min(self):
        enhancer, db = self._make_enhancer()
        try:
            enhancer.update("char1", delta=10.0)
            new, _ = enhancer.update("char1", delta=-200.0)
            assert new == 0.0, f"应被 min=0 限幅, 实际 {new}"
        finally:
            _safe_unlink(db)

    def test_independent_characters(self):
        enhancer, db = self._make_enhancer()
        try:
            enhancer.update("alice", delta=10.0)
            enhancer.update("bob", delta=20.0)
            assert enhancer.get_value("alice") == 10.0
            assert enhancer.get_value("bob") == 20.0
        finally:
            _safe_unlink(db)

    def test_get_progress_shape(self):
        enhancer, db = self._make_enhancer()
        try:
            enhancer.update("char1", delta=30.0)
            progress = enhancer.get_progress("char1")
            assert progress["character_id"] == "char1"
            assert progress["affinity"] == 30.0
            assert 0.0 <= progress["percentage"] <= 100.0
            assert isinstance(progress["unlocks"], list)
        finally:
            _safe_unlink(db)


# ───────────────────────── EmotionStageEngine ─────────────────────────


class TestEmotionStageEngine:
    """EmotionStageEngine 阶段评估测试。"""

    def test_evaluate_returns_stage(self):
        from shisi.emotion_stage.stage_engine import EmotionStageEngine
        engine = EmotionStageEngine()
        stage = engine.evaluate("char1", affinity=50.0)
        assert stage is not None
        # StageDefinition 字段: name / affinity_min / affinity_max / features
        assert hasattr(stage, "name")
        assert hasattr(stage, "affinity_min")
        assert hasattr(stage, "affinity_max")
        assert isinstance(stage.features, list)

    def test_evaluate_low_affinity_first_stage(self):
        from shisi.emotion_stage.stage_engine import EmotionStageEngine
        engine = EmotionStageEngine()
        stages = engine.stages
        first_stage = stages[0]
        result = engine.evaluate("char1", affinity=first_stage.affinity_min)
        assert result.name == first_stage.name

    def test_evaluate_high_affinity_advanced_stage(self):
        from shisi.emotion_stage.stage_engine import EmotionStageEngine
        engine = EmotionStageEngine()
        result = engine.evaluate("char1", affinity=9999.0)
        # 高好感度应映射到最后一个 stage
        assert result.name == engine.stages[-1].name

    def test_evaluate_mid_affinity(self):
        from shisi.emotion_stage.stage_engine import EmotionStageEngine
        engine = EmotionStageEngine()
        result = engine.evaluate("char1", affinity=60.0)
        # 60 落在 [50, 75) 区间 = 亲密
        assert result.name == "亲密"

    def test_evaluate_backward_forbidden(self):
        """回退被禁止时, 阶段应保持原状。"""
        from shisi.emotion_stage.stage_engine import EmotionStageEngine
        engine = EmotionStageEngine()
        # 升到 80 → 羁绊
        engine.evaluate("char1", affinity=80.0)
        # 降回 20 → 禁止回退, 仍应为羁绊
        result = engine.evaluate("char1", affinity=20.0)
        assert result.name == "羁绊"


# ───────────────────────── PersonaExtractor.set_user_id ─────────────────────────


class TestPersonaExtractorUserId:
    """P0-B 修复验证：set_user_id 多用户隔离。"""

    def _make_extractor(self, tmp_path: Path):
        from persona_extractor.fusion import PersonaExtractor
        db_path = tmp_path / "test.db"
        pe = PersonaExtractor(llm_gateway=MagicMock(), db_path=str(db_path), user_id="alice")
        return pe, db_path

    def test_set_user_id_changes(self, tmp_path):
        pe, _ = self._make_extractor(tmp_path)
        assert pe.user_id == "alice"
        pe.set_user_id("bob")
        assert pe.user_id == "bob"

    def test_set_user_id_resets_msg_count(self, tmp_path):
        pe, _ = self._make_extractor(tmp_path)
        pe._msg_count = 10  # 模拟累计
        pe.set_user_id("bob")
        assert pe._msg_count == 0, "切换 user_id 必须重置消息计数（避免频率控制串味）"

    def test_set_user_id_same_value_noop(self, tmp_path):
        pe, _ = self._make_extractor(tmp_path)
        pe._msg_count = 5
        pe.set_user_id("alice")
        # 同值切换不应重置 _msg_count（避免无谓清零）
        assert pe._msg_count == 5
