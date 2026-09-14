"""入站图片附件归一化（V2 图片通道）。

职责边界（不做的事：不落盘、不上传、不调 LLM）：
- 把微信入站的 image_data（裸 base64 或 data URL）归一化为 LLM 可消费的 data URL
- 按 magic bytes 判定图片类型，避免把 jpeg 标成 png 导致视觉模型解析失败
- 构造 OpenAI 兼容的 content part（{"type": "image_url", ...}）

隐私：全程只在内存处理，日志中不输出图片内容。
"""
from __future__ import annotations

import base64
import binascii

# magic bytes -> (mime, 扩展名)
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP
    (b"BM", "image/bmp"),
]

DEFAULT_MIME = "image/jpeg"


def sniff_mime(raw: bytes) -> str:
    """按 magic bytes 判定图片类型；无法判定时回落 jpeg。"""
    if raw.startswith(b"RIFF") and b"WEBP" not in raw[:16]:
        return DEFAULT_MIME
    for sig, mime in _SIGNATURES:
        if raw.startswith(sig):
            return mime
    return DEFAULT_MIME


def to_data_url(image_data: str) -> str | None:
    """归一化成 data URL。

    入站 image_data 可能是：
    - 已经是 data URL（部分通道直接给）→ 原样返回
    - 裸 base64 → 解码嗅探类型后补 data URL 前缀
    返回 None 表示无法解析（调用方应走降级）。
    """
    if not image_data or not isinstance(image_data, str):
        return None
    s = image_data.strip()
    if s.startswith("data:"):
        return s
    if s.startswith(("http://", "https://")):
        return s  # 视觉模型多数也接受公网 URL
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    mime = sniff_mime(raw)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def build_image_part(image_data: str) -> dict | None:
    """构造 OpenAI 兼容的 content part。"""
    url = to_data_url(image_data)
    if not url:
        return None
    return {"type": "image_url", "image_url": {"url": url}}


def build_attachments(image_datas: list[str]) -> list[dict]:
    """批量构造；无法解析的条目跳过（不抛异常，交给上层降级）。"""
    parts = []
    for d in image_datas:
        part = build_image_part(d)
        if part:
            parts.append(part)
    return parts
