"""
安全/RAG/语音/文件/缓存 路由 — safety/* + rag/* + voice/* + files/* + cache/*

来源：原 api.main_routes.py L1068/1077/1086/1104/1112/1123/1149/1157/1216/1233/1267/1282 共 12 端点

依赖：
- deps.get_safety() / deps.get_rag() / deps.get_tts()
- 共享常量：UPLOAD_DIR / MAX_UPLOAD_SIZE / MAX_RAG_UPLOAD_SIZE（来自 api.main_routes）
"""

from __future__ import annotations

import logging
import os
import re
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Security, UploadFile
from fastapi.responses import FileResponse, Response

from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user, get_current_user_id, require_role
from api.database import User
from api.deps import deps
from api.main_routes import MAX_RAG_UPLOAD_SIZE, MAX_UPLOAD_SIZE, UPLOAD_DIR

logger = logging.getLogger("api._safety_routes")

router = APIRouter(tags=["safety-infra"])


# ═══════════════════════════════════════════════════════
# Safety Dashboard API
# ═══════════════════════════════════════════════════════


def _user_filter_scope(current_user: User) -> int | None:
    """管理员可查看全部，普通用户只能查看本账号数据。"""
    return None if current_user.role == "admin" else current_user.id


@router.get("/api/safety/stats")
async def safety_stats(
    _auth: bool = Security(verify_api_key_dep),
    current_user: User = Security(get_current_user),
):
    sf = deps.get_safety()
    return deps.safety_log_mgr.get_stats(
        enabled=sf.enabled if sf else False,
        user_id=_user_filter_scope(current_user),
    )


@router.get("/api/safety/log")
async def safety_log(
    limit: int = Query(default=50, le=200),
    _auth: bool = Security(verify_api_key_dep),
    current_user: User = Security(get_current_user),
):
    return {"log": deps.safety_log_mgr.get_recent(limit, user_id=_user_filter_scope(current_user))}


@router.post("/api/safety/config")
async def safety_config(
    enabled: bool = True,
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    sf = deps.get_safety()
    if sf:
        sf.enabled = enabled
        return {"status": "ok", "enabled": enabled}
    return {"status": "not_available"}


# ═══════════════════════════════════════════════════════
# RAG Knowledge Base API
# ═══════════════════════════════════════════════════════


@router.get("/api/rag/stats")
async def rag_stats(_auth: bool = Security(verify_api_key_dep)):
    rag = deps.get_rag()
    if rag:
        return rag.health_check()
    return {"available": False}


@router.post("/api/rag/search")
async def rag_search(
    query: str = "",
    top_k: int = Query(default=5, le=20),
    _auth: bool = Security(verify_api_key_dep),
):
    rag = deps.get_rag()
    if not rag:
        raise HTTPException(503, "RAG引擎未初始化")
    results = rag.retrieve(query, top_k=top_k)
    return {
        "query": query,
        "results": results.get("results", []),
        "total_vector": results.get("total_vector", 0),
        "total_keyword": results.get("total_keyword", 0),
    }


@router.post("/api/rag/documents")
async def rag_upload_document(
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
):
    rag = deps.get_rag()
    if not rag:
        raise HTTPException(503, "RAG引擎未初始化")
    content = await file.read()
    if len(content) > MAX_RAG_UPLOAD_SIZE:
        raise HTTPException(413, f"文档大小超过限制 ({MAX_RAG_UPLOAD_SIZE // 1024 // 1024}MB)")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("gbk", errors="replace")
    chunk_size = 2000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] if len(text) > chunk_size else [text]
    for idx, chunk in enumerate(chunks):
        rag._sm.add_fact({
            "fact": chunk,
            "category": "upload",
            "source": file.filename,
            "confidence": 1.0,
            "chunk_index": idx,
            "total_chunks": len(chunks),
        })
    return {"status": "indexed", "filename": file.filename, "size": len(content), "chunks": len(chunks)}


# ═══════════════════════════════════════════════════════
# Voice / TTS API
# ═══════════════════════════════════════════════════════


@router.get("/api/voice/status")
async def voice_status(_auth: bool = Security(verify_api_key_dep)):
    tts = deps.get_tts()
    if tts:
        return tts.health_check()
    return {"enabled": False, "available_engines": []}


@router.post("/api/voice/synthesize")
async def voice_synthesize(
    text: str = Form(...),
    engine: str = Form(""),
    _auth: bool = Security(verify_api_key_dep),
):
    tts = deps.get_tts()
    if not tts or not tts.enabled:
        raise HTTPException(503, "TTS未启用")
    if engine and engine in tts.available_engines:
        await tts.switch_engine(engine)
    audio = await tts.synthesize(text)
    if audio is None:
        raise HTTPException(500, "语音合成失败")
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Content-Disposition": "inline; filename=tts.wav"},
    )


# ═══════════════════════════════════════════════════════
# Multimodal File Upload
# ═══════════════════════════════════════════════════════


@router.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
):
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)  # type: ignore[arg-type]
    dest = UPLOAD_DIR / f"{int(time.time())}_{safe_name}"
    with open(dest, "wb") as f:
        f.write(content)
    mime = file.content_type or "application/octet-stream"
    msg_type = "image" if mime.startswith("image/") else "voice" if mime.startswith("audio/") else "file"
    return {
        "status": "ok",
        "filename": safe_name,
        "size": len(content),
        "mime_type": mime,
        "message_type": msg_type,
        "url": f"/api/files/{dest.name}",
    }


@router.get("/api/files/{filename}")
async def serve_file(
    filename: str,
    _auth: bool = Security(verify_api_key_dep),
):
    safe_name = os.path.basename(filename)
    file_path = (UPLOAD_DIR / safe_name).resolve()
    upload_dir_resolved = UPLOAD_DIR.resolve()
    if not str(file_path).startswith(str(upload_dir_resolved)):
        raise HTTPException(403, "Access denied")
    if not file_path.exists():
        raise HTTPException(404, "文件不存在")
    if not file_path.is_file():
        raise HTTPException(400, "Not a file")
    return FileResponse(file_path)


# ═══════════════════════════════════════════════════════
# Cache Statistics API
# ═══════════════════════════════════════════════════════


@router.get("/api/cache/stats")
async def cache_stats(_auth: bool = Security(verify_api_key_dep)):
    try:
        from cache.llm_cache import LLMCache
        cache = LLMCache()
        return {
            "available": cache.enabled,
            "stats": cache.get_stats(),
            "health": cache.health_check(),
        }
    except Exception:
        logger.exception("Cache stats query failed")
        return {"available": False, "error": "internal_error"}


@router.post("/api/cache/invalidate")
async def cache_invalidate(
    pattern: str = "*",
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    try:
        from cache.llm_cache import LLMCache
        cache = LLMCache()
        if not cache.enabled:
            raise HTTPException(503, "Cache not enabled")
        deleted = cache.invalidate(pattern)
        return {"status": "ok", "deleted_keys": deleted}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Cache invalidation failed")
        raise HTTPException(500, "Cache invalidation failed") from None
