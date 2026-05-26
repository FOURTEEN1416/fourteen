"""
CosyVoice 提供者 - 本地/远程语音合成

通过 OpenAI 兼容 API 调用 CosyVoice TTS 服务。
需要运行 cosyvoice-server 命令启动服务：

    cosyvoice-server --ip 127.0.0.1 --port 8088 --type instruct

服务启动后会自动下载模型（首次约 3-4GB）。
"""

from __future__ import annotations

import logging

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.cosyvoice")

MAX_TEXT_LENGTH = 5000


class CosyVoiceProvider(TTSProviderBase):
    """
    CosyVoice 语音合成

    需要运行 CosyVoice 服务:
      https://github.com/lucasjinreal/CosyVoice

    用法:
        provider = CosyVoiceProvider()
        async with provider:
            audio = await provider.synthesize("你好")
    """

    def __init__(
        self,
        url: str = "http://localhost:8088",
        timeout: float = 60.0,
        voice: str = "中文男",
        response_format: str = "wav",
    ):
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._voice = voice
        self._response_format = response_format
        self._available = False
        self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        """显式关闭HTTP客户端，释放连接池"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.debug("CosyVoice HTTP客户端已关闭")

    @property
    def name(self) -> str:
        return "cosyvoice"

    async def _get_client(self):
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

        if len(text) > MAX_TEXT_LENGTH:
            logger.warning("CosyVoice 输入文本超长 (%d > %d 字符)", len(text), MAX_TEXT_LENGTH)
            text = text[:MAX_TEXT_LENGTH]

        voice = kwargs.get("voice", self._voice)
        response_format = kwargs.get("response_format", self._response_format)

        try:
            client = await self._get_client()
            payload = {
                "input": text,
                "model": "tts-1",
                "voice": voice,
                "response_format": response_format,
                "speed": kwargs.get("speed", 1.0),
                "stream": False,
            }

            response = await client.post("/v1/audio/speech", json=payload)
            response.raise_for_status()

            self._available = True
            return response.content  # type: ignore[no-any-return]

        except Exception as e:  # noqa: BLE001
            logger.error("CosyVoice 合成失败: %s", e)
            self._available = False
            return None

    def health_check(self) -> dict:
        return {
            "engine": "cosyvoice",
            "available": self._available,
            "url": self._url,
            "voice": self._voice,
            "timeout": self._timeout,
        }

    @property
    def supports_streaming(self) -> bool:
        return True

    async def synthesize_stream(self, text: str, **kwargs):
        """流式合成 - 通过SSE流式返回音频块"""
        if not text or not text.strip():
            return

        voice = kwargs.get("voice", self._voice)

        try:
            client = await self._get_client()
            payload = {
                "input": text,
                "model": "tts-1",
                "voice": voice,
                "response_format": "wav",
                "speed": kwargs.get("speed", 1.0),
                "stream": True,
            }

            async with client.stream("POST", "/v1/audio/speech", json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk

        except Exception as e:  # noqa: BLE001
            logger.error("CosyVoice 流式合成失败: %s", e)
