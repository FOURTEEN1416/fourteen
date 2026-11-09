"""``voicedesign`` 的描述解析回归（2026-11-09 生产「苏打」事件）。

生产 ``data/character_voices.json`` 里有一条：

    {"mimo_model": "mimo-v2.5-tts-voicedesign", "voice_id": "苏打"}

``voicedesign`` 的 user 消息就是「音色描述」，旧实现把 ``voice_id`` 直接当描述，
于是每次合成都让模型「按『苏打』这两个字现场设计音色」——同一角色前后音色不可能
稳定。预置音色名不是描述，必须回落默认描述并告警。
"""

from __future__ import annotations

import logging

import pytest

from voice.mimo_tts_provider import MiMoTTSProvider


@pytest.fixture
def provider() -> MiMoTTSProvider:
    return MiMoTTSProvider(api_key="test-key")


@pytest.mark.parametrize("preset", ["苏打", "冰糖", "茉莉", "白桦", "Mia", "mimo_default"])
def test_preset_name_is_not_used_as_design_description(provider, preset, caplog):
    with caplog.at_level(logging.WARNING, logger="voice.mimo_tts"):
        prompt = provider._resolve_design_prompt(preset)
    assert prompt == MiMoTTSProvider.DEFAULT_DESIGN_PROMPT
    assert preset not in prompt
    assert any("预置音色名" in r.message for r in caplog.records)


@pytest.mark.parametrize("alias", ["female-tianmei", "default"])
def test_legacy_aliases_are_treated_as_presets(provider, alias):
    """历史三档别名（default/female-tianmei…）同样不是描述。"""
    assert provider._resolve_design_prompt(alias) == MiMoTTSProvider.DEFAULT_DESIGN_PROMPT


def test_free_form_description_passes_through(provider):
    desc = "二十出头的女生，音高正常、不压嗓子，不要气泡音"
    assert provider._resolve_design_prompt(desc) == desc


def test_empty_voice_falls_back_to_default_description(provider):
    assert provider._resolve_design_prompt("") == MiMoTTSProvider.DEFAULT_DESIGN_PROMPT
    assert provider._resolve_design_prompt("   ") == MiMoTTSProvider.DEFAULT_DESIGN_PROMPT


def test_every_engine_emotion_has_mapping_and_style_hint():
    """情绪引擎能产出的每个值都必须有映射。

    缺项会 `_map_emotion` / `_style_instruction` 静默回落到「平常」——10 种情绪
    里有 3 种（吃醋/调皮/疲惫）历史上是空的，等于情绪层对它们完全空转。
    """
    from my_character.emotion_engine import Emotion

    missing_map = [e.value for e in Emotion if e.value not in MiMoTTSProvider.EMOTION_MAPPING]
    missing_hint = [e.value for e in Emotion if e.value not in MiMoTTSProvider.EMOTION_STYLE_HINTS]
    assert not missing_map, f"EMOTION_MAPPING 缺项: {missing_map}"
    assert not missing_hint, f"EMOTION_STYLE_HINTS 缺项: {missing_hint}"
