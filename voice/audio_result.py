"""统一音频合成契约 — 输入参数归一 + 输出结果载体（W7，2026-09-27）

- 输入端：``coerce_ratio`` 把 speed/pitch 存储值归一为 MiMo 数值比例，
  非数值（如 SAPI 风格 "0Hz"）返回 None，不进云端 payload。
- 输出端：provider 返回 ``SynthesizedAudio``（不可变），不再裸 ``bytes``——
  云端 MiMo 产 MP3、本地 SAPI 兜底产 WAV，格式由实际来源决定，
  禁止调用方按固定格式假设（曾全员硬编 MP3/WAV 各错一半）。
- 跨进程边界（orchestrator → 微信调用点）用 ``voice_result_payload`` /
  ``coerce_voice_payload`` 编解码，字段为 data/format/mime。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MIME_BY_FORMAT: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}


def coerce_ratio(value: Any) -> float | None:
    """存储/线上值 → MiMo 数值比例；非数值（含 SAPI "0Hz"）→ None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class SynthesizedAudio:
    """一次合成的不可变结果。

    Attributes:
        data: 音频字节。
        fmt: 实际容器格式（"mp3" | "wav"），来自真实来源而非请求期望。
    """

    data: bytes
    fmt: str

    @property
    def mime(self) -> str:
        return _MIME_BY_FORMAT.get(self.fmt, "application/octet-stream")

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("SynthesizedAudio.data 必须是 bytes")
        if not self.fmt:
            raise ValueError("SynthesizedAudio.fmt 不能为空")

    def __len__(self) -> int:
        return len(self.data)


def voice_result_payload(audio: SynthesizedAudio | None) -> dict | None:
    """合成结果 → 跨模块传输 dict（orchestrator result["voice"] 的形态）。"""
    if audio is None:
        return None
    return {"data": audio.data, "format": audio.fmt, "mime": audio.mime}


def coerce_voice_payload(payload: object) -> tuple[bytes, str]:
    """传输 dict → (bytes, format)。微信侧消费入口，形状错误直接抛错不静默。"""
    if not isinstance(payload, dict):
        raise TypeError(f"voice payload 必须是 dict，实得 {type(payload).__name__}")
    data = payload.get("data")
    fmt = payload.get("format")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("voice payload 缺少非空 data 字节")
    if not isinstance(fmt, str) or not fmt:
        raise ValueError("voice payload 缺少 format 字符串")
    return bytes(data), fmt
