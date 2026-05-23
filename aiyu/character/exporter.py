"""PersonaExporter导出器 — 导出为chara_card_v2 JSON，可重新导入。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import CharaCardV2

logger = logging.getLogger("aiyu.character.exporter")


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
