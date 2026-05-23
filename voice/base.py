"""
TTS引擎抽象基类

所有TTS提供者必须实现此接口
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class TTSProviderBase(ABC):
    """
    TTS提供者抽象基类

    所有引擎必须实现:
      - synthesize(): 文本→音频
      - health_check(): 健康检查

    可选实现:
      - synthesize_stream(): 流式合成
    """

    @abstractmethod
    async def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        """
        将文本合成为音频

        Args:
            text: 要合成的文本
            **kwargs: 引擎特定参数（语速、音色等）

        Returns:
            音频字节数据 (WAV/MP3格式)，失败返回None
        """
        ...

    async def synthesize_stream(self, text: str, **kwargs):
        """
        流式合成（可选）

        默认实现为非流式，返回单个chunk
        """
        audio = await self.synthesize(text, **kwargs)
        if audio:
            yield audio

    @abstractmethod
    def health_check(self) -> dict:
        """
        健康检查

        Returns:
            {"available": bool, "engine": str, ...}
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎名称"""
        ...

    @property
    def supports_streaming(self) -> bool:
        """是否支持流式合成"""
        return False
