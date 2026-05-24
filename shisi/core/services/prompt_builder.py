"""提示词构建领域服务"""

from __future__ import annotations

from ..models.character_aggregate import CharacterAggregate


def build(
    character: CharacterAggregate,
    user_message: str = "",
    chat_history: str = "",
) -> str:
    return character.build_system_prompt(user_message=user_message, chat_history=chat_history)


def merge_with_persona_prompt(base_prompt: str, character: CharacterAggregate) -> str:
    persona_segment = character.persona.to_prompt_segment()
    return f"{base_prompt}\n\n{persona_segment}"
