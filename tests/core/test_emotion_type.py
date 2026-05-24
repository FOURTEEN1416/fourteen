"""EmotionType 单元测试"""

from shisi.core.models.emotion_type import EmotionType


def test_all_emotions():
    assert len(EmotionType) == 10


def test_emotion_values():
    assert EmotionType.NEUTRAL == 1
    assert EmotionType.HAPPY == 2
    assert EmotionType.LOVELY == 5
