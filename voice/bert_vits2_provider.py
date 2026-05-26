"""
Bert-VITS2 提供者 - 本地/远程语音合成

通过HTTP API调用Bert-VITS2服务。
从AI-YinMei的BertVis2适配而来，改为async + httpx。

变更:
  - 使用httpx替代requests (async)
  - 超时保护
  - 连接池复用
  - 支持async context manager管理httpx生命周期
"""

from __future__ import annotations

import asyncio
import logging

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.bert_vits2")

# 最大输入文本长度
MAX_TEXT_LENGTH = 5000


class BertVITS2Provider(TTSProviderBase):
    """
    Bert-VITS2 语音合成

    需要运行 Bert-VITS2 API 服务:
      https://github.com/fishaudio/Bert-VITS2

    用法:
        provider = BertVITS2Provider()
        async with provider:
            audio = await provider.synthesize("你好")
    """

    def __init__(
        self,
        url: str = "http://localhost:5000",
        speaker_name: str = "珊瑚宫心海[中]",
        timeout: float = 60.0,
        sdp_ratio: float = 0.2,
        noise: float = 0.2,
        noisew: float = 0.9,
        speed: float = 1.0,
    ):
        self._url = url.rstrip("/")
        self._speaker_name = speaker_name
        self._timeout = timeout
        self._sdp_ratio = sdp_ratio
        self._noise = noise
        self._noisew = noisew
        self._speed = speed
        self._available = False
        self._client = None
        self._client_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        """显式关闭HTTP客户端，释放连接池"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("Bert-VITS2 HTTP客户端已关闭")

    @property
    def name(self) -> str:
        return "bert-vits2"

    async def _get_client(self):
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    import httpx
                    self._client = httpx.AsyncClient(
                        base_url=self._url,
                        timeout=self._timeout,
                    )
        return self._client

    async def synthesize(self, text: str, **kwargs) -> bytes | None:
        if not text or not text.strip():
            return None

        # 长度保护
        if len(text) > MAX_TEXT_LENGTH:
            logger.warning("Bert-VITS2 输入文本超长 (%d > %d 字符)", len(text), MAX_TEXT_LENGTH)
            text = text[:MAX_TEXT_LENGTH]

        try:
            client = await self._get_client()

            params = {
                "text": text,
                "speaker_name": kwargs.get("speaker_name", self._speaker_name),
                "sdp_ratio": kwargs.get("sdp_ratio", self._sdp_ratio),
                "noise": kwargs.get("noise", self._noise),
                "noisew": kwargs.get("noisew", self._noisew),
                "speed": kwargs.get("speed", self._speed),
            }

            # Bert-VITS2 可能有不同API路径
            response = await client.get("/voice", params=params)
            response.raise_for_status()

            self._available = True
            return response.content  # type: ignore[no-any-return]

        except Exception as e:  # noqa: BLE001
            logger.error("Bert-VITS2 合成失败: %s", e)
            self._available = False
            return None

    def health_check(self) -> dict:
        return {
            "engine": "bert-vits2",
            "available": self._available,
            "url": self._url,
            "speaker": self._speaker_name,
            "timeout": self._timeout,
        }
