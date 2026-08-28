"""角色专属TTS配置 + 情感参数调整。

2026-08-28 MiMo-only 收敛：原 EmotionVoiceMapper（Edge-TTS 专用）已删除，
情感参数统一由 MiMoTTSProvider 内部映射处理。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config

logger = logging.getLogger("shisi.voice.emotion_tts")


class VoiceEnhancer:
    def __init__(self):
        self._default_tts = get_config("voice", "default_tts", "mimo-tts")
        self._emotion_params = get_config("voice", "emotion_params", {})
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


# 2026-08-28 MiMo-only 收敛：EmotionVoiceMapper（Edge-TTS 专用 rate/volume 参数体系）已随
# 多引擎时代删除；情感→语音参数由 MiMoTTSProvider 内部 8 情感映射承担。
