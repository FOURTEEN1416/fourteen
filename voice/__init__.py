"""
语音合成模块 - 多TTS引擎统一抽象层

设计原则:
  - 完全异步 (asyncio-native)
  - 无singleton装饰器（使用简单实例管理）
  - 无全局锁（支持并发TTS合成）
  - 所有外部调用有超时保护
  - 支持运行时切换引擎
  - 引擎失败时自动降级

支持的引擎:
  - edge-tts: 免费，无需GPU，需要网络
  - gpt-sovits: 本地/远程GPT-SoVITS API
  - bert-vits2: 本地/远程Bert-VITS2 API

使用方式:
    from voice import TTSManager

    manager = TTSManager()
    await manager.initialize(config)

    # 合成语音
    audio_bytes = await manager.synthesize("你好呀")

    # 切换引擎
    await manager.switch_engine("gpt-sovits")
"""

from .bert_vits2_provider import BertVITS2Provider
from .edge_tts_provider import EdgeTTSProvider
from .manager import TTSManager, TTSProviderBase
from .sovits_provider import GPTSoVITSProvider

__all__ = [
    "TTSManager",
    "TTSProviderBase",
    "EdgeTTSProvider",
    "GPTSoVITSProvider",
    "BertVITS2Provider",
]
