"""
语音合成模块 — MiMo TTS 唯一引擎（2026-08-28 用户裁决 A：全语音域 MiMo-only）

设计原则:
  - 完全异步 (asyncio-native)
  - 无singleton装饰器（使用简单实例管理）
  - 无全局锁（支持并发TTS合成）
  - 外部调用有超时保护
  - 云 API 失败时由 MiMoTTSProvider 内部 fallback_local 本地引擎兜底

引擎:
  - mimo-tts: MiMo Cloud（mimo-v2.5-tts / voiceclone / voicedesign），唯一引擎
  （历史引擎 Edge-TTS / CosyVoice / GPT-SoVITS / Bert-VITS2 已于 08-28 删除）

使用方式:
    from voice import TTSManager

    manager = TTSManager()
    await manager.initialize(config)

    audio_bytes = await manager.synthesize("你好呀")
"""

from .mimo_tts_provider import MiMoTTSProvider
from .tts_manager import TTSManager
from .tts_provider_base import TTSProviderBase

__all__ = [
    "TTSManager",
    "TTSProviderBase",
    "MiMoTTSProvider",
]
