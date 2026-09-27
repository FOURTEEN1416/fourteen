"""记忆增强API端点 — 6个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..memory.favorite_manager import FavoriteManager
from ..memory.forward_manager import ForwardManager
from .common import ApiResponse

logger = logging.getLogger("shisi.api.memory_routes")

router = APIRouter(prefix="/api/shisi/memory", tags=["memory"])

_fav_mgr: FavoriteManager | None = None
_fwd_mgr: ForwardManager | None = None


def set_managers(fav: FavoriteManager, fwd: ForwardManager) -> None:
    global _fav_mgr, _fwd_mgr
    _fav_mgr = fav
    _fwd_mgr = fwd


class FavoriteRequest(BaseModel):
    character_id: str
    memory_id: str


class ForwardRequest(BaseModel):
    from_character: str
    to_character: str
    memory_id: str
    content: str = ""


@router.post("/favorite", response_model=ApiResponse)
async def favorite_memory(req: FavoriteRequest):
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    ok = _fav_mgr.favorite(req.character_id, req.memory_id)
    return ApiResponse(data={"success": ok})


@router.delete("/favorite/{fav_id}", response_model=ApiResponse)
async def unfavorite_memory(fav_id: int):
    """按收藏主键取消收藏。

    2026-09-18 前签名 `(fav_id, character_id="", memory_id="")` 中 `fav_id` **完全未被使用**，
    实际删除条件是 character_id + memory_id（两者均有空默认值，可被无参省略调用，
    行为未定义）。现改为 `fav_id` 唯一判据，与路径参数语义一致。
    """
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    ok = _fav_mgr.unfavorite_by_id(fav_id)
    return ApiResponse(data={"success": ok})


@router.get("/favorites", response_model=ApiResponse)
async def list_favorites(character_id: str):
    if _fav_mgr is None:
        raise HTTPException(status_code=503, detail="FavoriteManager未初始化")
    favs = _fav_mgr.list_favorites(character_id)
    return ApiResponse(data=favs)


# 通配路由必须放在全部静态路径之后：原声明位次在 /favorites 之前，
# `GET /api/shisi/memory/favorites` 被 `/{character_id}`（character_id="favorites"）
# 抢先匹配 → 收藏列表端点在 API 层不可达（集成测试只断言 200 故长期未暴露）。
@router.get("/{character_id}", response_model=ApiResponse)
async def get_memories(character_id: str):
    return ApiResponse(data={"character_id": character_id, "message": "记忆查询需要对接现有MemoryPipeline"})


@router.post("/forward", response_model=ApiResponse)
async def forward_memory(req: ForwardRequest):
    """跨角色转发：目标侧派生记录 + 真实回执（缺陷 F 恢复）。"""
    if _fwd_mgr is None:
        raise HTTPException(status_code=503, detail="ForwardManager未初始化")
    receipt = _fwd_mgr.forward_receipt(
        req.from_character, req.to_character, req.memory_id, req.content
    )
    return ApiResponse(data=receipt)


@router.delete("/{memory_id}", response_model=ApiResponse)
async def delete_memory(memory_id: str, character_id: str = "", confirm: bool = False):
    """占位端点——删除链路**未实现**。

    保留 `confirm` 前置校验（既有契约：未确认回 400，见 `tests/test_integration.py`）；
    但 `confirm=true` 时**不再返回"已移入回收站（30天保留期）"的谎报成功**，改为 501。
    依据：`memory_recycle_bin` 表已存在于 `shisi/migrations.py`，删除链路从未落地——谎报
    成功比显式失败更危险（调用方会误以为数据已按 30 天保留期妥善处置）。
    参数签名保留以维持路由契约不变。
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="删除记忆需要二次确认(confirm=true)")
    raise HTTPException(
        status_code=501,
        detail="记忆删除尚未实现：memory_recycle_bin 表已存在但无删除链路",
    )
