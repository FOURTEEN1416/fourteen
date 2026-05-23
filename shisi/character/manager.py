"""CharacterManager — 角色生命周期管理：加载/切换/列表/缓存。"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from ..config import get_config
from .chara_card_v2 import ParserDispatcher, to_persona_config
from .exporter import PersonaExporter
from .importer import PersonaImporter
from .models import CardFormat, CharaCardV2, CharacterState, ImportResult
from .store import CharacterStore
from .validator import ValidationError, validate_card

logger = logging.getLogger("shisi.character.manager")


class CharacterManager:
    def __init__(
        self,
        store: CharacterStore | None = None,
        cache_size: int | None = None,
    ):
        self.store = store or CharacterStore()
        self._cache_size = cache_size or get_config("character", "lru_cache_size", 5)
        self._cache: OrderedDict[str, CharaCardV2] = OrderedDict()
        self._active_id: str | None = None
        self._active_card: CharaCardV2 | None = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        active_id = self.store.get_active_id()
        if active_id:
            self._active_id = active_id
            card = self.load_character(active_id)
            self._active_card = card
        self._initialized = True
        logger.info("CharacterManager初始化完成, active=%s", self._active_id)

    def load_character(self, character_id: str) -> Optional[CharaCardV2]:
        if character_id in self._cache:
            self._cache.move_to_end(character_id)
            return self._cache[character_id]

        card = self.store.get_character(character_id)
        if card is None:
            return None

        self._cache[character_id] = card
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

        return card

    def switch_character(self, character_id: str) -> tuple[bool, str]:
        start = time.perf_counter()
        card = self.load_character(character_id)
        if card is None:
            return False, f"角色不存在: {character_id}"

        old_id = self._active_id
        active_id = self.store.set_active(character_id)
        if active_id is None:
            return False, f"设置活跃角色失败: {character_id}"

        self._active_id = character_id
        self._active_card = card
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info("角色切换: %s → %s (%.1fms)", old_id, character_id, elapsed_ms)
        return True, f"已切换到: {card.data.name}"

    def get_active(self) -> Optional[CharaCardV2]:
        return self._active_card

    def get_active_id(self) -> Optional[str]:
        return self._active_id

    def get_active_persona_config(self) -> dict | None:
        if self._active_card is None:
            return None
        return to_persona_config(self._active_card)

    def list_characters(self) -> list[CharacterState]:
        return self.store.list_characters()

    def import_character(self, path: Path | str) -> tuple[Optional[CharaCardV2], str | None]:
        importer = PersonaImporter()
        card, error = importer.import_file(path)
        if error:
            return None, error

        self.store.save_character(card, CardFormat.CHARA_CARD_V2)  # type: ignore
        return card, None

    def import_directory(self, dir_path: Path | str) -> ImportResult:
        importer = PersonaImporter()
        result = importer.import_directory(dir_path)
        for char_id in result.imported_ids:
            card = self.load_character_from_file(char_id)
            if card:
                db_id = self.store.save_character(card)
                logger.debug("存入DB: %s → %s", char_id, db_id)
        return result

    def load_character_from_file(self, char_id: str) -> Optional[CharaCardV2]:
        data_dir = Path(get_config("character", "data_dir", "data/characters"))
        path = data_dir / f"{char_id}.json"
        if not path.exists():
            return None
        try:
            card, _ = ParserDispatcher.parse_file(path)
            return card
        except Exception as e:
            logger.warning("加载角色卡失败 %s: %s", path, e)
            return None

    def export_character(self, character_id: str, output_dir: Path | str = "data/characters") -> Optional[Path]:
        card = self.load_character(character_id)
        if card is None:
            return None
        exporter = PersonaExporter(output_dir)
        return exporter.export_card(card)

    def delete_character(self, character_id: str) -> bool:
        if character_id == self._active_id:
            self._active_id = None
            self._active_card = None
        self._cache.pop(character_id, None)
        return self.store.delete_character(character_id)

    def update_character(self, character_id: str, card: CharaCardV2) -> bool:
        errors = validate_card(card)
        if errors:
            raise ValidationError(errors)
        ok = self.store.update_character(character_id, card)
        if ok and character_id in self._cache:
            self._cache[character_id] = card
        if ok and character_id == self._active_id:
            self._active_card = card
        return ok
