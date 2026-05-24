"""AffinityLevel 单元测试"""

from shisi.core.models.affinity_level import AffinityLevel


def test_display_name():
    assert AffinityLevel.STRANGER.display_name == "陌生人"
    assert AffinityLevel.LOVER.display_name == "恋人"
    assert AffinityLevel.BOND.display_name == "羁绊"


def test_threshold():
    assert AffinityLevel.STRANGER.threshold == 0
    assert AffinityLevel.LOVER.threshold == 200
    assert AffinityLevel.BOND.threshold == 500


def test_all_levels():
    assert len(AffinityLevel) == 9
