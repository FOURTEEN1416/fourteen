"""角色知识库 API 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Security, UploadFile
from pydantic import BaseModel, Field

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_optional_principal
from api.path_security import sanitize_id
from api.routers.character_routes import require_character_access
from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.character.models import CharaCardV2
from shisi.knowledge.character_knowledge_service import (
    build_character_aggregate,
    get_knowledge_service,
)
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
    """确保角色索引就绪 —— 从权威真源（卡 dict）走 CharacterAggregate 全量构建。

    2026-09-20 修复：此前端点缺索引时走 `index_from_card(CharaCardV2)`，该路径
    不携带 core_anchors（V2 schema 丢弃顶层扩展字段）→ 首次访问即以降级索引
    **覆盖**重建脚本的全量索引（实测米彩 18 块被覆盖成 7 块、锚点全丢）。
    现与运行时（persona_service）/重建脚本（rebuild_knowledge_index.py）统一。

    2026-09-27（W5）修复：旧实现在 ``has_index()``（仅内存判定）为假时直接
    ``index_character + save_index``，从不先 load 磁盘 —— 冷 worker 首开知识
    统计即以「仅卡片」的新索引**覆盖**磁盘上含上传/抓取内容的索引（外部知识
    唯一存储被销毁）。现统一走服务层 ``ensure_index`` 闭环：版本化磁盘优先、
    卡片来源刷新、外部来源保留、锁内派生重建。
    """
    service = get_knowledge_service()
    service.ensure_index(character_id, character=build_character_aggregate(raw))


# ── API 端点 ──


# ── 每用户限速（P0 修复批 2026-10-04，F3）────────────────
# crawl / enrich 是多源网络长链（实测单次 30s+）且会写知识库，此前对普通
# 注册用户完全不限速。进程内固定窗口计数：每用户每端点每分钟 2 次。
# 机器面（无 Bearer）不在「每用户」语义内，不在此限——其准入由 API Key 面
# 把守（与 W1 机器面契约同边界：归属类校验只在存在 Bearer 主体时生效）。

_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMIT_PER_WINDOW = 2
_RATE_BUCKETS: dict[str, list[float]] = {}


def _enforce_user_rate_limit(scope: str, principal: AuthPrincipal | None) -> None:
    """固定窗口内存限速：每用户（Bearer 主体）每 scope 每分钟 _RATE_LIMIT_PER_WINDOW 次。

    isinstance 收窄：经 FastAPI 注入时 principal 必为 AuthPrincipal | None；
    直调路由函数（既有单测的直调形态）拿到的是未解析的 Security 哨兵，
    按机器面处理（无身份可限，亦不因此崩）。
    """
    if not isinstance(principal, AuthPrincipal):
        return
    key = f"{scope}:user:{principal.user_id}"
    now = time.monotonic()
    hits = [t for t in _RATE_BUCKETS.get(key, ()) if now - t < _RATE_WINDOW_SECONDS]
    if len(hits) >= _RATE_LIMIT_PER_WINDOW:
        retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - hits[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail="操作过于频繁：该功能每用户每分钟最多 2 次，请稍后再试",
            headers={"Retry-After": str(retry_after), "X-Error-Code": "RATE_LIMIT"},
        )
    hits.append(now)
    _RATE_BUCKETS[key] = hits


@router.get("/{character_id}/knowledge/stats")
async def get_knowledge_stats(
    character_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    try:
        # W5：无条件走 ensure 闭环（内存/磁盘版本判定），冷 worker 不再以
        # 「仅卡片」新索引覆盖含上传内容的既有索引
        service.ensure_index(character_id, character=build_character_aggregate(raw))
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

    # W5：文档是不可丢源材料 —— 唯一存储在源存储（doc:{doc_id} 来源，替换式），
    # 索引由服务层锁内派生重建并落盘。旧实现直摸检索器 add_chunks 后落盘，
    # 任何整库重建/失效都会把文档销毁。
    stored_count, _changed = service.upsert_source(
        character_id,
        key=f"doc:{doc_id}",
        kind="upload",
        version=doc_id,
        chunks=knowledge_chunks,
    )

    logger.info("文档已上传到角色知识库: %s → %s (%d 块)", file.filename, character_id, stored_count)
    return {
        "status": "indexed",
        "filename": file.filename,
        "size": len(content),
        "chunks": stored_count,
        "document_id": doc_id,
    }


@router.delete("/{character_id}/knowledge/documents/{doc_id}")
async def delete_knowledge_document(
    character_id: str,
    doc_id: str,
    _auth: bool = Security(verify_api_key_dep),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """删除角色知识库中的文档（W5：按来源粒度 ``doc:{doc_id}`` 从源存储移除）"""
    service = get_knowledge_service()
    removed_count = service.remove_source(character_id, f"doc:{doc_id}")
    if removed_count == 0:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
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
    principal: AuthPrincipal | None = Security(get_optional_principal),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """从网络抓取人物资料并写入角色知识索引。"""
    _enforce_user_rate_limit("crawl", principal)  # F3：每用户限速前置（配额不因 404/502 泄漏）
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
    principal: AuthPrincipal | None = Security(get_optional_principal),
    # W1：角色子资源统一归属校验（唯一 owner 在 character_routes）。
    # 有 Bearer 主体时：他人卡片 / 无主存量卡一律 404；机器面（无 Bearer）不干预。
    _owned: dict = Depends(require_character_access),
):
    """火爬虫 + AgentReach 人设增强：抓取多源网络素材 → 处理为知识块 → 写入角色知识库。

    数据源：B站、小红书、Firecrawl、Jina Reader、Exa 等多源搜索。
    与对话内 search 工具不同，本端点将结果持久化到角色知识库供后续 RAG 检索。
    """
    _enforce_user_rate_limit("enrich", principal)  # F3：每用户限速前置
    card = _load_character_card(character_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")

    try:
        from persona_extractor.web_enricher import WebPersonaEnricher

        service = get_knowledge_service()
        # W5（缺陷 B 根治）：写入前先 ensure 已有索引（磁盘优先、版本化派生），
        # 并把统一知识服务注入 enricher —— 旧实现构造 WebPersonaEnricher() 漏传
        # knowledge_service，enrich 内部写库分支恒不成立，抓取结果从不落库。
        raw = _load_character_data(character_id) or {}
        service.ensure_index(character_id, character=build_character_aggregate(raw))
        enricher = WebPersonaEnricher(knowledge_service=service)
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
    except HTTPException:
        raise
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
