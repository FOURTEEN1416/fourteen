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

    def to_wav(self, audio_bytes: bytes, source_format: str = "silk", sample_rate: int = 16000) -> bytes | None:
        """任意格式→WAV 16k mono（ASR 前置转换）。

        微信语音是 silk，而标准 ffmpeg **不带 silk 解码器**，直接 `-f silk` 必然失败
        （实测 ffmpeg 8.1 essentials 无 silk decoder）。故 silk 源先由 pilk 解码成
        PCM(s16le)，再交 ffmpeg 封装为 WAV。
        """
        if source_format == "silk":
            wav = self._silk_to_wav(audio_bytes, sample_rate)
            if wav:
                return wav
        return self._ffmpeg_convert(
            audio_bytes, source_format=source_format,
            target_format="wav", sample_rate=sample_rate, channels=1,
        )

    def _silk_to_wav(self, silk_bytes: bytes, sample_rate: int = 16000) -> bytes | None:
        """silk（含微信 tencent 变体）→ WAV。依赖 pilk（pip install pilk）。

        用 pilk.silk_to_wav 而非 decode+ffmpeg：silk 内部采样率与 ASR 所需（16k）
        未必一致，由 pilk 按 rate 参数正确处理重采样，避免音速/音高失真。
        """
        try:
            import os
            import tempfile

            import pilk
        except ImportError:
            logger.debug("pilk 未安装，silk 解码不可用（pip install pilk）")
            return None
        silk_path = wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as sf:
                sf.write(silk_bytes)
                silk_path = sf.name
            wav_path = silk_path.replace(".silk", ".wav")
            # 微信 silk 与标准 SILK 的差异（开头 \x02、结尾无 \xFF\xFF）由 pilk 内部处理
            pilk.silk_to_wav(silk_path, wav_path, rate=sample_rate)
            with open(wav_path, "rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            logger.warning("pilk silk 解码失败: %s", e)
            return None
        finally:
            import os

            for p in (silk_path, wav_path):
                try:
                    if p:
                        os.unlink(p)
                except Exception:  # noqa: BLE001
                    pass

    def to_amr(self, audio_bytes: bytes, source_format: str = "mp3") -> bytes | None:
        return self._ffmpeg_convert(
            audio_bytes, source_format, "amr",
            sample_rate=8000, channels=1,
        )

    def _wav_to_silk(self, wav_bytes: bytes) -> bytes | None:
        # 优先 pilk：能正确产出微信需要的 tencent 变体 silk
        silk = self._pilk_encode_silk(wav_bytes)
        if silk:
            return silk
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

    def _pilk_encode_silk(self, wav_bytes: bytes) -> bytes | None:
        """WAV → silk（微信 tencent 变体）。依赖 pilk（pip install pilk）。"""
        try:
            import os
            import tempfile

            import pilk
        except ImportError:
            logger.debug("pilk 未安装，走 pysilk / silk_encoder 兜底")
            return None
        wav_path = pcm_path = silk_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wf:
                wf.write(wav_bytes)
                wav_path = wf.name
            pcm = self._ffmpeg_convert(
                wav_bytes, source_format="wav", target_format="s16le",
                sample_rate=self._silk_sample_rate, channels=1,
            )
            if not pcm:
                return None
            pcm_path = wav_path.replace(".wav", ".pcm")
            with open(pcm_path, "wb") as f:
                f.write(pcm)
            silk_path = wav_path.replace(".wav", ".silk")
            pilk.encode(pcm_path, silk_path, pcm_rate=self._silk_sample_rate, tencent=True)
            with open(silk_path, "rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            logger.warning("pilk silk 编码失败: %s", e)
            return None
        finally:
            import os

            for p in (wav_path, pcm_path, silk_path):
                try:
                    if p:
                        os.unlink(p)
                except Exception:  # noqa: BLE001
                    pass

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
        has_pilk = False
        try:
            import pilk  # noqa: F401

            has_pilk = True
        except ImportError:
            has_pilk = False
        return {
            "ffmpeg": self._has_ffmpeg,
            "silk_encoder": self._has_silk,
            # silk 解码能力：决定入站微信语音能否转成文字
            "pilk": has_pilk,
        }
