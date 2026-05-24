from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Security, UploadFile
from fastapi.responses import Response

logger = logging.getLogger("rest_api.rag")

router = APIRouter(prefix="/api", tags=["rag"])

_orch = None
_verify_api_key = None

MAX_RAG_UPLOAD_SIZE = int(os.environ.get("MAX_RAG_UPLOAD_SIZE", str(10 * 1024 * 1024)))


def set_dependencies(orch, verify_api_key):
    global _orch, _verify_api_key
    _orch = orch
    _verify_api_key = verify_api_key


def _get_rag():
    if _orch:
        return _orch.components.get("rag") if hasattr(_orch, 'components') else getattr(_orch, '_rag', None)
    return None


@router.get("/rag/stats")
async def rag_stats(_auth: bool = Security(_verify_api_key)):
    rag = _get_rag()
    if rag:
        return rag.health_check()
    return {"available": False}


@router.post("/rag/search")
async def rag_search(query: str = "", top_k: int = Query(default=5, le=20), _auth: bool = Security(_verify_api_key)):
    rag = _get_rag()
    if not rag:
        raise HTTPException(503, "RAG引擎未初始化")
    results = rag.retrieve(query, top_k=top_k)
    return {"query": query, "results": results.get("results", []),
            "total_vector": results.get("total_vector", 0),
            "total_keyword": results.get("total_keyword", 0)}


@router.post("/rag/documents")
async def rag_upload_document(file: UploadFile = File(...), _auth: bool = Security(_verify_api_key)):
    rag = _get_rag()
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
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)] if len(text) > chunk_size else [text]
    for idx, chunk in enumerate(chunks):
        rag._sm.add_fact({"fact": chunk, "category": "upload",
                           "source": file.filename, "confidence": 1.0,
                           "chunk_index": idx, "total_chunks": len(chunks)})
    return {"status": "indexed", "filename": file.filename, "size": len(content), "chunks": len(chunks)}


# ═══ Voice / TTS API ═══

def _get_tts():
    if _orch:
        return _orch.components.get("voice") if hasattr(_orch, 'components') else None
    return None


@router.get("/voice/status")
async def voice_status(_auth: bool = Security(_verify_api_key)):
    tts = _get_tts()
    if tts:
        return tts.health_check()
    return {"enabled": False, "available_engines": []}


@router.post("/voice/synthesize")
async def voice_synthesize(text: str = Form(...), engine: str = Form(""), _auth: bool = Security(_verify_api_key)):
    tts = _get_tts()
    if not tts or not tts.enabled:
        raise HTTPException(503, "TTS未启用")
    if engine and engine in tts.available_engines:
        await tts.switch_engine(engine)
    audio = await tts.synthesize(text)
    if audio is None:
        raise HTTPException(500, "语音合成失败")
    return Response(content=audio, media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=tts.wav"})


# ═══ Plugin Management API ═══

@router.get("/plugins")
async def list_plugins(_auth: bool = Security(_verify_api_key)):
    try:
        plugin_path = Path(__file__).parent.parent.parent / "plugins" / "plugins.json"
        if plugin_path.exists():
            with open(plugin_path, encoding="utf-8") as f:
                data = json.load(f)
            return {"plugins": data.get("plugins", {})}
    except Exception:
        pass
    return {"plugins": {}}


@router.post("/plugins/{name}/toggle")
async def toggle_plugin(name: str, enabled: bool = True, _auth: bool = Security(_verify_api_key)):
    plugin_path = Path(__file__).parent.parent.parent / "plugins" / "plugins.json"
    data = {}
    if plugin_path.exists():
        with open(plugin_path, encoding="utf-8") as f:
            data = json.load(f)
    plugins = data.get("plugins", {})
    if name not in plugins:
        plugins[name] = {}
    plugins[name]["enabled"] = enabled
    plugins[name]["toggled_at"] = datetime.now().isoformat()
    data["plugins"] = plugins
    with open(plugin_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "ok", "name": name, "enabled": enabled}
