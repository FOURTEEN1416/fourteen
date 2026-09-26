"""
TTS引擎抽象基类

所有TTS提供者必须实现此接口

W7 契约（2026-09-27）：synthesize 返回 ``SynthesizedAudio``（不可变，
data + 实际 fmt + mime），不再裸 bytes——云端产 MP3、SAPI 兜底产 WAV，
格式必须如实标注，消费方按实际格式播放/转码/发送。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .audio_result import SynthesizedAudio


class TTSProviderBase(ABC):
    """
    TTS提供者抽象基类

    所有引擎必须实现:
      - synthesize(): 文本→音频（SynthesizedAudio）
      - health_check(): 健康检查

    可选实现:
      - synthesize_stream(): 流式合成
    """

    @abstractmethod
    async def synthesize(self, text: str, **kwargs) -> SynthesizedAudio | None:
        """
        将文本合成为音频

        Args:
            text: 要合成的文本
            **kwargs: 合成参数快照（emotion/model/voice_id/speed/pitch/speaker_name），
                逐次传入，实现方不得改写自身全局状态来区分调用方

        Returns:
            SynthesizedAudio，失败返回None
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
