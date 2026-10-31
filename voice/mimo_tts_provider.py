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
import base64
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from utils.project_paths import project_path, resolve_project_path

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

    # API端点（2026-10-30 对齐官方现行协议：TTS 三模型统一走 OpenAI Chat
    # Completions 兼容接口；旧的 /audio/speech 与 /audio/voice-* 均已 404）
    API_BASE = "https://api.xiaomimimo.com/v1"
    ENDPOINTS = {
        "tts": "/chat/completions",
    }

    # 支持的模型（/v1/models 实测：v2.5 三款；mimo-v2-tts 已下线，仅作历史别名）
    SUPPORTED_MODELS = [
        "mimo-v2.5-tts",
        "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-tts-voicedesign",
    ]
    LEGACY_MODEL_ALIASES = {"mimo-v2-tts": "mimo-v2.5-tts"}

    # MiMo 预置音色（mimo-v2.5-tts）
    PRESET_VOICES = [
        "mimo_default", "冰糖", "茉莉", "苏打", "白桦",
        "Mia", "Chloe", "Milo", "Dean",
    ]
    # 历史预设名（旧 voice_catalog 三档）→ MiMo 现行音色，保证存量绑定不静默失效
    LEGACY_VOICE_ALIASES = {
        "default": "mimo_default",
        "female-tianmei": "冰糖",
        "female-qingxin": "茉莉",
        "male-chenwen": "白桦",
    }

    # 克隆参考音频落盘目录（voiceclone 无云端持久 voice_id，按请求内联样本）
    SAMPLES_DIR = project_path("data", "voice_samples")

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
        model = self.LEGACY_MODEL_ALIASES.get(model, model)
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
        # 官方 SSE 流返回的是 PCM16（且需自行拼帧），项目无流式消费者；
        # 本实现按非流式整体合成返回，如实声明不支持流式。
        return False

    def _get_headers(self) -> dict[str, str]:
        """获取API请求头（官方文档使用 api-key 头；Bearer 亦兼容）"""
        return {
            "api-key": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _map_emotion(self, emotion: str) -> dict[str, Any]:
        """将中文情感映射到MiMo参数"""
        return self.EMOTION_MAPPING.get(emotion, self.EMOTION_MAPPING["平常"]).copy()

    #: 情感 → 自然语言风格指令（现行 API 无 speed/pitch 数值字段，走指令遵循）
    EMOTION_STYLE_HINTS: dict[str, str] = {
        "开心": "语气开心、轻快，声音明亮上扬。",
        "撒娇": "语气撒娇、软糯，尾音略微上扬。",
        "温柔": "语气温柔、轻柔，语速稍慢。",
        "伤心": "语气难过、低落，语速偏慢。",
        "生气": "语气生气、强硬，语速偏快。",
        "害怕": "语气紧张、怯弱，声音略颤。",
        "害羞": "语气害羞、腼腆，声音轻。",
        "傲娇": "语气傲娇，表面强硬但带一点别扭。",
        "平常": "语气自然、口语化，像真人日常说话。",
    }

    def _style_instruction(self, emotion: str, speed: float, pitch: float) -> str:
        """把情感 + 数值比例转成一段自然语言风格指令（现行 API 的等价表达）。"""
        parts = [self.EMOTION_STYLE_HINTS.get(emotion, self.EMOTION_STYLE_HINTS["平常"])]
        if abs(speed - 1.0) > 0.02:
            parts.append(f"语速约为常速的 {speed:.2f} 倍。")
        if abs(pitch - 1.0) > 0.02:
            parts.append("音调略高。" if pitch > 1.0 else "音调略低。")
        return "".join(parts)

    def _normalize_preset_voice(self, voice: str) -> str:
        """预设音色名归一：历史别名映射到现行音色，空值回落 mimo_default。"""
        voice = (voice or "").strip()
        if not voice:
            return "mimo_default"
        alias = self.LEGACY_VOICE_ALIASES.get(voice)
        if alias:
            return alias
        if voice not in self.PRESET_VOICES:
            logger.warning("未知的 MiMo 预置音色 %r，回退 mimo_default", voice)
            return "mimo_default"
        return voice

    def _resolve_clone_voice(self, voice: str) -> str:
        """voiceclone 的 audio.voice：优先 data URL，其次 catalog 的本地样本文件。"""
        voice = (voice or "").strip()
        if voice.startswith("data:audio/"):
            return voice
        if not voice:
            return ""
        try:
            from voice.voice_catalog import get_voice_catalog

            entry = get_voice_catalog().get(voice) or {}
            sample_path = str(entry.get("sample_path") or "")
            if sample_path:
                path = resolve_project_path(sample_path)
                if path.exists():
                    raw = path.read_bytes()
                    mime = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
                    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                logger.warning("克隆音色样本不存在: %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("解析克隆音色失败: %s", e)
        return ""

    def _resolve_design_prompt(self, voice: str) -> str:
        """voicedesign 的 user 消息：catalog 条目 description 或调用方直接给的描述。"""
        voice = (voice or "").strip()
        if not voice:
            return "自然、温柔的年轻女声，普通话标准，语速适中。"
        try:
            from voice.voice_catalog import get_voice_catalog

            entry = get_voice_catalog().get(voice) or {}
            return str(entry.get("description") or voice)
        except Exception:  # noqa: BLE001
            return voice

    @staticmethod
    def _extract_audio_base64(result: dict[str, Any]) -> str:
        """从 chat.completions 响应取 choices[0].message.audio.data（base64）。"""
        choices = result.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        audio = message.get("audio") or {}
        return str(audio.get("data") or "")

    def _resolve_call_params(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """解析一次合成的参数快照（W7 契约）。

        model / voice_id / speed / pitch 均可由调用方按角色契约逐次传入；
        未传时才回退实例默认。**不写任何实例状态**——并发多角色合成各用各的
        快照，禁止以 provider 全局态模拟角色（必串音）。
        """
        emotion = str(kwargs.get("emotion") or "")
        emotion_params = self._map_emotion(emotion)

        model = str(kwargs.get("model") or "")
        model = self.LEGACY_MODEL_ALIASES.get(model, model)
        if model and model not in self.SUPPORTED_MODELS:
            logger.warning("未知的 MiMo 模型 %r，回退引擎默认 %s", model, self._model)
            model = ""
        model = model or self._model

        # 空值保持空串：由各模型分支决定默认（tts→mimo_default / clone→样本 / design→描述）
        voice = str(kwargs.get("voice_id") or "") or self._voice_id

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
        model = params["model"]
        voice = params["voice"]
        style = self._style_instruction(
            params["emotion"], params["speed"], params["pitch"],
        )

        audio_cfg: dict[str, Any] = {"format": "mp3"}
        user_content = style
        if model == "mimo-v2.5-tts-voiceclone":
            voice_url = self._resolve_clone_voice(voice)
            if not voice_url:
                self._last_error = "voiceclone 缺少参考音频（catalog 样本不存在）"
                logger.warning("MiMo TTS %s", self._last_error)
                if self._fallback_local:
                    return await self._fallback_to_local(text, **kwargs)
                return None
            audio_cfg["voice"] = voice_url
        elif model == "mimo-v2.5-tts-voicedesign":
            # 音色由 user 消息里的描述决定；audio 不支持 voice 字段
            user_content = self._resolve_design_prompt(voice)
        else:
            audio_cfg["voice"] = self._normalize_preset_voice(voice)

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": text},
            ],
            "audio": audio_cfg,
        }

        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['tts']}",
                headers=self._get_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    audio_b64 = self._extract_audio_base64(result)
                    if not audio_b64:
                        self._last_error = "API 返回空音频"
                        logger.warning("MiMo TTS API 返回空音频，按失败处理")
                    else:
                        audio_data = base64.b64decode(audio_b64)
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
        if model == "mimo-v2.5-tts-voiceclone":
            basic_result = await self._synthesize_with_basic_model(text, params)
            if basic_result is not None:
                return basic_result

        # API失败，尝试本地降级
        if self._fallback_local:
            return await self._fallback_to_local(text, **kwargs)

        return None

    async def _synthesize_with_basic_model(
        self, text: str, params: dict[str, Any],
    ) -> SynthesizedAudio | None:
        """voiceclone 失败时降级到 mimo-v2.5-tts 基础合成（同 API，不依赖本地引擎）"""
        basic_payload: dict[str, Any] = {
            "model": "mimo-v2.5-tts",
            "messages": [
                {"role": "user", "content": self._style_instruction(
                    params["emotion"], params["speed"], params["pitch"],
                )},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "mp3", "voice": "mimo_default"},
        }
        try:
            async with aiohttp.ClientSession() as session, session.post(
                f"{self._api_base}{self.ENDPOINTS['tts']}",
                headers=self._get_headers(),
                json=basic_payload,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    audio_b64 = self._extract_audio_base64(result)
                    if audio_b64:
                        audio_data = base64.b64decode(audio_b64)
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
        流式合成语音（兼容接口）

        现行官方流式为 SSE + PCM16 分帧，项目无流式消费者；本方法退化为
        一次性合成后按块产出 MP3，调用方语义（逐块拿到可播放字节）保持不变。
        """
        chunk_size = int(kwargs.get("chunk_size") or 8192)
        audio = await self.synthesize(text, **kwargs)
        if audio is None:
            return
        data = audio.data
        for offset in range(0, len(data), chunk_size):
            yield data[offset:offset + chunk_size]

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
        if not audio_data:
            return {"voice_id": "", "status": "error", "message": "参考音频为空"}

        try:
            # 官方 v2.5 已取消「创建音色返回 voice_id」接口：voiceclone 每次合成
            # 在 audio.voice 内联 data URL 样本。本地登记一个 voice_id 指向样本文件，
            # 合成时由 _resolve_clone_voice 还原成 data URL。
            ext = "wav" if audio_data[:4] == b"RIFF" else "mp3"
            voice_id = f"clone_{uuid.uuid4().hex[:12]}"
            sample_path = self.SAMPLES_DIR / f"{voice_id}.{ext}"
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sample_path.write_bytes(audio_data)
            try:
                rel_path = os.path.relpath(sample_path, project_path())
            except ValueError:
                # Windows 跨盘符（测试 tmp_path 在 C:、项目在 D:）时保留绝对路径
                rel_path = str(sample_path)

            # W7：不写 self._voice_id——克隆产物由 voice.catalog 持久化，
            # provider 全局默认音色只能经显式 set_voice_id 变更
            logger.info("克隆参考音频已登记: %s -> %s (%d bytes)",
                        voice_name, voice_id, len(audio_data))
            return {
                "voice_id": voice_id,
                "status": "success",
                "message": "参考音频已保存，合成时内联用于音色克隆",
                "sample_path": rel_path,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("语音克隆登记异常: %s", e)
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
        description = (description or "").strip()
        if not description:
            return {"voice_id": "", "status": "error", "message": "音色描述不能为空"}

        # 官方 v2.5 无「创建音色返回 voice_id」接口：设计描述在每次合成时作为
        # user 消息交给 voicedesign 模型。本地登记 voice_id，catalog 保存描述。
        voice_id = f"design_{uuid.uuid4().hex[:12]}"
        logger.info("音色设计描述已登记: %s -> %s", voice_name, voice_id)
        return {
            "voice_id": voice_id,
            "status": "success",
            "message": "音色描述已登记，合成时作为 voicedesign 指令",
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
