"""
GPT-SoVITS 提供者 - 本地/远程语音合成

通过HTTP API调用GPT-SoVITS服务。
从AI-YinMei的GtpVists适配而来，改为async + httpx。

与原版区别:
  - 使用httpx替代requests (async)
  - 超时保护
  - 连接池复用
  - 支持async context manager管理httpx生命周期
"""

from __future__ import annotations

import logging

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.gpt_sovits")

# 最大输入文本长度 (防止恶意超大文本超时)
MAX_TEXT_LENGTH = 5000


class GPTSoVITSProvider(TTSProviderBase):
    """
    GPT-SoVITS 语音合成

    需要运行 GPT-SoVITS API 服务:
      https://github.com/RVC-Boss/GPT-SoVITS

    用法:
        provider = GPTSoVITSProvider()
        async with provider:
            audio = await provider.synthesize("你好")
    """

    def __init__(
        self,
        url: str = "http://localhost:9880",
        timeout: float = 60.0,
        text_language: str = "auto",
    ):
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._text_language = text_language
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
            logger.debug("GPT-SoVITS HTTP客户端已关闭")

    @property
    def name(self) -> str:
        return "gpt-sovits"

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

        # 长度保护
        if len(text) > MAX_TEXT_LENGTH:
            logger.warning("GPT-SoVITS 输入文本超长 (%d > %d 字符)", len(text), MAX_TEXT_LENGTH)
            text = text[:MAX_TEXT_LENGTH]

        text_lang = kwargs.get("text_language", self._text_language)

        try:
            client = await self._get_client()
            params = {
                "text": text,
                "text_language": text_lang,
            }
            # 兼容不同版本的GPT-SoVITS API
            if "refer" in kwargs:
                params["refer"] = kwargs["refer"]
            if "prompt" in kwargs:
                params["prompt"] = kwargs["prompt"]
            if "prompt_language" in kwargs:
                params["prompt_language"] = kwargs["prompt_language"]

            response = await client.get("/", params=params)
            response.raise_for_status()

            self._available = True
            return response.content  # type: ignore[no-any-return]

        except Exception as e:  # noqa: BLE001
            logger.error("GPT-SoVITS 合成失败: %s", e)
            self._available = False
            return None

    async def set_refer_audio(self, refer_path: str, prompt_text: str, prompt_language: str):
        """设置参考音频（用于音色克隆）"""
        try:
            client = await self._get_client()  # 复用连接池
            response = await client.post(
                "/set_refer",
                params={
                    "refer_path": refer_path,
                    "prompt_text": prompt_text,
                    "prompt_language": prompt_language,
                },
            )
            response.raise_for_status()
            logger.info("GPT-SoVITS 参考音频设置成功")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("GPT-SoVITS 参考音频设置失败: %s", e)
            return False

    def health_check(self) -> dict:
        return {
            "engine": "gpt-sovits",
            "available": self._available,
            "url": self._url,
            "timeout": self._timeout,
        }
