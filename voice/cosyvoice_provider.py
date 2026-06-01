"""
CosyVoice 提供者 - 本地语音合成

直接加载 CosyVoice-300M-Instruct 模型进行文本到语音合成。
支持预训练音色（SFT）和自然语言控制（Instruct）模式。

注意：首次使用需要等待模型加载（约 10-30 秒，CPU 推理约 7x 实时）。
所有同步操作通过 run_in_executor 避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import sys
import threading
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np

from .tts_provider_base import TTSProviderBase

logger = logging.getLogger("voice.cosyvoice")

MODEL_DIR = os.path.join(
    os.path.expanduser("~"),
    "cosyvoice_models", "cosyvoice", "CosyVoice-300M-Instruct",
)
COSYVOICE_REPO = os.path.join(os.environ.get("TEMP", ""), "cosyvoice_repo")
MATCHA_TTS = os.path.join(COSYVOICE_REPO, "third_party", "Matcha-TTS")

MAX_TEXT_LENGTH = 5000
SYNTHESIS_TIMEOUT = 300  # CPU 推理的超时秒数

# 全局单例，避免重复加载模型
_cosyvoice_instance = None
_cosyvoice_lock = threading.Lock()


def _get_cosyvoice():
    """懒加载 CosyVoice 模型（线程安全）"""
    global _cosyvoice_instance
    if _cosyvoice_instance is not None:
        return _cosyvoice_instance

    with _cosyvoice_lock:
        if _cosyvoice_instance is not None:
            return _cosyvoice_instance

        if COSYVOICE_REPO not in sys.path:
            sys.path.insert(0, COSYVOICE_REPO)
        if MATCHA_TTS not in sys.path:
            sys.path.insert(0, MATCHA_TTS)

        from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore[import-untyped]

        logger.info("正在加载 CosyVoice 模型（%s）...", MODEL_DIR)
        _cosyvoice_instance = CosyVoice(MODEL_DIR, load_jit=False, device="cpu")
        logger.info(
            "CosyVoice 加载完成！可用音色: %s",
            _cosyvoice_instance.list_avaliable_spks(),
        )
        return _cosyvoice_instance


def _ensure_model_loaded():
    """在后台线程中确保模型已加载，返回是否首次加载耗时"""
    global _cosyvoice_instance
    if _cosyvoice_instance is not None:
        return False
    _get_cosyvoice()
    return True


def _sync_synthesize(text: str, voice: str, instruct: str | None) -> dict[str, Any]:
    """同步执行推理（在 executor 线程中运行）"""
    cosyvoice = _get_cosyvoice()
    if instruct:
        return next(cosyvoice.inference_instruct(text, voice, instruct, stream=False))
    return next(cosyvoice.inference_sft(text, voice, stream=False))


def _tensor_to_wav_bytes(tensor, sample_rate: int = 22050) -> bytes:
    """将 PyTorch tensor 转为 WAV 字节"""
    arr = tensor.cpu().numpy().flatten()
    arr = np.clip(arr, -1.0, 1.0)
    buf = io.BytesIO()
    data = (arr * 32767).astype(np.int16).tobytes()
    data_size = len(data)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", data_size + 36))
    buf.write(b"WAVE")
    buf.write(struct.pack("<I", 16))
    buf.write(struct.pack("<H", 1))
    buf.write(struct.pack("<H", 1))
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))
    buf.write(struct.pack("<H", 2))
    buf.write(struct.pack("<H", 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(data)
    return buf.getvalue()


class CosyVoiceProvider(TTSProviderBase):
    """
    CosyVoice 语音合成（本地模型）

    所有 CPU 密集推理在后台线程执行，不阻塞事件循环。
    """

    def __init__(
        self,
        voice: str = "中文女",
        instruct_prompt: str = "用温柔的语气说话",
        timeout: float = SYNTHESIS_TIMEOUT,
    ):
        self._voice = voice
        self._instruct_prompt = instruct_prompt
        self._timeout = timeout
        self._available = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        self._available = False

    @property
    def name(self) -> str:
        return "cosyvoice"

    async def synthesize(self, text: str, **kwargs) -> bytes | None:
        if not text or not text.strip():
            return None

        if len(text) > MAX_TEXT_LENGTH:
            logger.warning(
                "CosyVoice 输入文本超长 (%d > %d 字符)", len(text), MAX_TEXT_LENGTH
            )
            text = text[:MAX_TEXT_LENGTH]

        voice = kwargs.get("voice", self._voice)
        instruct = kwargs.get("instruct", self._instruct_prompt)

        try:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _sync_synthesize, text, voice, instruct),
                timeout=self._timeout,
            )

            wav_bytes = _tensor_to_wav_bytes(result["tts_speech"])
            self._available = True
            return wav_bytes

        except asyncio.TimeoutError:
            logger.error("CosyVoice 合成超时（%s 秒）", self._timeout)
            self._available = False
            return None
        except Exception as e:  # noqa: BLE001
            logger.error("CosyVoice 合成失败: %s", e, exc_info=True)
            self._available = False
            return None

    def health_check(self) -> dict:
        return {
            "engine": "cosyvoice",
            "available": self._available,
            "model": MODEL_DIR,
            "voice": self._voice,
            "instruct": self._instruct_prompt,
        }

    @property
    def supports_streaming(self) -> bool:
        return False

    async def synthesize_stream(self, text: str, **kwargs) -> AsyncGenerator[bytes, None]:
        audio = await self.synthesize(text, **kwargs)
        if audio:
            yield audio
