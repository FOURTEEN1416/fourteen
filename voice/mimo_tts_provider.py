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
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import aiohttp

from .audio_result import SynthesizedAudio, coerce_ratio
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
        base_url: str | None = None,
    ):
        """
        初始化MiMo TTS提供者

        Args:
            api_key: MiMo API密钥
            model: 模型名称，默认mimo-v2.5-tts
            voice_id: 克隆音色ID（使用voiceclone时需要）
            timeout: API调用超时时间
            fallback_local: API失败时是否降级到本地引擎
            base_url: 自定义 API Base URL，为空时使用默认地址
        """
        self._api_key = api_key
        self._model = model if model in self.SUPPORTED_MODELS else "mimo-v2.5-tts"
        self._voice_id = voice_id
        self._timeout = timeout
        self._fallback_local = fallback_local
        self._api_base = base_url or self.API_BASE
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

    def _resolve_call_params(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """解析一次合成的参数快照（W7 契约）。

        model / voice_id / speed / pitch 均可由调用方按角色契约逐次传入；
        未传时才回退实例默认。**不写任何实例状态**——并发多角色合成各用各的
        快照，禁止以 provider 全局态模拟角色（必串音）。
        """
        emotion = str(kwargs.get("emotion") or "")
        emotion_params = self._map_emotion(emotion)

        model = str(kwargs.get("model") or "")
        if model and model not in self.SUPPORTED_MODELS:
            logger.warning("未知的 MiMo 模型 %r，回退引擎默认 %s", model, self._model)
            model = ""
        model = model or self._model

        voice = str(kwargs.get("voice_id") or "") or self._voice_id or "default"

        speed = coerce_ratio(kwargs.get("speed"))
        if speed is None:
            speed = coerce_ratio(emotion_params.get("speed")) or 1.0
        pitch = coerce_ratio(kwargs.get("pitch"))
        if pitch is None:
            pitch = coerce_ratio(emotion_params.get("pitch")) or 1.0

        return {
            "emotion": emotion,
            "model": model,
            "voice": voice,
            "speed": speed,
            "pitch": pitch,
        }

    async def synthesize(self, text: str, **kwargs) -> SynthesizedAudio | None:
        """
        合成语音

        Args:
            text: 要合成的文本
            emotion: 情感状态（可选）
            model: 本次合成使用的 MiMo 模型（可选，角色契约快照）
            voice_id: 本次合成使用的音色 ID/预设名（可选，角色契约快照）
            speed: 语速比例（可选，覆盖情感参数）
            pitch: 音调比例（可选，覆盖情感参数）

        Returns:
            SynthesizedAudio（云端为 mp3；SAPI 本地兜底为 wav），失败返回 None
        """
        if not text:
            return None

        params = self._resolve_call_params(kwargs)

        payload: dict[str, Any] = {
            "model": params["model"],
            "input": text,
            "voice": params["voice"],
            "speed": params["speed"],
            "pitch": params["pitch"],
            "response_format": "mp3",
        }

        # 如果是voiceclone模式，添加voice_id
        if (
            params["model"] == "mimo-v2.5-tts-voiceclone"
            and params["voice"] not in ("", "default")
        ):
            payload["voice_id"] = params["voice"]

        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['tts']}",
                headers=self._get_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status == 200:
                    audio_data = await response.read()
                    if not audio_data:
                        self._last_error = "API 返回空音频"
                        logger.warning("MiMo TTS API 返回空音频，按失败处理")
                    else:
                        self._available = True
                        self._last_error = None
                        logger.debug("MiMo TTS合成成功: %d bytes", len(audio_data))
                        return SynthesizedAudio(data=audio_data, fmt="mp3")
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

        # voiceclone 模型失败时，先降级到 MiMo 基础合成（同 API，仅切换模型）
        if params["model"] == "mimo-v2.5-tts-voiceclone":
            basic_result = await self._synthesize_with_basic_model(
                text, params["speed"], params["pitch"],
            )
            if basic_result is not None:
                return basic_result

        # API失败，尝试本地降级
        if self._fallback_local:
            return await self._fallback_to_local(text, **kwargs)

        return None

    async def _synthesize_with_basic_model(
        self, text: str, speed: float, pitch: float,
    ) -> SynthesizedAudio | None:
        """voiceclone 失败时降级到 mimo-v2.5-tts 基础合成（同 API，不依赖本地引擎）"""
        basic_payload: dict[str, Any] = {
            "model": "mimo-v2.5-tts",
            "input": text,
            "voice": "default",
            "speed": speed,
            "pitch": pitch,
            "response_format": "mp3",
        }
        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['tts']}",
                headers=self._get_headers(),
                json=basic_payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status == 200:
                    audio_data = await response.read()
                    if audio_data:
                        logger.info(
                            "voiceclone 降级到 mimo-v2.5-tts 基础合成成功: %d bytes",
                            len(audio_data),
                        )
                        return SynthesizedAudio(data=audio_data, fmt="mp3")
                else:
                    error_text = await response.text()
                    logger.warning("基础模型降级也失败: API %s: %s", response.status, error_text)
        except Exception as e:  # noqa: BLE001
            logger.warning("基础模型降级异常: %s", e)
        return None

    async def synthesize_stream(self, text: str, **kwargs):
        """
        流式合成语音

        Args:
            text: 要合成的文本
            chunk_size: 每个chunk的大小（字节）
            model / voice_id: 角色契约快照（同 synthesize）

        Yields:
            音频数据chunks
        """
        chunk_size = kwargs.get("chunk_size", 8192)
        params = self._resolve_call_params(kwargs)

        payload: dict[str, Any] = {
            "model": params["model"],
            "input": text,
            "voice": params["voice"],
            "stream": True,
            "response_format": "mp3",
        }

        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['tts']}",
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
        """本地降级（2026-08-28 MiMo-only 收敛后）。

        历史四级本地降级链（CosyVoice/GPT-SoVITS/Bert-VITS2/Edge-TTS）已随引擎删除；
        MiMo-only 后 _fallback_to_local 由 _local_synth 承担（Windows SAPI/本地 mimo 引擎）。
        """
        return await self._local_synth(text, **kwargs)

    async def _local_synth(self, text: str, **kwargs) -> bytes | None:
        """本地合成兜底：Windows SAPI TTS（无需额外服务，离线可用）。

        生产目标环境为 Windows（AGENTS §3），SAPI 是唯一零依赖本地语音出口。
        非 Windows 或合成失败返回 None（调用方得到 None 语义不变）。
        """
        if sys.platform != "win32":
            logger.info("非 Windows 环境，无本地 TTS 兜底")
            return None
        try:
            import win32com.client  # pywin32（仅 Windows，延迟导入）

            def _sapi_synth() -> bytes | None:
                voice = kwargs.get("speaker_name") or ""
                pythoncom_ok = False
                try:
                    import pythoncom
                    pythoncom.CoInitialize()
                    pythoncom_ok = True
                except Exception:
                    pass
                try:
                    sapi = win32com.client.Dispatch("SAPI.SpVoice")
                    if voice:
                        for v in sapi.GetVoices():
                            if voice.lower() in str(v.GetDescription()).lower():
                                sapi.Voice = v
                                break
                    tmp = Path(tempfile.gettempdir()) / f"mimo_fallback_{os.getpid()}.wav"
                    stream = win32com.client.Dispatch("SAPI.SpFileStream")
                    from win32com.client import constants
                    stream.Format.Type = constants.SAFTFileFormat
                    stream.Open(str(tmp))
                    sapi.AudioOutputStream = stream
                    sapi.Speak(text)
                    stream.Close()
                    data = tmp.read_bytes()
                    tmp.unlink(missing_ok=True)
                    return data or None
                finally:
                    if pythoncom_ok:
                        pythoncom.CoUninitialize()

            result = await asyncio.to_thread(_sapi_synth)
            if result:
                logger.info("MiMo TTS 降级到 Windows SAPI 本地合成")
                # SAPI 产出 RIFF WAV——统一对象必须如实标 wav，不得沿用云端 mp3 假设
                return SynthesizedAudio(data=result, fmt="wav")
            return None
        except Exception as e:
            logger.warning("本地 SAPI 合成不可用: %s", e)
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

            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['voiceclone']}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                data=data,
                timeout=aiohttp.ClientTimeout(total=60.0),  # 克隆需要更长时间
            ) as response:
                result = await response.json()
                if response.status == 200:
                    voice_id = result.get("voice_id", "")
                    # W7：不再写 self._voice_id——克隆产物由 voice.catalog 持久化，
                    # provider 全局默认音色只能经显式 set_voice_id 变更
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
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['voicedesign']}",
                headers=self._get_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30.0),
            ) as response:
                result = await response.json()
                if response.status == 200:
                    voice_id = result.get("voice_id", "")
                    # W7：同 clone_voice，不写 provider 全局态（catalog 是唯一 owner）
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
