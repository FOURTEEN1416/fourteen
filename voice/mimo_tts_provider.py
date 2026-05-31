"""
MiMo TTS Provider - 小米MiMo语音合成API封装

支持模型:
- mimo-v2.5-tts: 基础语音合成
- mimo-v2.5-tts-voiceclone: 语音克隆
- mimo-v2.5-tts-voicedesign: 音色设计
- mimo-v2-tts: 降级备选

架构: 优先API，本地引擎作为降级
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.mimo_tts")


class MiMoTTSProvider(TTSProviderBase):
    """
    MiMo TTS API 提供者
    
    特性:
    - 支持多种MiMo TTS模型
    - 情感参数原生支持
    - 自动降级到本地引擎
    """

    # API端点
    API_BASE = "https://api.xiaomimimo.com/v1"
    ENDPOINTS = {
        "tts": "/audio/speech",
        "voiceclone": "/audio/voice-clone",
        "voicedesign": "/audio/voice-design",
    }

    # 支持的模型
    SUPPORTED_MODELS = [
        "mimo-v2.5-tts",
        "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2-tts",
    ]

    # 情感到MiMo参数的映射
    EMOTION_MAPPING: dict[str, dict[str, Any]] = {
        "开心": {"emotion": "cheerful", "speed": 1.1, "pitch": 1.05},
        "撒娇": {"emotion": "gentle", "speed": 0.95, "pitch": 1.1},
        "温柔": {"emotion": "soft", "speed": 0.9, "pitch": 0.95},
        "伤心": {"emotion": "sad", "speed": 0.85, "pitch": 0.9},
        "生气": {"emotion": "angry", "speed": 1.15, "pitch": 1.1},
        "害怕": {"emotion": "fearful", "speed": 1.1, "pitch": 1.15},
        "害羞": {"emotion": "shy", "speed": 0.9, "pitch": 1.05},
        "傲娇": {"emotion": "tsundere", "speed": 1.0, "pitch": 1.0},
        "平常": {"emotion": "neutral", "speed": 1.0, "pitch": 1.0},
    }

    def __init__(
        self,
        api_key: str,
        model: str = "mimo-v2.5-tts",
        voice_id: str = "",
        timeout: float = 30.0,
        fallback_local: bool = True,
    ):
        """
        初始化MiMo TTS提供者

        Args:
            api_key: MiMo API密钥
            model: 模型名称，默认mimo-v2.5-tts
            voice_id: 克隆音色ID（使用voiceclone时需要）
            timeout: API调用超时时间
            fallback_local: API失败时是否降级到本地引擎
        """
        self._api_key = api_key
        self._model = model if model in self.SUPPORTED_MODELS else "mimo-v2.5-tts"
        self._voice_id = voice_id
        self._timeout = timeout
        self._fallback_local = fallback_local
        self._local_fallback_provider: TTSProviderBase | None = None
        self._available = True
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return f"mimo-tts-{self._model}"

    @property
    def supports_streaming(self) -> bool:
        return True

    def _get_headers(self) -> dict[str, str]:
        """获取API请求头"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _map_emotion(self, emotion: str) -> dict[str, Any]:
        """将中文情感映射到MiMo参数"""
        return self.EMOTION_MAPPING.get(emotion, self.EMOTION_MAPPING["平常"]).copy()

    async def synthesize(self, text: str, **kwargs) -> bytes | None:
        """
        合成语音

        Args:
            text: 要合成的文本
            emotion: 情感状态（可选）
            speed: 语速调整（可选，覆盖情感参数）
            pitch: 音调调整（可选，覆盖情感参数）

        Returns:
            音频字节数据，失败返回None
        """
        if not text:
            return None

        # 构建请求参数
        emotion = kwargs.get("emotion", "")
        emotion_params = self._map_emotion(emotion)

        # 允许kwargs覆盖情感参数
        speed = kwargs.get("speed", emotion_params.get("speed", 1.0))
        pitch = kwargs.get("pitch", emotion_params.get("pitch", 1.0))

        payload: dict[str, Any] = {
            "model": self._model,
            "input": text,
            "voice": self._voice_id or "default",
            "speed": speed,
            "pitch": pitch,
            "response_format": "mp3",
        }

        # 如果是voiceclone模式，添加voice_id
        if self._model == "mimo-v2.5-tts-voiceclone" and self._voice_id:
            payload["voice_id"] = self._voice_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.API_BASE}{self.ENDPOINTS['tts']}",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        self._available = True
                        self._last_error = None
                        logger.debug("MiMo TTS合成成功: %d bytes", len(audio_data))
                        return audio_data
                    else:
                        error_text = await response.text()
                        self._last_error = f"API错误 {response.status}: {error_text}"
                        logger.warning("MiMo TTS API失败: %s", self._last_error)

        except asyncio.TimeoutError:
            self._last_error = "API调用超时"
            logger.warning("MiMo TTS超时")
        except Exception as e:
            self._last_error = f"请求异常: {e}"
            logger.warning("MiMo TTS异常: %s", e)

        # API失败，尝试本地降级
        if self._fallback_local:
            return await self._fallback_to_local(text, **kwargs)

        return None

    async def synthesize_stream(self, text: str, **kwargs):
        """
        流式合成语音

        Args:
            text: 要合成的文本
            chunk_size: 每个chunk的大小（字节）

        Yields:
            音频数据chunks
        """
        chunk_size = kwargs.get("chunk_size", 8192)

        payload: dict[str, Any] = {
            "model": self._model,
            "input": text,
            "voice": self._voice_id or "default",
            "stream": True,
            "response_format": "mp3",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.API_BASE}{self.ENDPOINTS['tts']}",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as response:
                    if response.status == 200:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            if chunk:
                                yield chunk
                    else:
                        error_text = await response.text()
                        logger.warning("MiMo TTS流式合成失败: %s", error_text)

        except Exception as e:
            logger.warning("MiMo TTS流式合成异常: %s", e)

    async def _fallback_to_local(self, text: str, **kwargs) -> bytes | None:
        """
        降级到本地TTS引擎

        降级顺序:
        1. CosyVoice (如果配置)
        2. GPT-SoVITS (如果配置)
        3. Bert-VITS2 (如果配置)
        4. Edge-TTS (最后保底)
        """
        if self._local_fallback_provider is None:
            self._local_fallback_provider = await self._create_fallback_provider()

        if self._local_fallback_provider:
            logger.info("MiMo TTS降级到本地引擎: %s", self._local_fallback_provider.name)
            try:
                result = await self._local_fallback_provider.synthesize(text, **kwargs)
                if result is not None:
                    return result
            except Exception as e:
                logger.warning("本地降级引擎失败: %s", e)

        return None

    async def _create_fallback_provider(self) -> TTSProviderBase | None:
        """创建本地降级引擎（按优先级）"""
        # 尝试CosyVoice
        try:
            from .cosyvoice_provider import CosyVoiceProvider
            provider = CosyVoiceProvider()
            health = provider.health_check()
            if health.get("available"):
                logger.info("降级引擎选择: CosyVoice")
                return provider
        except Exception:
            pass

        # 尝试GPT-SoVITS
        try:
            from .sovits_provider import GPTSoVITSProvider
            provider = GPTSoVITSProvider()
            health = provider.health_check()
            if health.get("available"):
                logger.info("降级引擎选择: GPT-SoVITS")
                return provider
        except Exception:
            pass

        # 尝试Bert-VITS2
        try:
            from .bert_vits2_provider import BertVITS2Provider
            provider = BertVITS2Provider()
            health = provider.health_check()
            if health.get("available"):
                logger.info("降级引擎选择: Bert-VITS2")
                return provider
        except Exception:
            pass

        # 最后尝试Edge-TTS（最稳定，无需本地服务）
        try:
            from .edge_tts_provider import EdgeTTSProvider
            provider = EdgeTTSProvider()
            logger.info("降级引擎选择: Edge-TTS")
            return provider
        except Exception as e:
            logger.warning("所有本地降级引擎不可用: %s", e)

        return None

    async def clone_voice(
        self,
        audio_data: bytes,
        voice_name: str,
        **kwargs,
    ) -> dict[str, Any]:
        """
        克隆音色（使用mimo-v2.5-tts-voiceclone）

        Args:
            audio_data: 参考音频数据（10-30秒）
            voice_name: 音色名称
            description: 音色描述（可选）

        Returns:
            {"voice_id": str, "status": str, "message": str}
        """
        if self._model != "mimo-v2.5-tts-voiceclone":
            return {
                "voice_id": "",
                "status": "error",
                "message": "当前模型不支持语音克隆，请使用mimo-v2.5-tts-voiceclone",
            }

        description = kwargs.get("description", f"克隆音色: {voice_name}")

        try:
            # 构建multipart表单
            data = aiohttp.FormData()
            data.add_field("voice_name", voice_name)
            data.add_field("description", description)
            data.add_field(
                "audio",
                audio_data,
                filename="reference.mp3",
                content_type="audio/mpeg",
            )

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.API_BASE}{self.ENDPOINTS['voiceclone']}",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=60.0),  # 克隆需要更长时间
                ) as response:
                    result = await response.json()
                    if response.status == 200:
                        voice_id = result.get("voice_id", "")
                        self._voice_id = voice_id  # 保存克隆的voice_id
                        logger.info("语音克隆成功: %s -> %s", voice_name, voice_id)
                        return {
                            "voice_id": voice_id,
                            "status": "success",
                            "message": "语音克隆成功",
                        }
                    else:
                        error_msg = result.get("error", "未知错误")
                        logger.warning("语音克隆失败: %s", error_msg)
                        return {
                            "voice_id": "",
                            "status": "error",
                            "message": error_msg,
                        }

        except Exception as e:
            logger.warning("语音克隆异常: %s", e)
            return {
                "voice_id": "",
                "status": "error",
                "message": str(e),
            }

    async def design_voice(
        self,
        description: str,
        voice_name: str,
        **kwargs,
    ) -> dict[str, Any]:
        """
        设计音色（使用mimo-v2.5-tts-voicedesign）

        Args:
            description: 音色描述（如"温柔的女声，带有一点磁性"）
            voice_name: 音色名称
            gender: 性别（male/female，可选）
            age_group: 年龄段（young/adult/elder，可选）

        Returns:
            {"voice_id": str, "status": str, "message": str}
        """
        if self._model != "mimo-v2.5-tts-voicedesign":
            return {
                "voice_id": "",
                "status": "error",
                "message": "当前模型不支持音色设计，请使用mimo-v2.5-tts-voicedesign",
            }

        payload: dict[str, Any] = {
            "voice_name": voice_name,
            "description": description,
        }

        if "gender" in kwargs:
            payload["gender"] = kwargs["gender"]
        if "age_group" in kwargs:
            payload["age_group"] = kwargs["age_group"]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.API_BASE}{self.ENDPOINTS['voicedesign']}",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30.0),
                ) as response:
                    result = await response.json()
                    if response.status == 200:
                        voice_id = result.get("voice_id", "")
                        self._voice_id = voice_id
                        logger.info("音色设计成功: %s -> %s", voice_name, voice_id)
                        return {
                            "voice_id": voice_id,
                            "status": "success",
                            "message": "音色设计成功",
                        }
                    else:
                        error_msg = result.get("error", "未知错误")
                        logger.warning("音色设计失败: %s", error_msg)
                        return {
                            "voice_id": "",
                            "status": "error",
                            "message": error_msg,
                        }

        except Exception as e:
            logger.warning("音色设计异常: %s", e)
            return {
                "voice_id": "",
                "status": "error",
                "message": str(e),
            }

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        return {
            "available": self._available,
            "engine": self.name,
            "model": self._model,
            "voice_id": self._voice_id,
            "fallback_enabled": self._fallback_local,
            "last_error": self._last_error,
        }

    def set_voice_id(self, voice_id: str) -> None:
        """设置音色ID（用于切换克隆音色）"""
        self._voice_id = voice_id
        logger.info("MiMo TTS切换音色: %s", voice_id)
