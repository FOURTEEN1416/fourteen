"""PNG tEXt chunk 编解码 — SillyTavern 角色卡 V2/V3 生态标准。

SillyTavern 规范:
  - PNG tEXt chunk 关键字: ``chara``
  - 值: base64 编码的 UTF-8 JSON 字符串
  - 导入: 读 PNG → 提取 tEXt ``chara`` → base64 解码 → JSON 解析
  - 导出: JSON → base64 编码 → 嵌入 PNG tEXt ``chara``

参考:
  - SillyTavern character-card 源码
  - PNG 规范 (http://www.libpng.org/pub/png/spec/1.2/PNG-Chunks.html)

依赖: Pillow (PIL)
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any

logger = logging.getLogger("shisi.character.png_codec")

# SillyTavern 标准 tEXt 关键字
CHARA_KEYWORD = "chara"

try:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class PNGCodecError(Exception):
    """PNG 编解码异常"""


def is_png(data: bytes) -> bool:
    """通过魔术字节判断是否为 PNG 文件"""
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def extract_card_from_png(data: bytes) -> dict[str, Any]:
    """从 PNG 字节流提取角色卡 JSON 字典。

    Args:
        data: PNG 文件字节内容

    Returns:
        角色卡字典（已 JSON 解析）

    Raises:
        PNGCodecError: Pillow 不可用 / 不是 PNG / 无 chara chunk / JSON 解析失败
    """
    if not HAS_PIL:
        raise PNGCodecError("Pillow 未安装，无法解析 PNG（pip install Pillow）")
    if not is_png(data):
        raise PNGCodecError("不是有效的 PNG 文件")

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise PNGCodecError(f"PNG 解析失败: {e}") from e

    # 读取 tEXt chunk
    text_chunks: dict[str, str] = {}
    if hasattr(img, "text") and img.text:
        text_chunks = dict(img.text)
    else:
        # 某些 PIL 版本需要显式读取 info
        if hasattr(img, "info") and img.info:
            text_chunks = {k: v for k, v in img.info.items() if isinstance(v, str)}

    if CHARA_KEYWORD not in text_chunks:
        raise PNGCodecError(
            f"PNG 中未找到 tEXt chunk '{CHARA_KEYWORD}'（非 SillyTavern 角色卡）"
        )

    b64_value = text_chunks[CHARA_KEYWORD]
    try:
        json_bytes = base64.b64decode(b64_value)
    except Exception as e:  # noqa: BLE001
        raise PNGCodecError(f"base64 解码失败: {e}") from e

    try:
        return json.loads(json_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PNGCodecError(f"JSON 解析失败: {e}") from e


def embed_card_to_png(
    card_data: dict[str, Any],
    image_bytes: bytes | None = None,
    width: int = 400,
    height: int = 600,
    color: tuple[int, int, int] = (30, 30, 45),
) -> bytes:
    """将角色卡 JSON 嵌入 PNG tEXt chunk，返回 PNG 字节流。

    Args:
        card_data: 角色卡字典（将被 JSON 序列化）
        image_bytes: 可选的底图 PNG 字节（通常是角色头像）；
                     为 None 时生成纯色占位图
        width: 占位图宽度（仅 image_bytes 为 None 时生效）
        height: 占位图高度（仅 image_bytes 为 None 时生效）
        color: 占位图 RGB 颜色（仅 image_bytes 为 None 时生效）

    Returns:
        嵌入了 chara tEXt chunk 的 PNG 字节流

    Raises:
        PNGCodecError: Pillow 不可用 / 底图无效
    """
    if not HAS_PIL:
        raise PNGCodecError("Pillow 未安装，无法生成 PNG（pip install Pillow）")

    # 准备底图
    if image_bytes is not None:
        if not is_png(image_bytes):
            raise PNGCodecError("提供的底图不是有效的 PNG")
        try:
            img = Image.open(io.BytesIO(image_bytes))
            # 转为 RGB 模式确保可保存为 PNG
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
        except Exception as e:  # noqa: BLE001
            raise PNGCodecError(f"底图解析失败: {e}") from e
    else:
        # 生成纯色占位图
        img = Image.new("RGB", (width, height), color)

    # 构建 tEXt chunk
    json_str = json.dumps(card_data, ensure_ascii=False)
    b64_value = base64.b64encode(json_str.encode("utf-8")).decode("ascii")

    pnginfo = PngInfo()
    pnginfo.add_text(CHARA_KEYWORD, b64_value, zip=False)  # tEXt (不压缩)

    # 保存到内存
    out = io.BytesIO()
    img.save(out, format="PNG", pnginfo=pnginfo)
    result = out.getvalue()

    logger.debug(
        "PNG 角色卡已嵌入: %s bytes, name=%s",
        len(result),
        card_data.get("data", {}).get("name", card_data.get("name", "?")),
    )
    return result


def has_chara_chunk(data: bytes) -> bool:
    """快速判断 PNG 是否包含 chara tEXt chunk（不抛异常）"""
    try:
        extract_card_from_png(data)
        return True
    except PNGCodecError:
        return False
