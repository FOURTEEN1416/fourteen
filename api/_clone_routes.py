"""
好友克隆数据管理路由 — /api/clone/*

来源：原 api.main_routes.py L848/855/862/885/898/912/924 共 7 端点

依赖：
- deps.get_clone_mgr() — CloneDataManager 单例
- 涉及 CRUD：contacts / datasets / conversations
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Security

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps

logger = logging.getLogger("api._clone_routes")

router = APIRouter(tags=["clone"])


# ═══════════════════════════════════════════════════════
# Clone Data Management API
# ═══════════════════════════════════════════════════════


@router.get("/api/clone/contacts")
async def list_clone_contacts(
    keyword: str = "",
    _auth: bool = Security(verify_api_key_dep),
):
    mgr = deps.get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword)
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/api/clone/datasets")
async def list_clone_datasets(_auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    datasets = mgr.list_datasets()
    return {"datasets": datasets, "total": len(datasets)}


@router.get("/api/clone/datasets/{person_id}")
async def get_clone_dataset_detail(
    person_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    only_user: bool = Query(default=False),
    _auth: bool = Security(verify_api_key_dep),
):
    mgr = deps.get_clone_mgr()
    return mgr.get_dataset_detail(
        person_id=person_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        only_user=only_user,
    )


@router.delete("/api/clone/datasets/{person_id}")
async def delete_clone_dataset(
    person_id: str,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_dataset(person_id)
    if not ok:
        raise HTTPException(404, f"数据集 {person_id} 未找到")
    return {"status": "deleted", "person_id": person_id}


@router.delete("/api/clone/datasets/{person_id}/conversation")
async def delete_clone_conversation(
    person_id: str,
    index: int = Query(..., description="对话索引（从0开始）"),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    ok = mgr.delete_conversation(person_id, index)
    if not ok:
        raise HTTPException(404, "对话未找到或删除失败")
    return {"status": "deleted", "person_id": person_id, "index": index}


@router.post("/api/clone/datasets/{person_id}/conversations/batch-delete")
async def batch_delete_clone_conversations(
    person_id: str,
    indices: list[int] = Query(..., description="要删除的索引列表"),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    deleted = mgr.batch_delete_conversations(person_id, indices)
    return {"status": "deleted", "person_id": person_id, "deleted_count": deleted}


@router.get("/api/clone/stats")
async def get_clone_stats(_auth: bool = Security(verify_api_key_dep)):
    mgr = deps.get_clone_mgr()
    return mgr.get_stats()
