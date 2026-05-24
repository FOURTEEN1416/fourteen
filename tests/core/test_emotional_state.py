"""EmotionalState 单元测试"""

from shisi.core.models.emotional_state import EmotionalState


def test_intensity_clamping():
    e = EmotionalState(intensity=-0.5)
    assert e.intensity == 0.0

    e2 = EmotionalState(intensity=1.5)
    assert e2.intensity == 1.0


def test_energy_clamping():
    e = EmotionalState(energy=2.0)
    assert e.energy == 1.0


def test_affection_points_non_negative():
    e = EmotionalState(affection_points=-10)
    assert e.affection_points == 0.0


def test_to_dict():
    e = EmotionalState()
    d = e.to_dict()
    assert d["primary_emotion"] == "NEUTRAL"
    assert d["affinity_name"] == "陌生人"
    assert "intensity" in d
    assert "energy" in d
