"""AffinityMapper 映射逻辑单元测试。"""

from pathlib import Path

import pytest

from shisi.affinity.enhancer import AffinityEnhancer
from shisi.affinity.mapper import AffinityMapper
from shisi.core.models import AffinityLevel
from shisi.migrations import run_migrations


@pytest.fixture
def mapper(tmp_db):
    run_migrations(tmp_db)
    enhancer = AffinityEnhancer(db_path=Path(tmp_db))
    return AffinityMapper(enhancer=enhancer)


def test_emotion_max_is_bond_threshold(mapper):
    assert mapper.emotion_max() == AffinityLevel.BOND.threshold == 500.0


def test_to_shisi_scales_by_bond_threshold(mapper):
    assert mapper.to_shisi(0) == 0.0
    assert mapper.to_shisi(250) == 50.0
    assert mapper.to_shisi(500) == 100.0
    assert mapper.to_shisi(1000) == 100.0  # clamped to max


def test_to_shisi_negative_clamped_to_min(mapper):
    assert mapper.to_shisi(-100) == 0.0


def test_to_emotion_reverse_mapping(mapper):
    assert mapper.to_emotion(0) == 0.0
    assert mapper.to_emotion(50) == 250.0
    assert mapper.to_emotion(100) == 500.0
    assert mapper.to_emotion(200) == 500.0  # clamped to max


def test_sync_updates_enhancer(mapper):
    result = mapper.sync("c1", 100)
    assert result is not None
    assert result["affinity"] > 0
    assert mapper._enhancer.get_value("c1") == result["affinity"]


def test_sync_delta_is_capped(mapper):
    # 100 emotion pts -> 20 shisi, but default max_delta=3 means first step = 3
    result = mapper.sync("c1", 100)
    assert result["affinity"] == 3.0


def test_sync_multiple_converges(mapper):
    mapper.sync("c1", 500)
    mapper.sync("c1", 500)
    mapper.sync("c1", 500)
    mapper.sync("c1", 500)
    # each step max +3; after 4 steps should be 12, not yet 100
    assert mapper._enhancer.get_value("c1") == 12.0


def test_sync_no_op_when_unchanged(mapper):
    # 1 emotion pt -> 0.2 shisi, small enough to not hit max_delta cap
    mapper.sync("c1", 1)
    first = mapper._enhancer.get_value("c1")
    result = mapper.sync("c1", 1)
    assert result["affinity"] == first
    assert mapper._enhancer.get_value("c1") == first


def test_sync_without_enhancer_returns_none():
    mapper = AffinityMapper()
    assert mapper.sync("c1", 100) is None
