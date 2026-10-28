"""话题状态机管理端路由 —— ``/api/topic-state/*``（2026-10-27 实施计划 Task 5）。

三个端点：

- ``GET  /api/topic-state/config``：纯热读 ``data/scheduler_config.json`` 的
  ``topic_state`` 块；不依赖 ``orch._ase``，4 个 worker 里谁都能答。
- ``POST /api/topic-state/config``：partial merge 热写；越界 422（Pydantic 先行），
  合并后跨字段冲突/未知字段 400，其余顶层配置块逐字保留。
- ``GET  /api/topic-state/{session_key}``：只读快照（active/parked/closed +
  next_plan）；不写库、不初始化调度器。

路由注册顺序是硬约束：``/config`` 必须在 ``/{session_key}`` 之前，否则
FastAPI 会把 ``config`` 当成会话键。

会话键口径沿用 R7 P0 修复后的 ``_resolve_admin_target_key``：含 ``:`` 的完整键
原样使用；纯数字仅当该用户存在微信绑定时还原为 ``N:wxid``，否则 400（不猜键，
web/WS 的 ``N:web:8hex`` 必须传完整键）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import get_db
from api.main_routes import TopicStateConfigRequest

logger = logging.getLogger("api.routers.topic_routes")

router = APIRouter(tags=["topic-state"])


@router.get("/api/topic-state/config")
async def get_topic_state_config(
    _auth: bool = Security(verify_api_key_dep),
    _admin=Depends(require_role("admin")),
):
    """读取话题状态机配置（热读，不缓存、不依赖调度器）。"""
    from utils.topic_state import read_topic_config

    return {"status": "ok", "config": read_topic_config()}


@router.post("/api/topic-state/config")
async def update_topic_state_config(
    req: TopicStateConfigRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin=Depends(require_role("admin")),
):
    """partial merge 写入话题状态机配置（锁内读改写，只触碰 ``topic_state`` 块）。"""
    from utils.topic_state import write_topic_config

    payload = req.model_dump(exclude_none=True)
    try:
        config = write_topic_config(**payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    logger.info("话题状态配置已更新: %s", payload)
    return {"status": "ok", "config": config}


@router.get("/api/topic-state/{session_key}")
async def get_topic_state_snapshot(
    session_key: str = Path(..., min_length=1, max_length=200),
    character_id: str = Query("default", max_length=128),
    _auth: bool = Security(verify_api_key_dep),
    _admin=Depends(require_role("admin")),
    db=Depends(get_db),
):
    """只读快照：完整会话键（或可确定性还原的数字 user_id）→ 话题状态。"""
    from api.routers.training_routes import _resolve_admin_target_key
    from utils.topic_state import get_topic_snapshot

    key = await _resolve_admin_target_key(session_key, db)
    snapshot = get_topic_snapshot(key, character_id)
    snapshot.pop("text", None)  # 管理端只读结构；prompt 文本不对外回传
    return {
        "status": "ok",
        "session_key": key,
        "character_id": character_id,
        **snapshot,
    }
