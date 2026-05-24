"""人设编辑v2路由"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from shisi.application.character_service import CharacterService
from shisi.infrastructure.persistence.sqlite_repository import SQLiteCharacterRepository

from .schemas import UpdatePersonaRequest

router = APIRouter(prefix="/v2/characters", tags=["v2-persona"])

_repo_instance: SQLiteCharacterRepository | None = None


def get_character_service() -> CharacterService:
    global _repo_instance
    if _repo_instance is None:
        _repo_instance = SQLiteCharacterRepository()
    return CharacterService(_repo_instance)


@router.get("/{character_id}/persona")
async def get_persona(character_id: str, service: CharacterService = Depends(get_character_service)):
    character = service.get_character(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    return character.persona.to_dict()


@router.put("/{character_id}/persona")
async def update_persona(
    character_id: str,
    request: UpdatePersonaRequest,
    service: CharacterService = Depends(get_character_service),
):
    character = service.get_character(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    persona = character.persona
    update_data = request.model_dump(exclude_none=True)
    for key, value in update_data.items():
        if hasattr(persona, key):
            setattr(persona, key, value)

    persona.__post_init__()

    service._repo.save(character)
    return character.persona.to_dict()
