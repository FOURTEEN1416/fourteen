"""表情包API端点 — 5个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth_jwt import User, require_role

from ..sticker.sticker_manager import StickerManager
from .common import ApiResponse

logger = logging.getLogger("shisi.api.sticker_routes")

router = APIRouter(prefix="/api/shisi/stickers", tags=["stickers"])

_manager: StickerManager | None = None


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


@router.post("/recommend", response_model=ApiResponse)
async def recommend_stickers(req: RecommendRequest):
    mgr = _get_manager()
    results = mgr.recommend(req.emotion_tags, req.limit)
    return ApiResponse(data=results)


@router.post("/import", response_model=ApiResponse)
async def import_stickers(
    file: UploadFile = File(...),  # noqa: B008
    category: str = "default",
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """ZIP 导入回执（W14 · D12-K 语义对齐）：
    ``accepted`` = 真实入库张数（文件落盘 + stickers 表写入都成功），
    ``failed`` = 安全门禁拦截 / 格式不支持 / 大小超限 / 解压或写库失败数。

    P0 越权收口：批量导入全局表情池，仅 admin；``category`` 非法
    （含路径穿越字符）由 importer 白名单消毒拒绝 → 400。
    """
    mgr = _get_manager()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        accepted, failed = mgr.import_zip(tmp_path, category)
        return ApiResponse(data={"accepted": accepted, "failed": failed})
    except ValueError as e:
        # importer 的 category 白名单/包含性断言拒绝（路径穿越等）
        raise HTTPException(status_code=400, detail=str(e)) from None
    finally:
        from pathlib import Path
        Path(tmp_path).unlink(missing_ok=True)


@router.put("/characters/{character_id}/stickers", response_model=ApiResponse)
async def bind_stickers(
    character_id: str,
    req: BindRequest,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：角色表情绑定影响对话链渲染，仅 admin。"""
    mgr = _get_manager()
    count = mgr.bind_to_character(character_id, req.sticker_ids, req.unlock_threshold)
    return ApiResponse(data={"bound": count})


@router.delete("/{sticker_id}", response_model=ApiResponse)
async def delete_sticker(
    sticker_id: str,
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """P0 越权收口：删除全局表情资产，仅 admin。"""
    mgr = _get_manager()
    ok = mgr.delete_sticker(sticker_id)
    if not ok:
        raise HTTPException(status_code=404, detail="表情包不存在")
    return ApiResponse(data={"sticker_id": sticker_id, "message": "删除成功"})
