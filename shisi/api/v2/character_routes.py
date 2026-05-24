"""角色管理v2路由"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from shisi.application.character_service import CharacterService
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.infrastructure.persistence.sqlite_repository import SQLiteCharacterRepository

from .schemas import (
    CharacterDetail,
    CharacterSummary,
    CreateCharacterRequest,
    ProcessMessageRequest,
    ProcessMessageResponse,
)

logger = logging.getLogger("shisi.api.v2.character_routes")

router = APIRouter(prefix="/v2/characters", tags=["v2-characters"])

_repo_instance: SQLiteCharacterRepository | None = None


def _get_repo() -> SQLiteCharacterRepository:
    global _repo_instance
    if _repo_instance is None:
        _repo_instance = SQLiteCharacterRepository()
    return _repo_instance


def get_character_service() -> CharacterService:
    return CharacterService(_get_repo())


def _to_detail(character: CharacterAggregate, is_active: bool = False) -> CharacterDetail:
    return CharacterDetail(
        id=character.id,
        name=character.name,
        description=character.description,
        avatar_url=character.avatar_url,
        tags=character.tags,
        persona=character.persona.to_dict(),
        emotional_state=character.emotional_state.to_dict(),
        is_active=is_active,
        created_at=character.created_at,
        updated_at=character.updated_at,
    )


@router.get("", response_model=list[CharacterSummary])
async def list_characters(service: CharacterService = Depends(get_character_service)):
    characters = service.list_characters()
    active = service.get_active_character()
    active_id = active.id if active else None
    return [
        CharacterSummary(
            id=c.id,
            name=c.name,
            description=c.description[:100] + "..." if len(c.description) > 100 else c.description,
            is_active=c.id == active_id,
            affinity_level=c.emotional_state.affinity_level.value,
            affinity_name=c.emotional_state.affinity_level.display_name,
            avatar_url=c.avatar_url,
            tags=c.tags,
            updated_at=c.updated_at,
        )
        for c in characters
    ]


@router.get("/active", response_model=CharacterDetail | None)
async def get_active_character(service: CharacterService = Depends(get_character_service)):
    character = service.get_active_character()
    if not character:
        return None
    return _to_detail(character, is_active=True)


@router.get("/{character_id}", response_model=CharacterDetail)
async def get_character(character_id: str, service: CharacterService = Depends(get_character_service)):
    character = service.get_character(character_id)
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    active = service.get_active_character()
    return _to_detail(character, is_active=(active is not None and active.id == character_id))


@router.post("", response_model=CharacterDetail, status_code=201)
async def create_character(request: CreateCharacterRequest, service: CharacterService = Depends(get_character_service)):
    character = service.create_character(name=request.name, description=request.description, tags=request.tags)
    return _to_detail(character)


@router.put("/{character_id}/activate")
async def activate_character(character_id: str, service: CharacterService = Depends(get_character_service)):
    success = service.switch_character(character_id)
    if not success:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"success": True, "character_id": character_id}


@router.post("/{character_id}/process", response_model=ProcessMessageResponse)
async def process_message(
    character_id: str,
    request: ProcessMessageRequest,
    service: CharacterService = Depends(get_character_service),
):
    try:
        character, prompt = service.process_message(
            character_id=character_id,
            user_message=request.message,
            chat_history=request.chat_history,
        )
        return ProcessMessageResponse(character=_to_detail(character, is_active=True), system_prompt=prompt)
    except ValueError:
        logger.exception("处理消息失败: character_id=%s", character_id)
        raise HTTPException(status_code=404, detail="角色不存在或消息处理失败")


@router.post("/import")
async def import_character(file: UploadFile = File(...), service: CharacterService = Depends(get_character_service)):
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小超过限制")
    try:
        data = json.loads(content.decode("utf-8"))
        character = service.import_from_legacy(data)
        return {"success": True, "character_id": character.id, "name": character.name}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的JSON文件")
    except Exception:
        raise HTTPException(status_code=500, detail="导入失败，请检查文件格式")


@router.post("/{character_id}/export")
async def export_character(character_id: str, service: CharacterService = Depends(get_character_service)):
    try:
        return service.export_to_legacy(character_id)
    except ValueError:
        logger.exception("导出角色失败: character_id=%s", character_id)
        raise HTTPException(status_code=404, detail="角色不存在或导出失败")


@router.delete("/{character_id}")
async def delete_character(character_id: str, service: CharacterService = Depends(get_character_service)):
    success = service.delete_character(character_id)
    if not success:
        raise HTTPException(status_code=404, detail="角色不存在")
    return {"success": True}
