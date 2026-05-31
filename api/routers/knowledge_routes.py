"""角色知识库 API 路由。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, HTTPException, Query, Security, UploadFile
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from shisi.character.models import CharaCardV2
from shisi.knowledge.character_knowledge_service import get_knowledge_service

from api.deps import deps

logger = logging.getLogger("api.knowledge_routes")

router = APIRouter(prefix="/api/characters", tags=["knowledge"])

_verify_api_key_func: Callable | None = None
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _verify_api_key(api_key: str | None = Security(_api_key_header)):
    if _verify_api_key_func is not None:
        return await _verify_api_key_func(api_key)
    return True


def set_dependencies(verify_api_key: Callable) -> None:
    global _verify_api_key_func
    _verify_api_key_func = verify_api_key


CHARACTER_DIR = Path("characters")


def _load_character_data(character_id: str) -> dict[str, Any] | None:
    """从 JSON 文件加载角色数据。"""
    filepath = CHARACTER_DIR / f"{character_id}.json"
    if not filepath.exists():
        filepath = Path("data") / "characters" / f"{character_id}.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("加载角色数据失败: %s", character_id)
        return None


def _load_character_card(character_id: str) -> CharaCardV2 | None:
    """加载角色卡。"""
    data = _load_character_data(character_id)
    if not data:
        return None
    try:
        return CharaCardV2.from_dict(data)
    except Exception:
        logger.exception("解析角色卡失败: %s", character_id)
        return None


# ── API 端点 ──


@router.get("/{character_id}/knowledge/stats")
async def get_knowledge_stats(
    character_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """获取角色知识库统计。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            service.index_from_card(character_id, card)
        except Exception:
            logger.exception("索引知识失败: %s", character_id)
            return {
                "indexed": False,
                "total_chunks": 0,
                "retriever_type": "",
                "sources": [],
                "error": "索引失败",
            }

    stats = service.get_stats(character_id)
    chunks = _get_all_chunks(service, character_id)
    sources = _aggregate_sources(chunks)

    return {"indexed": True, **stats, "sources": sources}


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="搜索关键词")
    top_k: int = Field(default=3, ge=1, le=20, description="返回条目数")


@router.post("/{character_id}/knowledge/search")
async def search_knowledge(
    character_id: str,
    req: KnowledgeSearchRequest,
    _auth: bool = Security(_verify_api_key),
):
    """搜索角色知识库。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            service.index_from_card(character_id, card)
        except Exception:
            raise HTTPException(status_code=500, detail="知识索引失败")

    result = service.search(character_id, req.query, top_k=req.top_k)
    return {
        "query": req.query,
        "total": result.total,
        "results": [
            {"content": r.content, "source": r.source, "score": r.score}
            for r in result.top_k
        ],
    }


# ── 角色知识库文档上传 ──


@router.post("/{character_id}/knowledge/documents", status_code=201)
async def upload_knowledge_document(
    character_id: str,
    file: UploadFile = File(...),
    _auth: bool = Security(_verify_api_key),
):
    """上传文档到角色知识库（文本文件，自动分块索引）"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    content = await file.read()
    max_size = 10 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"文件大小超过限制 ({max_size // 1024 // 1024}MB)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")

    service = get_knowledge_service()
    if not service.has_index(character_id):
        try:
            service.index_from_card(character_id, card)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"索引知识失败: {e}") from e

    chunk_size = 1000
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)] if len(text) > chunk_size else [text]

    from shisi.knowledge.retriever import KnowledgeChunk
    doc_id = str(uuid.uuid4())[:8]
    knowledge_chunks = []
    for idx, chunk_text in enumerate(chunks):
        knowledge_chunks.append(KnowledgeChunk(
            content=chunk_text,
            source=file.filename or f"document_{doc_id}",
            source_id=f"{doc_id}_{idx}",
        ))

    retriever = service._retrievers.get(character_id)
    if retriever and hasattr(retriever, 'index'):
        retriever.index(knowledge_chunks)

    if character_id in service._chunk_counts:
        service._chunk_counts[character_id] += len(knowledge_chunks)
    else:
        service._chunk_counts[character_id] = len(knowledge_chunks)

    logger.info("文档已上传到角色知识库: %s → %s (%d 块)", file.filename, character_id, len(knowledge_chunks))
    return {
        "status": "indexed",
        "filename": file.filename,
        "size": len(content),
        "chunks": len(knowledge_chunks),
        "document_id": doc_id,
    }


@router.delete("/{character_id}/knowledge/documents/{doc_id}")
async def delete_knowledge_document(
    character_id: str,
    doc_id: str,
    _auth: bool = Security(_verify_api_key),
):
    """删除角色知识库中的文档"""
    service = get_knowledge_service()
    if not service.has_index(character_id):
        raise HTTPException(status_code=404, detail=f"角色知识库不存在: {character_id}")
    retriever = service._retrievers.get(character_id)
    if retriever and hasattr(retriever, 'clear'):
        retriever.clear()
    if character_id in service._chunk_counts:
        del service._chunk_counts[character_id]
    logger.info("文档已从角色知识库删除: %s", doc_id)
    return {"status": "deleted", "document_id": doc_id}


# ── 辅助 ──


def _get_all_chunks(service, character_id: str) -> list:
    """获取所有知识块（用于统计）。"""
    if not service.has_index(character_id):
        return []
    result = service.search(character_id, "", top_k=200)
    return result.top_k


def _aggregate_sources(chunks: list) -> list[dict]:
    """按来源聚合知识块。"""
    source_map: dict[str, int] = {}
    for c in chunks:
        src = getattr(c, "source", "unknown")
        source_map[src] = source_map.get(src, 0) + 1
    return [{"name": k, "count": v} for k, v in source_map.items()]
