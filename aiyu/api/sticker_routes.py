"""表情包API端点 — 5个端点。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..sticker.sticker_manager import StickerManager
from .common import ApiResponse

logger = logging.getLogger("aiyu.api.sticker_routes")

router = APIRouter(prefix="/api/aiyu/stickers", tags=["stickers"])

_manager: Optional[StickerManager] = None


def set_manager(mgr: StickerManager) -> None:
    global _manager
    _manager = mgr


def _get_manager() -> StickerManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="StickerManager未初始化")
    return _manager


class RecommendRequest(BaseModel):
    emotion_tags: list[str]
    limit: int = 5


class BindRequest(BaseModel):
    sticker_ids: list[str]
    unlock_threshold: int = 0


@router.get("", response_model=ApiResponse)
async def list_stickers(category: str | None = None):
    mgr = _get_manager()
    stickers = mgr.list_by_category(category)
    return ApiResponse(data=stickers)


@router.get("/recommend", response_model=ApiResponse)
async def recommend_stickers(req: RecommendRequest):
    mgr = _get_manager()
    results = mgr.recommend(req.emotion_tags, req.limit)
    return ApiResponse(data=results)


@router.post("/import", response_model=ApiResponse)
async def import_stickers(file: UploadFile = File(...), category: str = "default"):
    mgr = _get_manager()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        success, failed = mgr.import_zip(tmp_path, category)
        return ApiResponse(data={"success": success, "failed": failed})
    finally:
        from pathlib import Path
        Path(tmp_path).unlink(missing_ok=True)


@router.put("/characters/{character_id}/stickers", response_model=ApiResponse)
async def bind_stickers(character_id: str, req: BindRequest):
    mgr = _get_manager()
    count = mgr.bind_to_character(character_id, req.sticker_ids, req.unlock_threshold)
    return ApiResponse(data={"bound": count})


@router.delete("/{sticker_id}", response_model=ApiResponse)
async def delete_sticker(sticker_id: str):
    mgr = _get_manager()
    ok = mgr.delete_sticker(sticker_id)
    if not ok:
        raise HTTPException(status_code=404, detail="表情包不存在")
    return ApiResponse(data={"sticker_id": sticker_id, "message": "删除成功"})
