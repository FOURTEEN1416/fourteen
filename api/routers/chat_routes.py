from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, Security
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("rest_api.chat")

router = APIRouter(prefix="/api", tags=["chat"])

_orch = None
_verify_api_key = None


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    session_id: str = Field(default="", max_length=128)
    message_type: str = Field(default="text", pattern=r"^(text|image|voice|file)$")


class ChatResponse(BaseModel):
    reply: str
    trace_id: str = ""
    emotion: dict | None = None


def set_dependencies(orch, verify_api_key):
    global _orch, _verify_api_key
    _orch = orch
    _verify_api_key = verify_api_key


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
    if not _orch:
        raise HTTPException(503, "Orchestrator not initialized")
    result = await _orch.process_message(req.message, req.session_id, req.message_type)
    return ChatResponse(
        reply=result.get("reply", ""),
        trace_id=result.get("trace_id", ""),
        emotion=result.get("emotion"),
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, _auth: bool = Security(_verify_api_key)):
    if not _orch or not hasattr(_orch, 'process_message_stream'):
        raise HTTPException(503, "Stream not available")

    async def event_generator():
        async for token in _orch.process_message_stream(req.message, req.session_id, req.message_type):  # type: ignore
            yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/chat/history")
async def chat_history(session_id: str = "", limit: int = Query(default=20, ge=1, le=100), _auth: bool = Security(_verify_api_key)):
    if not _orch or not _orch._memory:
        return {"messages": []}
    messages = _orch._memory.working.get_recent(limit)
    return {"messages": messages, "session_id": session_id}


@router.get("/emotion/state")
async def emotion_state(_auth: bool = Security(_verify_api_key)):
    if _orch and _orch._emotion:
        return _orch._emotion.health_check()
    return {}


@router.get("/emotion/trend")
async def emotion_trend(days: int = Query(default=7, ge=1, le=30), _auth: bool = Security(_verify_api_key)):
    if not _orch or not _orch._emotion:
        return {"trend": [], "days": days}
    trend = getattr(_orch._emotion, '_emotion_history', [])
    return {"trend": trend[-days * 20:], "days": days}
