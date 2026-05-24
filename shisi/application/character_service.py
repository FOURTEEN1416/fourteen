"""角色应用服务"""

from __future__ import annotations

from typing import List, Optional, Tuple

from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.ports.character_repository import CharacterRepository
from shisi.core.services.prompt_builder import build as build_prompt


class CharacterService:
    def __init__(self, repo: CharacterRepository):
        self._repo = repo

    def create_character(self, name: str, description: str = "", **kwargs) -> CharacterAggregate:
        character = CharacterAggregate(name=name, description=description, **kwargs)
        self._repo.save(character)
        return character

    def get_character(self, character_id: str) -> Optional[CharacterAggregate]:
        return self._repo.get_by_id(character_id)

    def get_active_character(self) -> Optional[CharacterAggregate]:
        return self._repo.get_active()

    def list_characters(self) -> List[CharacterAggregate]:
        return self._repo.list_all()

    def switch_character(self, character_id: str) -> bool:
        return self._repo.set_active(character_id)

    def process_message(
        self,
        character_id: str,
        user_message: str,
        chat_history: str = "",
    ) -> Tuple[CharacterAggregate, str]:
        character = self._repo.get_by_id(character_id)
        if not character:
            raise ValueError(f"角色不存在: {character_id}")

        character.update_emotion(user_message)
        prompt = build_prompt(character, user_message, chat_history)
        self._repo.save(character)
        return character, prompt

    def import_from_legacy(self, legacy_data: dict) -> CharacterAggregate:
        character = CharacterAggregate.from_legacy_card(legacy_data)
        self._repo.save(character)
        return character

    def export_to_legacy(self, character_id: str) -> dict:
        character = self._repo.get_by_id(character_id)
        if not character:
            raise ValueError(f"角色不存在: {character_id}")
        return {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": character.name,
                "description": character.description,
                "personality": character.persona.to_prompt_segment(),
                "tags": character.tags,
            },
        }

    def delete_character(self, character_id: str) -> bool:
        character = self._repo.get_by_id(character_id)
        if not character:
            return False
        active = self._repo.get_active()
        if active and active.id == character_id:
            self._repo.set_active("")
        return self._repo.delete(character_id)
