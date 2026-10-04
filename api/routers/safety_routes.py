"""
安全/RAG/语音/文件/缓存 路由 — safety/* + rag/* + voice/* + files/* + cache/*

来源：原 api.main_routes.py L1068/1077/1086/1104/1112/1123/1149/1157/1216/1233/1267/1282 共 12 端点

依赖：
- deps.get_safety() / deps.get_rag() / deps.get_tts()
- 共享常量：UPLOAD_DIR / MAX_UPLOAD_SIZE / MAX_RAG_UPLOAD_SIZE（来自 api.main_routes）
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Security, UploadFile
from fastapi.responses import FileResponse, Response

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_current_user, get_optional_principal, require_role
from api.database import User
from api.deps import deps
from api.main_routes import MAX_RAG_UPLOAD_SIZE, MAX_UPLOAD_SIZE, UPLOAD_DIR

logger = logging.getLogger("api.routers.safety_routes")

router = APIRouter(tags=["safety-infra"])

# P0 安全批 F5：合成文本上限（与 mimo_voice_routes 同一产品口径）
_MAX_SYNTH_TEXT_LEN = 600


class _RateLimiter:
    """每主体滑动窗口限速（内存计数；进程级）。

    仅本文件使用，勿抽公共模块（P0 安全批并行窗口纪律，与 mimo_voice_routes 各自内置）。
    """

    def __init__(self, max_events: int, window_seconds: float = 60.0):
        self._max = max_events
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """窗口内还有配额则记账并放行，否则拒绝（含失败请求，防绕过试错）。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < self._window]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self) -> None:
        """清空记账（测试隔离用）。"""
        with self._lock:
            self._hits.clear()


_VOICE_SYNTH_LIMITER = _RateLimiter(6)  # 6 次/分钟/用户


def _as_principal(candidate: Any) -> AuthPrincipal | None:
    """归一主体：直呼 handler（既有测试/脚本）会拿到 Depends 哨兵——按「无主体」解释。"""
    return candidate if isinstance(candidate, AuthPrincipal) else None


def _limit_or_429(limiter: _RateLimiter, principal: AuthPrincipal | None) -> None:
    key = f"user:{principal.user_id}" if principal is not None else "machine"
    if not limiter.check(key):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")


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
    """内容安全总开关（W6 缺陷 B/A 根治）。

    旧实现只改当前进程的 ``sf.enabled``：不落盘（重启即回退）、跨 worker 不
    生效。现走 ConfigManager 持久化 + live 应用——同时设置输入/输出两个
    开关保持「总开关」语义，重启不再反转。
    """
    cfg = deps.config
    receipt_summary: dict = {}
    persisted = False
    if cfg is not None and hasattr(cfg, "save_with_receipt"):
        components = getattr(deps.orch, "components", None) if deps.orch else None
        receipt = cfg.save_with_receipt(
            {
                "safety": {
                    "input_filter_enabled": enabled,
                    "output_filter_enabled": enabled,
                }
            },
            live_components=components,
        )
        persisted = True
        receipt_summary = {
            "persisted_version": receipt["persisted_version"],
            "effective_version": receipt["effective_version"],
            "in_sync": receipt["in_sync"],
        }
    sf = deps.get_safety()
    if sf is None and not persisted:
        return {"status": "not_available"}
    return {
        "status": "ok",
        "enabled": enabled,
        "persisted": persisted,
        **receipt_summary,
    }


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
    query: str = Query(default="", max_length=500),
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
    # P0 F3：共享知识库无归属写入（写进即全用户可检索注入），收 admin 控制面
    _admin: tuple[int, User] = Depends(require_role("admin")),
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

    def _store_chunks() -> None:
        # P0-4：add_fact 第一参是 fact 字符串——旧实现把整个 dict 位置传入，
        # str(dict) 的 repr 垃圾落进 user_facts 并被检索注入他人 prompt。
        for idx, chunk in enumerate(chunks):
            rag._sm.add_fact(
                chunk,
                category="upload",
                confidence=1.0,
                source=f"{file.filename}#{idx}",
            )

    # P1-10：同步 SQLite 写挪出事件循环
    await asyncio.to_thread(_store_chunks)
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
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """P0 F5：text>600 字 400 + 每用户限速 6 次/分钟（TTS 成本与滥用面收口）。"""
    principal = _as_principal(_principal)
    if len(text) > _MAX_SYNTH_TEXT_LEN:
        raise HTTPException(
            400, f"合成文本过长（{len(text)} 字 > 上限 {_MAX_SYNTH_TEXT_LEN} 字）"
        )
    _limit_or_429(_VOICE_SYNTH_LIMITER, principal)

    tts = deps.get_tts()
    if not tts or not tts.enabled:
        raise HTTPException(503, "TTS未启用")
    if engine and engine in tts.available_engines:
        await tts.switch_engine(engine)
    audio = await tts.synthesize(text)
    if audio is None or len(audio) == 0:
        raise HTTPException(500, "语音合成失败")
    # W7：MIME 按实际格式（云端 mp3 / SAPI 兜底 wav），不再固定 audio/wav
    ext = "mp3" if audio.fmt == "mp3" else audio.fmt
    return Response(
        content=audio.data,
        media_type=audio.mime,
        headers={"Content-Disposition": f"inline; filename=tts.{ext}"},
    )


