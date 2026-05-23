"""角色专属TTS配置 + 情感参数调整。"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config

logger = logging.getLogger("shisi.voice_ext.emotion_tts")


class VoiceEnhancer:
    def __init__(self):
        self._default_tts = get_config("voice_ext", "default_tts", "edge-tts")
        self._emotion_params = get_config("voice_ext", "emotion_params", {})
        self._character_tts: dict[str, dict[str, Any]] = {}

    def get_tts_config(self, character_id: str, emotion: str = "") -> dict[str, Any]:
        config: dict[str, Any] = {
            "tts_engine": self._default_tts,
            "speed": 1.0,
            "pitch": 1.0,
            "volume": 1.0,
        }

        char_config = self._character_tts.get(character_id)
        if char_config:
            config.update(char_config)

        if emotion and emotion in self._emotion_params:
            ep = self._emotion_params[emotion]
            config["speed"] = config.get("speed", 1.0) * ep.get("speed", 1.0)
            config["pitch"] = config.get("pitch", 1.0) * ep.get("pitch", 1.0)

        return config

    def bind_character_tts(self, character_id: str, tts_engine: str, voice_id: str = "", **kwargs: Any) -> None:
        self._character_tts[character_id] = {
            "tts_engine": tts_engine,
            "voice_id": voice_id,
            **kwargs,
        }
        logger.info("绑定角色TTS: %s → %s (%s)", character_id, tts_engine, voice_id)

    def get_character_tts(self, character_id: str) -> dict[str, Any] | None:
        return self._character_tts.get(character_id)


class EmotionVoiceMapper:
    """
    情感→语音参数映射器（Edge-TTS专用）

    支持参数: rate(语速), volume(音量)
    不支持: pitch(音调) - Edge-TTS限制

    扩展 VoiceEnhancer，与其共存不替换
    """

    _EMOTION_PARAMS: dict[str, dict[str, str]] = {
        "开心": {"rate": "+10%", "volume": "+10%"},
        "撒娇": {"rate": "-5%", "volume": "+5%"},
        "温柔": {"rate": "-10%", "volume": "-5%"},
        "伤心": {"rate": "-15%", "volume": "-10%"},
        "生气": {"rate": "+15%", "volume": "+15%"},
        "害怕": {"rate": "+10%", "volume": "-5%"},
        "害羞": {"rate": "-8%", "volume": "-10%"},
        "傲娇": {"rate": "+0%", "volume": "+0%"},
        "平常": {"rate": "+0%", "volume": "+0%"},
    }

    def __init__(self):
        self._character_overrides: dict[str, dict[str, dict[str, str]]] = {}

    def get_params(self, emotion: str, character_id: str = "") -> dict[str, Any]:
        params = self._EMOTION_PARAMS.get(emotion, self._EMOTION_PARAMS["平常"]).copy()
        if character_id and character_id in self._character_overrides:
            override = self._character_overrides[character_id].get(emotion, {})
            params.update(override)
        return params

    def apply_to_edge_tts(self, emotion: str, character_id: str = "") -> dict[str, str]:
        params = self.get_params(emotion, character_id)
        return {
            "rate": params.get("rate", "+0%"),
            "volume": params.get("volume", "+0%"),
        }

    def set_character_override(self, character_id: str, emotion: str, params: dict[str, str]) -> None:
        if character_id not in self._character_overrides:
            self._character_overrides[character_id] = {}
        self._character_overrides[character_id][emotion] = params
        logger.info("角色情感语音覆盖: %s/%s → %s", character_id, emotion, params)
