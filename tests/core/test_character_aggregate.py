"""CharacterAggregate 单元测试"""

import pytest

from shisi.core.models.affinity_level import AffinityLevel
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.models.emotion_type import EmotionType


def test_create_default():
    char = CharacterAggregate(name="十四")
    assert char.name == "十四"
    assert char.version == 1
    assert len(char.id) == 8


def test_validate_name_empty():
    with pytest.raises(Exception):
        CharacterAggregate(name="")


def test_validate_name_whitespace():
    with pytest.raises(Exception):
        CharacterAggregate(name="   ")


def test_update_emotion_lovely():
    char = CharacterAggregate(name="十四")
    char.update_emotion("我想你了")
    assert char.emotional_state.primary_emotion == EmotionType.LOVELY
    assert char.emotional_state.affection_points > 0


def test_update_emotion_angry():
    char = CharacterAggregate(name="十四")
    char.update_emotion("烦死了")
    assert char.emotional_state.primary_emotion == EmotionType.ANGRY


def test_energy_decay():
    char = CharacterAggregate(name="十四")
    initial_energy = char.emotional_state.energy
    char.update_emotion("你好")
    assert char.emotional_state.energy == initial_energy - 0.02


def test_affinity_level_upgrade():
    char = CharacterAggregate(name="十四")
    char.emotional_state.affection_points = 9.0
    char.update_emotion("喜欢")
    assert char.emotional_state.affinity_level >= AffinityLevel.ACQUAINTANCE


def test_build_system_prompt():
    char = CharacterAggregate(name="十四", description="可爱")
    prompt = char.build_system_prompt("你好", "历史对话")
    assert "十四" in prompt
    assert "【核心性格】" in prompt
    assert "历史对话" in prompt


def test_from_legacy_card():
    card_data = {
        "data": {"name": "十四", "description": "可爱", "personality": "温柔"},
        "spec": "chara_card_v2",
    }
    char = CharacterAggregate.from_legacy_card(card_data)
    assert char.name == "十四"
    assert char.source_format == "chara_card_v2"


def test_to_dict():
    char = CharacterAggregate(name="十四")
    d = char.to_dict()
    assert d["name"] == "十四"
    assert "persona" in d
    assert "emotional_state" in d
