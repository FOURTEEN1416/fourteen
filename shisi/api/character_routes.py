"""角色管理REST API端点 — 7个端点。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth_jwt import User, require_role

from ..character.manager import CharacterManager
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
async def switch_character(
    req: SwitchRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：角色切换是全局控制面动作（影响运行态激活角色），
    仅 admin 可调——旧实现任意注册用户（role=viewer）即可切换。"""
    mgr = _get_manager()
    ok, msg = mgr.switch_character(req.character_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return ApiResponse(data={"character_id": req.character_id, "message": msg})


@router.post("/import", response_model=ApiResponse)
async def import_characters(
    file: UploadFile = File(...),  # noqa: B008
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：批量导入会写入全局角色池，仅 admin。"""
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
async def export_character(
    character_id: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：整卡导出（含完整人设字段）落盘为可分发文件，仅 admin。"""
    mgr = _get_manager()
    path = mgr.export_character(character_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(data={"path": str(path)})


@router.put("/{character_id}", response_model=ApiResponse)
async def update_character(character_id: str, req: UpdateRequest):
    """已作废：与 `/api/shisi/persona/characters/{id}` 同一类缺陷。

    `manager.update_character` 只 UPDATE SQLite 运行态副本，`config/characters`
    权威真源不变（v1.9 裁决），旧响应却返回「更新成功」。410 先于任何写动作，
    避免副本被单独改动的半写态。
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "该端点已作废：只写运行态副本、不写权威真源，改动不会在对话中生效。"
            f"完整角色卡请用 PUT /api/characters/{character_id}/persona-card，"
            f"数值/风格人设请用 PUT /api/characters/{character_id}/persona"
        ),
    )


@router.delete("/{character_id}", response_model=ApiResponse)
async def delete_character(
    character_id: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：删除运行态角色副本属全局控制面写动作，仅 admin。"""
    mgr = _get_manager()
    ok = mgr.delete_character(character_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    return ApiResponse(
        data={
            "character_id": character_id,
            "message": (
                "已从运行态管理器移除该角色副本；权威角色卡真源在 config/characters，"
                "不受本端点影响。完整删除（含记忆/索引级联）请用认证面 "
                f"DELETE /api/characters/{character_id}（W9 lifecycle 作业）。"
            ),
        }
    )
