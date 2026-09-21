"""角色知识库 API 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Security, UploadFile
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.path_security import sanitize_id
from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.character.models import CharaCardV2
from shisi.knowledge.character_knowledge_service import get_knowledge_service
from shisi.knowledge.crawler_adapter import get_crawler_adapter
from utils.project_paths import project_path

logger = logging.getLogger("api.knowledge_routes")

router = APIRouter(prefix="/api/characters", tags=["knowledge"])


# 角色卡唯一权威目录（2026-09-18 裁决收敛：data/characters 旧库已删除并入本目录）
CHARACTER_DIR = project_path("config", "characters")


def _load_character_data(character_id: str) -> dict[str, Any] | None:
    """从 JSON 文件加载角色数据。"""
    safe_id = sanitize_id(character_id)
    if not safe_id:
        return None
    filepath = CHARACTER_DIR / f"{safe_id}.json"
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


def _ensure_full_index(character_id: str, raw: dict[str, Any]) -> None:
    """确保角色索引存在 —— 从权威真源（卡 dict）走 CharacterAggregate 全量构建。

    2026-09-20 修复：此前端点缺索引时走 `index_from_card(CharaCardV2)`，该路径
    不携带 core_anchors（V2 schema 丢弃顶层扩展字段）→ 首次访问即以降级索引
    **覆盖**重建脚本的全量索引（实测米彩 18 块被覆盖成 7 块、锚点全丢）。
    现与运行时（persona_service）/重建脚本（rebuild_knowledge_index.py）统一。
    """
    service = get_knowledge_service()
    if service.has_index(character_id):
        return
    from shisi.core.models.character_aggregate import CharacterAggregate
    from shisi.core.models.persona_profile import PersonaProfile

    character = CharacterAggregate(
        id=character_id,
        name=(raw.get("name") or "未命名").strip() or "未命名",
        description=raw.get("description", "") or "",
        personality_text=raw.get("personality_text", "") or "",
        scenario=raw.get("scenario", "") or "",
        creator_notes=raw.get("creator_notes", "") or "",
        persona=PersonaProfile(core_anchors=raw.get("core_anchors", []) or []),
        source_data=raw,
    )
    service.index_character(character_id, character)
    service.save_index(character_id)


# ── API 端点 ──


@router.get("/{character_id}/knowledge/stats")
async def get_knowledge_stats(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
):
    """获取角色知识库统计。"""
    raw = _load_character_data(character_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            _ensure_full_index(character_id, raw)
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
    raw = _load_character_data(character_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    service = get_knowledge_service()

    if not service.has_index(character_id):
        try:
            _ensure_full_index(character_id, raw)
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
    raw = _load_character_data(character_id)
    if raw is None:
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
            _ensure_full_index(character_id, raw)
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
    if retriever is not None and hasattr(retriever, "add_chunks"):
        # P0-3：index() 是替换语义——旧实现在此调用会把该角色既有索引块
        # （锚点/示例对话）整体覆盖销毁并落盘。追加必须走 add_chunks。
        retriever.add_chunks(knowledge_chunks)
    elif retriever is not None and hasattr(retriever, "index"):
        retriever.index(list(retriever._chunks) + knowledge_chunks)  # type: ignore[attr-defined]

    # 计数以检索器实际块数为准，不再累加假数字
    if retriever is not None:
        service._chunk_counts[character_id] = len(retriever._chunks)  # type: ignore[attr-defined]
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
    # P1-10（2026-09-21 审查修复）：多源网络长链（实测单次 33.2s）不得在
    # 事件循环上裸跑——同仓 character_routes._schedule_character_crawl 已示范
    # asyncio.to_thread 包同一函数。
    result = await asyncio.to_thread(
        adapter.crawl_and_index,
        character_id=character_id,
        name=req.name,
        card=card.model_dump() if card is not None else None,
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


# ── 火爬虫 + AgentReach 人设增强（与对话内 search 工具区分）──


class EnrichRequest(BaseModel):
    """人设增强请求。

    与对话内 search 工具的区别：
    - search 工具：对话中实时联网搜索，结果给 LLM 参考（不写入知识库）
    - 人设增强：离线批量抓取，处理为知识块后写入角色 BM25 索引（持久化）
    """
    name: str = Field(..., min_length=1, description="角色名称（用于搜索关键词）")
    max_docs: int = Field(default=3, ge=1, le=10, description="最大文档数")


@router.post("/{character_id}/enrich")
async def enrich_character_persona(
    character_id: str,
    req: EnrichRequest,
    _auth: bool = Security(verify_api_key_dep),
):
    """火爬虫 + AgentReach 人设增强：抓取多源网络素材 → 处理为知识块 → 写入角色知识库。

    数据源：B站、小红书、Firecrawl、Jina Reader、Exa 等多源搜索。
    与对话内 search 工具不同，本端点将结果持久化到角色知识库供后续 RAG 检索。
    """
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    try:
        from persona_extractor.web_enricher import WebPersonaEnricher
        enricher = WebPersonaEnricher()
        result = await asyncio.to_thread(
            enricher.enrich,
            character_id=character_id,
            character_name=req.name,
            max_docs=req.max_docs,
            interactive=False,
        )
        return {
            "status": "enriched" if result.chunks_added > 0 else "no_new_chunks",
            "character_id": character_id,
            "name": req.name,
            "documents_found": result.documents_found,
            "chunks_generated": result.chunks_generated,
            "chunks_added": result.chunks_added,
            "sources_used": result.sources_used,
            "duration_seconds": round(result.duration_seconds, 2),
            "errors": result.errors,
        }
    except Exception as e:
        logger.exception("人设增强失败: %s", character_id)
        raise HTTPException(status_code=500, detail=f"人设增强失败: {e}") from e


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
