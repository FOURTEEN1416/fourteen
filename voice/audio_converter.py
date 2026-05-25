"""AudioFormatConverter - 音频格式转换：MP3/WAV → SILK/AMR"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger("voice.audio_converter")


class AudioFormatConverter:
    def __init__(self, ffmpeg_path: str = "ffmpeg", silk_sample_rate: int = 24000):
        self._ffmpeg_path = ffmpeg_path
        self._silk_sample_rate = silk_sample_rate
        self._has_ffmpeg: bool | None = None
        self._has_silk: bool | None = None

    def to_silk(self, audio_bytes: bytes, source_format: str = "mp3") -> bytes | None:
        wav_bytes = self._ffmpeg_convert(
            audio_bytes, source_format, "wav",
            sample_rate=self._silk_sample_rate, channels=1,
        )
        if wav_bytes is None:
            return None
        return self._wav_to_silk(wav_bytes)

    def to_amr(self, audio_bytes: bytes, source_format: str = "mp3") -> bytes | None:
        return self._ffmpeg_convert(
            audio_bytes, source_format, "amr",
            sample_rate=8000, channels=1,
        )

    def _wav_to_silk(self, wav_bytes: bytes) -> bytes | None:
        try:
            from pysilk import encode
            result = encode(wav_bytes, sample_rate=self._silk_sample_rate)
            return result if isinstance(result, bytes) else None
        except ImportError:
            logger.debug("pysilk not installed, trying silk-v3-decoder CLI")
            return self._silk_cli_encode(wav_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning("SILK编码失败: %s", e)
            return None

    def _silk_cli_encode(self, wav_bytes: bytes) -> bytes | None:
        import tempfile
        try:
            silk_bin = shutil.which("silk_encoder")
            if not silk_bin:
                logger.warning("silk_encoder CLI not found")
                return None
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wf:
                wf.write(wav_bytes)
                wav_path = wf.name
            out_path = wav_path.replace(".wav", ".silk")
            subprocess.run(
                [silk_bin, wav_path, out_path, "-tencent", "-fs", str(self._silk_sample_rate)],
                capture_output=True, timeout=10,
            )
            with open(out_path, "rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            logger.warning("SILK CLI编码失败: %s", e)
            return None
        finally:
            try:
                import os
                os.unlink(wav_path)
                os.unlink(out_path)
            except Exception:  # noqa: BLE001
                pass

    def _ffmpeg_convert(
        self,
        audio_bytes: bytes,
        source_format: str,
        target_format: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> bytes | None:
        if not audio_bytes:
            return None
        try:
            cmd = [
                self._ffmpeg_path, "-y",
                "-f", source_format, "-i", "pipe:0",
                "-ar", str(sample_rate),
                "-ac", str(channels),
                "-f", target_format, "pipe:1",
            ]
            result = subprocess.run(
                cmd, input=audio_bytes,
                capture_output=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
            logger.warning("ffmpeg转换失败: %s", result.stderr.decode(errors="replace")[:200])
            return None
        except FileNotFoundError:
            logger.error("ffmpeg未找到: %s", self._ffmpeg_path)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg转换超时")
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("ffmpeg异常: %s", e)
            return None

    def health_check(self) -> dict:
        if self._has_ffmpeg is None:
            self._has_ffmpeg = shutil.which(self._ffmpeg_path) is not None
        if self._has_silk is None:
            try:
                from pysilk import encode  # noqa: F401
                self._has_silk = True
            except ImportError:
                self._has_silk = shutil.which("silk_encoder") is not None
        return {
            "ffmpeg": self._has_ffmpeg,
            "silk_encoder": self._has_silk,
        }
