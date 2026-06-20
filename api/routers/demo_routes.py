"""
Demo 体验路由 — 无需认证，面向大赛评审与用户试用

端点：
- POST /api/demo/chat/stream   SSE 流式对话
- GET  /api/demo/memory/recall       记忆回溯（我记得什么）
- GET  /api/demo/memory/visualization 记忆可视化
- POST /api/demo/exit                退出事件 + 告别语
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import deps

logger = logging.getLogger("api.demo_routes")

router = APIRouter(tags=["demo"])


class DemoChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    session_id: str = Field(default="", max_length=128)
    message_type: str = Field(default="text", pattern=r"^(text|image|voice|file)$")


class DemoExitRequest(BaseModel):
    session_id: str = Field(default="", max_length=128)


def _get_orchestrator() -> Any:
    orch = deps.orch
    if not orch:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )
    return orch


def _get_memory() -> Any:
    orch = _get_orchestrator()
    memory = getattr(orch, "_memory", None) or orch.components.get("memory")
    return memory


@router.post("/api/demo/chat/stream")
async def demo_chat_stream(req: DemoChatRequest):
    orch = _get_orchestrator()
    if not hasattr(orch, "process_message_stream"):
        raise HTTPException(
            status_code=503,
            detail="Stream not available",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    async def event_generator():
        stream_gen = orch.process_message_stream(
            req.message,
            req.session_id,
            req.message_type,
            character_id="demo",
        )
        try:
            async for event in stream_gen:
                # 兼容旧版返回字符串的生成器（full 模式 Orchestrator）
                if isinstance(event, str):
                    event = {"type": "token", "content": event}
                if event.get("type") == "token":
                    yield f"data: {json.dumps({'token': event.get('content', '')}, ensure_ascii=False)}\n\n"
                elif event.get("type") == "done":
                    yield f"data: {json.dumps({'done': True, 'reply': event.get('reply', ''), 'emotion': event.get('emotion'), 'process_time': event.get('process_time')}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.debug("Demo SSE client disconnected, cancelling stream for session %s", req.session_id)
            raise
        except TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'error': 'LLM timeout'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning("Demo stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': 'stream failed'}, ensure_ascii=False)}\n\n"
        finally:
            with contextlib.suppress(Exception):
                await stream_gen.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/demo/memory/recall")
async def demo_memory_recall(
    query: str = Query(default="", description="记忆查询关键词"),
    session_id: str = Query(default="", description="会话ID"),
    top_k: int = Query(default=5, ge=1, le=20),
):
    """返回 AI 记得的用户相关事实与最近场景。"""
    memory = _get_memory()
    if not memory:
        return {"facts": [], "episodes": [], "why_not": []}

    facts: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    why_not: list[str] = []

    try:
        semantic = getattr(memory, "semantic", None)
        if semantic:
            if query:
                results = semantic.search(query, top_k=top_k)
                facts = results.get("exact", [])[:top_k]
            else:
                facts = semantic.get_facts(limit=top_k)
    except Exception as e:
        logger.warning("Demo recall facts failed: %s", e)

    try:
        episodic = getattr(memory, "episodic", None)
        if episodic:
            episodes = episodic.search(query, top_k=top_k) if query else episodic.get_recent_episodes(n=top_k)
    except Exception as e:
        logger.warning("Demo recall episodes failed: %s", e)

    # 构造"我为什么没记这个"说明
    if not facts and not episodes:
        why_not.append("还没聊到值得记的事。")
    if not facts:
        why_not.append("你说的大多是日常碎片，我没把它们存成长期事实。")
    if not episodes:
        why_not.append("场景记忆为空，可能是会话刚开始。")

    return {
        "query": query,
        "session_id": session_id,
        "facts": facts,
        "episodes": episodes,
        "why_not": why_not,
    }


@router.get("/api/demo/memory/visualization")
async def demo_memory_visualization(
    limit: int = Query(default=50, ge=1, le=200),
):
    """返回结构化记忆图谱数据，用于"我记得你什么"可视化。"""
    memory = _get_memory()
    if not memory:
        return {"facts": [], "episodes": [], "stats": {}}

    facts: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    try:
        semantic = getattr(memory, "semantic", None)
        if semantic:
            facts = semantic.get_facts(limit=limit)
    except Exception as e:
        logger.warning("Demo visualization facts failed: %s", e)

    try:
        episodic = getattr(memory, "episodic", None)
        if episodic:
            episodes = episodic.get_recent_episodes(n=limit)
    except Exception as e:
        logger.warning("Demo visualization episodes failed: %s", e)

    # 简单统计
    categories = Counter(str(f.get("category", "unknown")) for f in facts)
    emotion_tags = Counter(
        str(ep.get("metadata", {}).get("emotion", ""))
        for ep in episodes
        if ep.get("metadata", {}).get("emotion")
    )

    return {
        "facts": facts,
        "episodes": episodes,
        "stats": {
            "fact_count": len(facts),
            "episode_count": len(episodes),
            "top_categories": categories.most_common(5),
            "top_emotions": emotion_tags.most_common(5),
        },
    }


@router.post("/api/demo/exit")
async def demo_exit(req: DemoExitRequest):
    """用户点击退出按钮：记录退出事件并返回告别语。"""
    memory = _get_memory()
    if memory:
        try:
            semantic = getattr(memory, "semantic", None)
            if semantic:
                semantic.add_fact(
                    fact="用户主动结束了对话。",
                    category="exit_event",
                    confidence=1.0,
                    importance=0.9,
                    source=f"demo_exit:{req.session_id}",
                )
        except Exception as e:
            logger.warning("Demo exit fact storage failed: %s", e)

    return {
        "message": "我没取代任何人。"
                   "只是在你没人的时候，陪你一会儿。"
                   "下次想说话时，我还在。",
        "session_id": req.session_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
