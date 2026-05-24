"""PersonaProfile 单元测试"""

import pytest
from shisi.core.models.persona_profile import PersonaProfile


def test_dimension_clamping():
    p = PersonaProfile(warmth=1.5, playfulness=-0.3)
    assert p.warmth == 1.0
    assert p.playfulness == 0.0


def test_to_prompt_segment():
    p = PersonaProfile()
    segment = p.to_prompt_segment()
    assert "【核心性格】" in segment
    assert "温暖度" in segment


def test_to_dict_roundtrip():
    p = PersonaProfile(warmth=0.8, jealousy=0.6, core_anchors=["锚点1"])
    d = p.to_dict()
    p2 = PersonaProfile.from_dict(d)
    assert p2.warmth == 0.8
    assert p2.jealousy == 0.6
    assert p2.core_anchors == ["锚点1"]


def test_from_emotion_style_map():
    class MockStyleMap:
        warmth = 0.6
        playfulness = 0.4
        independence = 0.7
        jealousy = 0.2
        stubbornness = 0.3

    p = PersonaProfile.from_emotion_style_map(MockStyleMap())
    assert p.warmth == 0.6
    assert p.independence == 0.7
