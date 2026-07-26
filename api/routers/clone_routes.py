"""
好友克隆数据管理路由 — /api/clone/*

来源：原 api.main_routes.py L848/855/862/885/898/912/924 共 7 端点

依赖：
- deps.get_clone_mgr() — CloneDataManager 单例
- 涉及 CRUD：contacts / datasets / conversations
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.auth_jwt import require_role
from api.database import User
from api.deps import deps

logger = logging.getLogger("api.routers.clone_routes")

router = APIRouter(tags=["clone"])


class ClonePreviewRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=128)
    max_messages: int = Field(default=2000, ge=20, le=5000)


def _build_clone_preview(target: str, max_messages: int) -> dict[str, Any]:
    """显式请求时才读取本机微信数据并生成可编辑人设预览。"""
    from clone_training.style_analyzer import StyleAnalyzer
    from clone_training.wechat_decrypt_source import DecryptSource, DecryptSourceError

    try:
        conversations = DecryptSource().extract(target, max_messages=max_messages)
    except DecryptSourceError:
        raise
    if not conversations:
        raise ValueError("未找到可用于分析的文本对话")

    profile = StyleAnalyzer().analyze(conversations)
    catchphrases = [phrase for phrase, _count in profile.catchphrases[:8]]
    anchors = [*catchphrases[:3], *profile.slang_examples[:3]]
    dominant_emotion = max(profile.emotion_dist, key=profile.emotion_dist.get) if profile.emotion_dist else "自然"
    anchors.append(f"{dominant_emotion}表达")
    anchors = list(dict.fromkeys(anchors))[:8]

    positive = profile.emotion_dist.get("正面", 0.0)
    negative = profile.emotion_dist.get("负面", 0.0)
    question_ratio = profile.sentence_type_dist.get("提问", 0.0)
    speaking_style = {
        "formality": 0.35,
        "expressiveness": min(1.0, 0.35 + profile.emoji_freq + profile.kaomoji_freq),
        "humor": min(1.0, 0.3 + profile.slang_freq),
        "directness": max(0.1, min(1.0, 0.75 - question_ratio * 0.4)),
        "emoji_freq": min(1.0, profile.emoji_freq),
        "catchphrases": catchphrases,
    }
    persona = {
        "name": target,
        "description": profile.to_style_prompt(),
        "core_anchors": anchors,
        "personality": {
            "warmth": max(0.1, min(1.0, 0.5 + positive * 0.35 - negative * 0.2)),
            "playfulness": max(0.1, min(1.0, 0.35 + profile.slang_freq + profile.emoji_freq)),
            "independence": 0.6,
            "jealousy": 0.3,
            "stubbornness": 0.4,
        },
        "speaking_style": speaking_style,
    }
    return {
        "persona": persona,
        "sample_count": len(conversations),
        "style_report": profile.to_dict(),
    }


# ═══════════════════════════════════════════════════════
# Clone Data Management API
# ═══════════════════════════════════════════════════════


@router.post("/api/clone/preview")
async def preview_clone_persona(
    req: ClonePreviewRequest,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """从本机微信 4.x 解密数据生成人设预览；不会自动创建角色。"""
    try:
        return await asyncio.to_thread(_build_clone_preview, req.target.strip(), req.max_messages)
    except Exception as e:
        logger.warning("微信克隆预览失败: %s", e)
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/api/clone/contacts")
async def list_clone_contacts(
    keyword: str = Query(default="", max_length=100),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    contacts = mgr.get_contacts(keyword=keyword)
    return {"contacts": contacts, "total": len(contacts)}


@router.get("/api/clone/datasets")
async def list_clone_datasets(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
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
    _admin: tuple[int, User] = Depends(require_role("admin")),
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
async def get_clone_stats(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    mgr = deps.get_clone_mgr()
    return mgr.get_stats()
