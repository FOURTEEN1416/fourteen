"""角色管理REST API端点 — 7个端点。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..character.manager import CharacterManager
from ..character.models import CharaCardV2
from ..character.validator import ValidationError
from .common import ApiResponse

logger = logging.getLogger("shisi.api.character_routes")

router = APIRouter(prefix="/api/shisi/characters", tags=["characters"])

_manager: CharacterManager | None = None


def set_manager(mgr: CharacterManager) -> None:
    global _manager
    _manager = mgr


def _get_manager() -> CharacterManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="CharacterManager未初始化")
    return _manager


class SwitchRequest(BaseModel):
    character_id: str


class UpdateRequest(BaseModel):
    card: dict[str, Any]


@router.get("", response_model=ApiResponse)
async def list_characters():
    mgr = _get_manager()
    chars = mgr.list_characters()
    return ApiResponse(data=[c.model_dump(mode="json") for c in chars])


@router.get("/{character_id}", response_model=ApiResponse)
async def get_character(character_id: str):
    mgr = _get_manager()
    card = mgr.load_character(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(data=card.model_dump(mode="json"))


@router.post("/switch", response_model=ApiResponse)
async def switch_character(req: SwitchRequest):
    mgr = _get_manager()
    ok, msg = mgr.switch_character(req.character_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ApiResponse(data={"character_id": req.character_id, "message": msg})


@router.post("/import", response_model=ApiResponse)
async def import_characters(file: UploadFile = File(...)):  # noqa: B008
    mgr = _get_manager()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, encoding="utf-8") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        card, error = mgr.import_character(tmp_path)
        if error:
            raise HTTPException(status_code=400, detail=error)
        return ApiResponse(data={"name": card.data.name, "message": "导入成功"})  # type: ignore
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/export/{character_id}", response_model=ApiResponse)
async def export_character(character_id: str):
    mgr = _get_manager()
    path = mgr.export_character(character_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(data={"path": str(path)})


@router.put("/{character_id}", response_model=ApiResponse)
async def update_character(character_id: str, req: UpdateRequest):
    mgr = _get_manager()
    try:
        card = CharaCardV2.model_validate(req.card)
    except Exception:
        logger.exception("角色卡数据校验失败: %s", character_id)
        raise HTTPException(status_code=400, detail="角色卡数据无效") from None
    try:
        ok = mgr.update_character(character_id, card)
    except ValidationError:
        logger.exception("角色更新校验失败: %s", character_id)
        raise HTTPException(status_code=400, detail="角色数据更新失败") from None
    if not ok:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(data={"character_id": character_id, "message": "更新成功"})


@router.delete("/{character_id}", response_model=ApiResponse)
async def delete_character(character_id: str):
    mgr = _get_manager()
    ok = mgr.delete_character(character_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(data={"character_id": character_id, "message": "删除成功"})
