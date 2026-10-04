"""好感度API端点 — 4个端点。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth_jwt import User, get_current_user

from ..affinity.enhancer import AffinityEnhancer
from .common import ApiResponse

logger = logging.getLogger("shisi.api.affinity_routes")

router = APIRouter(prefix="/api/shisi/affinity", tags=["affinity"])

# B008 合规的依赖单例（ruff 建议：默认值读模块级变量而非就地调 Depends）
_current_user = Depends(get_current_user)

_enhancer: AffinityEnhancer | None = None


def set_enhancer(e: AffinityEnhancer) -> None:
    global _enhancer
    _enhancer = e


class UpdateRequest(BaseModel):
    delta: float
    reason: str = ""
    source: str = "api"


def _effective_user_id(user: User, requested: str | None) -> str:
    """归属主体裁决（2026-10 P0 越权收口）。

    旧实现直接信任 query 里的 ``user_id``——任何登录用户可读写**任意他人**
    的 ``user::character`` 隔离键。现改为：键一律由 JWT 主体导出；
    非 admin 的 query ``user_id`` 一律忽略并以主体覆盖；admin 显式传
    ``user_id`` 时允许管理他人（显式管理通道保留，留空 = admin 本人键）。
    """
    requested_key = str(requested or "").strip()
    if requested_key and user.role == "admin":
        return requested_key
    return str(user.id)


@router.get("/{character_id}", response_model=ApiResponse)
async def get_affinity(
    character_id: str,
    user_id: str | None = None,
    user: User = _current_user,
):
    """按隔离键读——HTTP 管理面独立键空间 ``uid::cid``。

    ⚠️ 键空间边界：对话路径好感以完整会话键（``user_key::cid``）为准，
    与本管理面的 ``uid::cid`` 两套键空间互不相通，控制台对齐另立批次。
    键归属一律由 JWT 主体解析（非 admin 的 query ``user_id`` 被主体覆盖，
    admin 可显式指定他人）；旧「不带 user_id 读裸角色键」的口径随越权面
    一并关闭——裸键正是无归属的全局读写通道。
    """
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    return ApiResponse(
        data=_enhancer.get_progress(character_id, user_id=_effective_user_id(user, user_id))
    )


@router.post("/{character_id}/update", response_model=ApiResponse)
async def update_affinity(
    character_id: str,
    req: UpdateRequest,
    user_id: str | None = None,
    user: User = _current_user,
):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    new_val, unlocks = _enhancer.update(
        character_id,
        req.delta,
        req.reason,
        req.source,
        user_id=_effective_user_id(user, user_id),
    )
    return ApiResponse(data={"affinity": new_val, "unlocks": [u.__dict__ for u in unlocks]})


@router.post("/{character_id}/decay", response_model=ApiResponse)
async def apply_decay(
    character_id: str,
    user_id: str | None = None,
    user: User = _current_user,
):
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")
    effective = _effective_user_id(user, user_id)
    decay = _enhancer.apply_decay(character_id, user_id=effective)
    return ApiResponse(
        data={"decay": decay, "affinity": _enhancer.get_value(character_id, user_id=effective)}
    )


@router.get("/{character_id}/unlocks", response_model=ApiResponse)
async def get_unlocks(
    character_id: str,
    user_id: str | None = None,
    user: User = _current_user,
):
    """解锁展示：``unlocks``=按当前好感算得的应解锁档位（旧形态）；
    ``recorded``=affinity_unlocks 落表行（W14 D10，user×character 首次解锁记录）。

    键归属一律由 JWT 主体解析（``affinity_key(character_id, 主体)``）；
    admin 可显式传 ``user_id`` 查看他人档位。
    """
    if _enhancer is None:
        raise HTTPException(status_code=503, detail="AffinityEnhancer未初始化")

    from ..affinity.enhancer import affinity_key

    effective = _effective_user_id(user, user_id)
    value = _enhancer.get_value(character_id, user_id=effective)
    recorded = _enhancer.unlock_manager.recorded_unlocks(
        affinity_key(character_id, effective)
    )
    unlocks = _enhancer.unlock_manager.get_unlocks_at(value)
    return ApiResponse(
        data={"affinity": value, "unlocks": [u.__dict__ for u in unlocks], "recorded": recorded}
    )
