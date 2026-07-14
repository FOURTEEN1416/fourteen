"""角色知识库 API 路由。"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Security, UploadFile
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.path_security import sanitize_id
from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.character.models import CharaCardV2
from shisi.knowledge.character_knowledge_service import get_knowledge_service
from shisi.knowledge.crawler_adapter import get_crawler_adapter

logger = logging.getLogger("api.knowledge_routes")

router = APIRouter(prefix="/api/characters", tags=["knowledge"])


CHARACTER_DIR = Path("characters")


def _load_character_data(character_id: str) -> dict[str, Any] | None:
    """从 JSON 文件加载角色数据。"""
    safe_id = sanitize_id(character_id)
    if not safe_id:
        return None
    filepath = CHARACTER_DIR / f"{safe_id}.json"
    if not filepath.exists():
        filepath = Path("data") / "characters" / f"{safe_id}.json"
    if not filepath.exists():
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
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
        return CharaCardV2Parser.parse(data)
    except Exception:
        logger.exception("解析角色卡失败: %s", character_id)
        return None


# ── API 端点 ──


@router.get("/{character_id}/knowledge/stats")
async def get_knowledge_stats(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取角色知识库统计。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            service.index_from_card(character_id, card)
            service.save_index(character_id)
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
    _auth: bool = Security(verify_api_key_dep),
):
    """搜索角色知识库。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            service.index_from_card(character_id, card)
            service.save_index(character_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail="知识索引失败") from e

    result = service.search(character_id, req.query, top_k=req.top_k)
    top_results = result.get_top(req.top_k)
    return {
        "query": req.query,
        "total": result.total_chunks,
        "results": [
            {"content": r.content, "source": r.source, "score": r.score}
            for r in top_results
        ],
    }


# ── 角色知识库文档上传 ──


@router.post("/{character_id}/knowledge/documents", status_code=201)
async def upload_knowledge_document(
    character_id: str,
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
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
            raise HTTPException(status_code=500, detail="索引知识失败") from e

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

    service.save_index(character_id)

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
    _auth: bool = Security(verify_api_key_dep),
):
    """删除角色知识库中的文档"""
    service = get_knowledge_service()
    if not service.has_index(character_id):
        raise HTTPException(status_code=404, detail=f"角色知识库不存在: {character_id}")
    retriever = service._retrievers.get(character_id)
    if retriever is None:
        raise HTTPException(status_code=404, detail="检索器未初始化")

    # 修复：按 doc_id 删除单个文档，而非 clear() 清空整个角色知识库
    # 上传时 source_id 格式为 f"{doc_id}_{idx}"，按前缀过滤保留其余文档
    existing_chunks = list(retriever._chunks)  # type: ignore[attr-defined]
    remaining_chunks = [
        c for c in existing_chunks
        if not c.source_id.startswith(f"{doc_id}_")
    ]
    removed_count = len(existing_chunks) - len(remaining_chunks)
    if removed_count == 0:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    retriever.index(remaining_chunks)
    service._chunk_counts[character_id] = len(remaining_chunks)
    service.save_index(character_id)
    logger.info("文档已从角色知识库删除: %s (移除 %d 块)", doc_id, removed_count)
    return {"status": "deleted", "document_id": doc_id, "removed_chunks": removed_count}


# ── 知识宝库 vault 端点 ──


class VaultCollectRequest(BaseModel):
    collect_persona: bool = Field(default=True, description="是否从角色卡提取 PersonaFeatures")
    collect_documents: bool = Field(default=False, description="是否收集已上传文档")


@router.post("/{character_id}/knowledge/vault")
async def collect_knowledge_vault(
    character_id: str,
    req: VaultCollectRequest = VaultCollectRequest(),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
):
    """触发知识宝库收集：从角色卡提取 PersonaFeatures → 知识块 → BM25 索引。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    try:
        from shisi.vault import VaultCollector
        collector = VaultCollector()
        chunk_count = collector.collect_card(character_id, card)

        stats = get_knowledge_service().get_stats(character_id)
        return {
            "status": "collected",
            "character_id": character_id,
            "chunks_added": chunk_count,
            "total_chunks": stats.get("total_chunks", 0),
            "features": {
                "persona": req.collect_persona,
                "documents": req.collect_documents,
            },
        }
    except Exception as e:
        logger.exception("知识宝库收集失败: %s", character_id)
        raise HTTPException(status_code=500, detail="知识宝库收集失败") from e


@router.get("/{character_id}/knowledge/vault/features")
async def get_vault_features(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """查看角色 PersonaFeatures 提取结果（调试用）。"""
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    try:
        from shisi.vault._persona_adapter import PersonaAdapter
        features = PersonaAdapter.extract(card)
        return {
            "character_id": character_id,
            "features": features.to_dict(),
            "chunk_count": (
                len(features.core_anchors)
                + len(features.speaking_style)
                + len(features.background)
                + len(features.relationship)
                + len(features.behavior_rules)
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Persona 提取失败") from e


# ── 从网络抓取人物资料 ──


class CrawlPersonaRequest(BaseModel):
    name: str = Field(..., min_length=1, description="要抓取的人物名称")


@router.post("/{character_id}/knowledge/crawl")
async def crawl_persona_knowledge(
    character_id: str,
    req: CrawlPersonaRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """从网络抓取人物资料并写入角色知识索引。"""
    card = _load_character_card(character_id)
    # 允许角色卡不存在，此时仅建立爬虫来源的索引
    adapter = get_crawler_adapter()
    result = adapter.crawl_and_index(
        character_id=character_id,
        name=req.name,
        card=card,
    )
    if not result.get("success"):
        logger.warning("抓取人物资料失败: %s — %s", character_id, result.get("error"))
        raise HTTPException(status_code=502, detail=result.get("error", "抓取失败"))

    stats = get_knowledge_service().get_stats(character_id)
    return {
        "status": "crawled",
        "character_id": character_id,
        "name": req.name,
        "chunks_added": result.get("chunks_added", 0),
        "source": result.get("source", "unknown"),
        "source_url": result.get("source_url", ""),
        "fallback_chain": result.get("fallback_chain", []),
        "total_chunks": stats.get("total_chunks", 0),
    }


# ── 辅助 ──


def _get_all_chunks(service, character_id: str) -> list:
    """获取所有知识块（用于统计）。"""
    if not service.has_index(character_id):
        return []
    result = service.search(character_id, "", top_k=200)
    return result.chunks


def _aggregate_sources(chunks: list) -> list[dict]:
    """按来源聚合知识块。"""
    source_map: dict[str, int] = {}
    for c in chunks:
        src = getattr(c, "source", "unknown")
        source_map[src] = source_map.get(src, 0) + 1
    return [{"name": k, "count": v} for k, v in source_map.items()]