# ═══════════════════════════════════════════════════════
# Multimodal File Upload
# ═══════════════════════════════════════════════════════


@router.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),  # noqa: B008
    _auth: bool = Security(verify_api_key_dep),
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """P0 F4 归属化上传：落 ``data/uploads/<user_id>/`` 子目录（已存在则复用），
    文件名加 uuid4 随机段（防猜测/防碰撞）；无登录主体一律 401——不可归属的
    写入不再收（存量根目录文件从此仅 admin 可读）。"""
    principal = _as_principal(_principal)
    if principal is None:
        raise HTTPException(401, "需要登录主体（Bearer token）才能上传文件")
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
    user_dir = UPLOAD_DIR / str(principal.user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name}"
    with open(dest, "wb") as f:
        f.write(content)
    mime = file.content_type or "application/octet-stream"
    msg_type = "image" if mime.startswith("image/") else "voice" if mime.startswith("audio/") else "file"
    return {
        "status": "ok",
        "filename": dest.name,
        "size": len(content),
        "mime_type": mime,
        "message_type": msg_type,
        "url": f"/api/files/{principal.user_id}/{dest.name}",
    }


@router.get("/api/files/{filename:path}")
async def serve_file(
    filename: str,
    _auth: bool = Security(verify_api_key_dep),
    _principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """P0 F4 按主体校验归属后回源：

    - ``<user_id>/`` 子目录内文件：仅本人与 admin 可读；
    - 存量根目录文件（无归属）：仅 admin 可读；
    - 路径穿越防护保留并加强：拒绝 ``..`` 段 / 反斜杠 / 盘符形态，
      resolve 后仍须落在 uploads 目录内。
    """
    principal = _as_principal(_principal)
    is_admin = principal is not None and principal.role == "admin"

    parts = [p for p in filename.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or ":" in "".join(parts):
        raise HTTPException(403, "Access denied")
    upload_dir_resolved = UPLOAD_DIR.resolve()
    file_path = UPLOAD_DIR
    for part in parts:
        file_path = file_path / part
    file_path_resolved = file_path.resolve()
    if file_path_resolved != upload_dir_resolved and not str(file_path_resolved).startswith(
        str(upload_dir_resolved) + os.sep
    ):
        raise HTTPException(403, "Access denied")
    if not file_path_resolved.exists():
        raise HTTPException(404, "文件不存在")
    if not file_path_resolved.is_file():
        raise HTTPException(400, "Not a file")

    rel_parts = file_path_resolved.relative_to(upload_dir_resolved).parts
    if len(rel_parts) == 1:
        # 存量根目录文件：无归属，仅 admin
        if not is_admin:
            raise HTTPException(403, "Access denied")
    else:
        owner_dir = rel_parts[0]
        if not is_admin and (principal is None or owner_dir != str(principal.user_id)):
            raise HTTPException(403, "Access denied")
    return FileResponse(file_path_resolved)


# ═══════════════════════════════════════════════════════
# Cache Statistics API
# ═══════════════════════════════════════════════════════

# W10（2026-09-27）：LLMCache 目前**未接入对话链路**——全仓生产代码没有任何
# cache.get / cache.set 调用方（唯一引用就是本文件的两个端点）。旧实现每个
# 请求 new 一个 LLMCache，统计恒为零，面板却显得"缓存开着、正在省 token"。
# 若未来真正把 LLMCache 接入 llm_provider 网关，必须同步把此常量改为 True
# 并补 hits/misses 与真实调用数对齐的契约测试。
CACHE_INTEGRATED = False


@router.get("/api/cache/stats")
async def cache_stats(_auth: bool = Security(verify_api_key_dep)):
    if not CACHE_INTEGRATED:
        return {
            "available": False,
            "integrated": False,
            "status": "not_integrated",
            "detail": "LLM 缓存未接入对话链路：llm.cache 配置与以下统计当前不生效，不代表真实省 token",
        }
    try:
        from cache.llm_cache import LLMCache
        cache = LLMCache()
        return {
            "available": cache.enabled,
            "integrated": True,
            "stats": cache.get_stats(),
            "health": cache.health_check(),
        }
    except Exception:
        logger.exception("Cache stats query failed")
        return {"available": False, "integrated": CACHE_INTEGRATED, "error": "internal_error"}


@router.post("/api/cache/invalidate")
async def cache_invalidate(
    pattern: str = "*",
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    if not CACHE_INTEGRATED:
        raise HTTPException(503, "Cache not_integrated: LLM 缓存未接入对话链路，无键可失效")
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
