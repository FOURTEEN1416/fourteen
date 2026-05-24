"""提示词应用服务"""

from __future__ import annotations

from shisi.core.ports.character_repository import CharacterRepository
from shisi.core.services.prompt_builder import build


class PromptService:
    def __init__(self, repo: CharacterRepository):
        self._repo = repo

    def build_prompt(self, character_id: str, user_message: str = "", chat_history: str = "") -> str:
        character = self._repo.get_by_id(character_id)
        if not character:
            raise ValueError(f"角色不存在: {character_id}")
        return build(character, user_message, chat_history)
