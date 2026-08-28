"""PersonaExporter导出器 — 导出为chara_card_v2 JSON 或 SillyTavern PNG，可重新导入。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import CharaCardV2

logger = logging.getLogger("shisi.character.exporter")


class PersonaExporter:
    def __init__(self, output_dir: Path | str = "data/characters"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_card(self, card: CharaCardV2, filename: str | None = None) -> Path:
        if filename is None:
            import re
            filename = re.sub(r'[^\w\u4e00-\u9fff]', '_', card.data.name).strip('_')[:50]
            filename = f"{filename}.json"

        out_path = self.output_dir / filename
        data = card.model_dump(mode="json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("导出角色: %s → %s", card.data.name, out_path)
        return out_path

    def export_json_string(self, card: CharaCardV2, indent: int = 2) -> str:
        data = card.model_dump(mode="json")
        return json.dumps(data, ensure_ascii=False, indent=indent)

    def export_card_png(
        self,
        card: CharaCardV2,
        image_bytes: bytes | None = None,
        filename: str | None = None,
    ) -> tuple[bytes, str]:
        """导出为 SillyTavern 标准 PNG 角色卡（chara tEXt chunk）。

        Args:
            card: CharaCardV2 模型
            image_bytes: 可选底图 PNG 字节（通常是角色头像）；
                         为 None 时生成纯色占位图
            filename: 输出文件名（None 时自动生成）

        Returns:
            (png_bytes, filename) — PNG 字节流和文件名
        """
        from .png_codec import PNGCodecError, embed_card_to_png

        if filename is None:
            import re
            safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '_', card.data.name).strip('_')[:50]
            filename = f"{safe_name}.png"

        card_data = card.model_dump(mode="json")
        try:
            png_bytes = embed_card_to_png(card_data, image_bytes=image_bytes)
        except PNGCodecError as e:
            logger.error("PNG 导出失败: %s", e)
            raise

        logger.info("导出 PNG 角色卡: %s → %s (%d bytes)", card.data.name, filename, len(png_bytes))
        return png_bytes, filename

    def export_card_png_to_file(
        self,
        card: CharaCardV2,
        image_bytes: bytes | None = None,
        filename: str | None = None,
    ) -> Path:
        """导出 PNG 角色卡并保存到 output_dir，返回文件路径。"""
        png_bytes, filename = self.export_card_png(card, image_bytes, filename)
        out_path = self.output_dir / filename
        out_path.write_bytes(png_bytes)
        return out_path
