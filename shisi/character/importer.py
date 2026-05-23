"""PersonaImporter批量导入管道 — 逐文件校验→安全检测→存储→统计。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .chara_card_v2 import ParserDispatcher
from .models import CardFormat, CharaCardV2, ImportResult
from .validator import validate_card

logger = logging.getLogger("shisi.character.importer")


class PersonaImporter:
    def __init__(self, output_dir: Path | str = "data/characters"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def import_file(self, path: Path | str) -> tuple[CharaCardV2 | None, str | None]:
        p = Path(path)
        if not p.exists():
            return None, f"文件不存在: {p}"

        try:
            card, fmt = ParserDispatcher.parse_file(p)
        except Exception as e:
            return None, f"解析失败: {e}"

        errors = validate_card(card)
        if errors:
            return None, f"校验失败: {'; '.join(errors)}"

        self._save_card(card, fmt)
        return card, None

    def import_files(self, paths: list[Path | str]) -> ImportResult:
        result = ImportResult(total=len(paths))
        for path in paths:
            card, error = self.import_file(path)
            if error:
                result.failed += 1
                result.errors.append(f"{Path(path).name}: {error}")
                logger.warning("导入失败 %s: %s", path, error)
            else:
                result.success += 1
                char_id = self._make_character_id(card)  # type: ignore
                result.imported_ids.append(char_id)
                logger.info("导入成功: %s (%s)", card.data.name, char_id)  # type: ignore
        return result

    def import_directory(self, dir_path: Path | str, pattern: str = "*.json") -> ImportResult:
        d = Path(dir_path)
        if not d.exists():
            return ImportResult(errors=[f"目录不存在: {d}"])
        files = sorted(d.glob(pattern))
        return self.import_files(files)  # type: ignore

    def _save_card(self, card: CharaCardV2, fmt: CardFormat) -> Path:
        char_id = self._make_character_id(card)
        out_path = self.output_dir / f"{char_id}.json"
        data = card.model_dump(mode="json")
        data["_format"] = fmt.value
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return out_path

    @staticmethod
    def _make_character_id(card: CharaCardV2) -> str:
        name = card.data.name or "unnamed"
        return re.sub(r'[^\w\u4e00-\u9fff]', '_', name).strip('_')[:50]
