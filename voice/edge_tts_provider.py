"""
Edge-TTS提供者 - 微软免费TTS

基于 edge-tts 库，无需GPU，需要网络连接。
AI-YinMei 中 EdgeTTs 的异步适配版本。

与原版区别:
  - async-native: 不阻塞事件循环
  - 超时保护: 30秒超时
  - 错误恢复: 失败时返回None而不是崩
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from .base import TTSProviderBase

logger = logging.getLogger("voice.edge_tts")


class EdgeTTSProvider(TTSProviderBase):
    """
    Edge-TTS 语音合成

    默认音色: zh-CN-XiaoxiaoNeural (中文女声)
    """

    def __init__(
        self,
        speaker_name: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        timeout: float = 30.0,
        output_dir: str | None = None,
    ):
        self._speaker_name = speaker_name
        self._rate = rate
        self._volume = volume
        self._timeout = timeout
        self._output_dir = output_dir or tempfile.gettempdir()
        self._available = False

    @property
    def name(self) -> str:
        return "edge-tts"

    async def synthesize(self, text: str, **kwargs) -> bytes | None:
        """
        合成语音

        Args:
            text: 要合成的文本
            **kwargs: 可选覆盖 voice, rate, volume

        Returns:
            MP3音频字节，失败返回None
        """
        if not text or not text.strip():
            return None

        voice = kwargs.get("voice", self._speaker_name)
        rate = kwargs.get("rate", self._rate)
        volume = kwargs.get("volume", self._volume)

        try:
            import edge_tts

            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                volume=volume,
            )

            # 收集所有chunk
            audio_chunks = []
            async with asyncio.timeout(self._timeout):  # type: ignore[attr-defined]
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])

            if not audio_chunks:
                logger.warning("Edge-TTS 返回空音频: text=%s", text[:50])
                return None

            self._available = True
            return b"".join(audio_chunks)

        except asyncio.TimeoutError:
            logger.error("Edge-TTS 超时 (%ss): text=%s", self._timeout, text[:50])
            self._available = False
            return None
        except ImportError:
            logger.error("edge-tts 未安装: pip install edge-tts")
            self._available = False
            return None
        except Exception as e:
            logger.exception("Edge-TTS 合成失败: %s", e)
            self._available = False
            return None

    async def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        """合成到文件"""
        audio = await self.synthesize(text, **kwargs)
        if audio is None:
            return False
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(audio)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("保存音频文件失败: %s", e)
            return False

    async def synthesize_stream(self, text: str, **kwargs):
        """流式合成"""
        voice = kwargs.get("voice", self._speaker_name)
        rate = kwargs.get("rate", self._rate)
        volume = kwargs.get("volume", self._volume)

        try:
            import edge_tts

            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)

            async with asyncio.timeout(self._timeout):  # type: ignore[attr-defined]
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        yield chunk["data"]

        except asyncio.TimeoutError:
            logger.error("Edge-TTS 流式超时")
        except Exception:
            logger.exception("Edge-TTS 流式失败")

    def health_check(self) -> dict:
        return {
            "engine": "edge-tts",
            "available": self._available,
            "speaker": self._speaker_name,
            "timeout": self._timeout,
        }
