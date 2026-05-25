"""
微信语音处理器 — 处理微信语音的发送和接收
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("wechat_voice")


def _validate_temp_path(path: str) -> bool:
    """验证临时文件路径安全性"""
    # 检查路径遍历
    if ".." in path:
        return False
    # 检查特殊字符（命令注入风险）
    return not re.search(r"[;&|`$<>]", path)


class WeChatVoiceHandler:
    """
    微信语音处理器

    功能：
    - 文字转语音并发送
    - 接收语音并转文字
    - SILK格式转换
    """

    def __init__(
        self,
        sovits_api_url: str = "http://localhost:9880",
        whisper_api_url: str | None = None,
    ):
        self.sovits_api_url = sovits_api_url
        self.whisper_api_url = whisper_api_url

    def text_to_voice(
        self,
        text: str,
        persona_name: str,
        model_path: str | None = None,
    ) -> bytes | None:
        """
        文字转语音

        Args:
            text: 要转换的文字
            persona_name: 人设名称
            model_path: 声音模型路径

        Returns:
            音频数据（WAV格式）
        """
        try:
            import httpx

            # 调用GPT-SoVITS推理API
            payload = {
                "text": text,
                "text_language": "zh",
                "character": persona_name,
            }

            if model_path:
                payload["model_path"] = model_path

            response = httpx.post(
                f"{self.sovits_api_url}/tts",
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                return response.content
            else:
                logger.warning("TTS failed: %s", response.text)
                return None

        except Exception as e:  # noqa: BLE001

            logger.error("Text to voice failed: %s", e)
            return None

    def voice_to_text(self, audio_data: bytes) -> str | None:
        """
        语音转文字

        Args:
            audio_data: 音频数据

        Returns:
            识别的文字
        """
        if not self.whisper_api_url:
            logger.warning("Whisper API not configured")
            return None

        try:
            import httpx

            response = httpx.post(
                f"{self.whisper_api_url}/transcribe",
                files={"audio": ("voice.wav", audio_data, "audio/wav")},
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                return result.get("text", "")  # type: ignore[no-any-return]
            else:
                logger.warning("ASR failed: %s", response.text)
                return None
  # noqa: BLE001

        except Exception as e:  # noqa: BLE001

            logger.error("Voice to text failed: %s", e)
            return None

    def convert_to_silk(self, audio_data: bytes) -> bytes | None:
        """
        转换为SILK格式（微信语音格式）

        Args:
            audio_data: WAV格式音频数据

        Returns:
            SILK格式音频数据
        """
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_file.write(audio_data)
                wav_path = wav_file.name

            silk_path = wav_path.replace(".wav", ".silk")

            # 安全验证
            if not _validate_temp_path(wav_path) or not _validate_temp_path(silk_path):
                logger.warning("Invalid temp file path detected")
                Path(wav_path).unlink(missing_ok=True)
                return None

            # 使用ffmpeg转换（参数列表形式，避免命令注入）
            result = subprocess.run(
                ["ffmpeg", "-i", wav_path, "-f", "silk", silk_path],
                capture_output=True,
                timeout=10,
            )

            if result.returncode == 0:
                with open(silk_path, "rb") as f:
                    silk_data = f.read()

                # 清理临时文件
                Path(wav_path).unlink(missing_ok=True)
                Path(silk_path).unlink(missing_ok=True)

                return silk_data
            else:
                logger.warning("SILK conversion failed")  # noqa: BLE001
                return None  # noqa: BLE001


        except Exception as e:  # noqa: BLE001

            logger.error("Convert to SILK failed: %s", e)
            return None

    def convert_from_silk(self, silk_data: bytes) -> bytes | None:
        """
        从SILK格式转换

        Args:
            silk_data: SILK格式音频数据

        Returns:
            WAV格式音频数据
        """
        try:
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as silk_file:
                silk_file.write(silk_data)
                silk_path = silk_file.name

            wav_path = silk_path.replace(".silk", ".wav")

            # 使用ffmpeg转换
            result = subprocess.run(
                ["ffmpeg", "-f", "silk", "-i", silk_path, wav_path],
                capture_output=True,
                timeout=10,
            )

            if result.returncode == 0:
                with open(wav_path, "rb") as f:
                    wav_data = f.read()

                # 清理临时文件
                Path(silk_path).unlink(missing_ok=True)
                Path(wav_path).unlink(missing_ok=True)

                return wav_data  # noqa: BLE001
            else:
                logger.warning("WAV conversion failed")  # noqa: BLE001

                return None

        except Exception as e:  # noqa: BLE001

            logger.error("Convert from SILK failed: %s", e)
            return None

    def send_voice_message(
        self,
        text: str,
        persona_name: str,
        wechat_connector: Any,
    ) -> bool:
        """
        发送语音消息到微信

        Args:
            text: 要发送的文字
            persona_name: 人设名称
            wechat_connector: 微信连接器

        Returns:
            是否成功
        """
        # 文字转语音
        audio_data = self.text_to_voice(text, persona_name)
        if audio_data is None:
            return False

        # 转换为SILK
        silk_data = self.convert_to_silk(audio_data)
        if silk_data is None:
            return False  # noqa: BLE001

        # 发送
        try:  # noqa: BLE001

            wechat_connector.send_voice(silk_data)
            logger.info("Sent voice message for %s", persona_name)
            return True
        except Exception as e:  # noqa: BLE001

            logger.error("Send voice failed: %s", e)
            return False

    def receive_voice_message(
        self,
        silk_data: bytes,
    ) -> str | None:
        """
        接收并处理微信语音消息

        Args:
            silk_data: SILK格式语音数据

        Returns:
            识别的文字
        """
        # 转换为WAV
        wav_data = self.convert_from_silk(silk_data)
        if wav_data is None:
            return None

        # 语音转文字
        text = self.voice_to_text(wav_data)
        return text
