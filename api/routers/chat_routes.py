"""
聊天与会话路由 — chat/session + wechat channels

来源：原 api.main_routes.py L112/130/168/180/191/682/701/709/717/729 共 10 端点

依赖：
- deps.orch / deps.sessions / deps.gf / deps.get_wechat_connector()
- ChatRequest/ChatResponse/CreateSessionRequest 模型来自 api.main_routes
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user_id, require_role
from api.database import User, get_db
from api.deps import deps
from api.main_routes import ChatRequest, ChatResponse, CreateSessionRequest

logger = logging.getLogger("api.routers.chat_routes")

router = APIRouter(tags=["chat"])


def _resolve_character_id(character_id: str) -> str:
    if character_id and character_id != "default":
        return character_id
    from api.routers.character_routes import get_active_character_id

    return get_active_character_id()


# ═══════════════════════════════════════════════════════
# Chat / Session
# ═══════════════════════════════════════════════════════


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    _auth: bool = Security(verify_api_key_dep),
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    orch = deps.orch
    if not orch:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    # 读取用户级 LLM 配置（API Key 隔离）
    user_llm_config = None
    user = None
    try:
        user = await db.get(User, user_id)
        if user and user.llm_config:
            user_llm_config = user.llm_config if isinstance(user.llm_config, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load user %s LLM config, using global: %s", user_id, e)

    from api.byok import ensure_user_has_key

    try:
        llm_cfg = orch.components.get("config").config.llm if orch.components else None
        ensure_user_has_key(user, llm_cfg)
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        result = await orch.process_message(
            req.message,
            req.session_id,
            req.message_type,
            _resolve_character_id(req.character_id),
            user_llm_config=user_llm_config,
            user_id=user_id,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="LLM response timeout",
            headers={"X-Error-Code": "LLM_TIMEOUT"},
        ) from None
    except ConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Upstream connection error",
            headers={"X-Error-Code": "NETWORK_ERROR"},
        ) from None
    return ChatResponse(
        reply=result.get("reply", ""),
        trace_id=result.get("trace_id", ""),
        emotion=result.get("emotion"),
    )


@router.post("/api/chat/stream")
async def chat_stream(
    req: ChatRequest,
    _auth: bool = Security(verify_api_key_dep),
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    orch = deps.orch
    if not orch or not hasattr(orch, "process_message_stream"):
        raise HTTPException(
            status_code=503,
            detail="Stream not available",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    # 读取用户级 LLM 配置（API Key 隔离）— 与 /api/chat 保持一致
    user_llm_config = None
    user = None
    try:
        user = await db.get(User, user_id)
        if user and user.llm_config:
            user_llm_config = user.llm_config if isinstance(user.llm_config, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load user %s LLM config for stream, using global: %s", user_id, e)

    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load user %s LLM config for stream, using global: %s", user_id, e)

    # BYOK 强制（W1）：异常必须在读配置的 try 外抛出，避免被兜底吞掉
    from api.byok import ensure_user_has_key

    try:
        llm_cfg = orch.components.get("config").config.llm if orch.components else None
        ensure_user_has_key(user, llm_cfg)
    except HTTPException:
        raise
    except Exception:
        pass

    async def event_generator():
        stream_gen = orch.process_message_stream(
            req.message,
            req.session_id,
            req.message_type,
            _resolve_character_id(req.character_id),
            user_llm_config=user_llm_config,
            user_id=user_id,
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
            logger.debug("SSE client disconnected, cancelling stream for session %s", req.session_id)
            raise
        except TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'error': 'LLM timeout'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.warning("Stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': 'stream failed'}, ensure_ascii=False)}\n\n"
        finally:
            with contextlib.suppress(Exception):
                await stream_gen.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/session")
async def create_session(req: CreateSessionRequest, _auth: bool = Security(verify_api_key_dep)):
    orch = deps.orch
    sessions = deps.sessions
    if sessions:
        session_id = sessions.create_session(req.user_id, req.channel)
        if orch and orch._memory:
            orch._memory.working.start_session(session_id, req.channel)
        return {"session_id": session_id}
    return {"session_id": ""}


@router.get("/api/sessions")
async def list_sessions(_auth: bool = Security(verify_api_key_dep)):
    sessions = deps.sessions
    if sessions:
        return {
            "sessions": sessions.get_active_sessions(),
            "active_count": sessions.active_count,
        }
    return {"sessions": [], "active_count": 0}


@router.get("/api/chat/history")
async def chat_history(
    session_id: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    before: int = Query(default=0, ge=0, description="Timestamp to load messages before"),
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    orch = deps.orch
    if not orch or not orch._memory:
        return {"messages": []}
    if session_id:
        sm = getattr(orch._memory, "structured_memory", None) or getattr(orch._memory, "_sm", None)
        if sm and hasattr(sm, "get_connection"):
            try:
                with sm.get_connection() as conn:
                    conditions = ["session_id = ?"]
                    params = [session_id]
                    if before > 0:
                        conditions.append("created_at < ?")
                        params.append(str(before))
                    where_clause = " AND ".join(conditions)
                    params.append(str(limit))
                    rows = conn.execute(
                        f"SELECT role, content, emotion_tag, created_at FROM chat_history "
                        f"WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
                        tuple(params),
                    ).fetchall()
                    messages = [dict(r) for r in rows][::-1]
                    return {"messages": messages, "session_id": session_id}
            except Exception as e:
                logger.warning("Failed to query chat history by session_id: %s", e)
    messages = orch._memory.working.get_recent(limit)
    return {"messages": messages, "session_id": session_id}


# ═══════════════════════════════════════════════════════
# WeChat Channel Management
# ═══════════════════════════════════════════════════════


@router.post("/api/channels/wechat/connect")
async def manual_connect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        return {"status": "connected", "message": "微信已连接"}

    def _do_connect():
        try:
            from wechat_direct import WeChatConnector
            # 修复 P0-WX1：必须传入 UserManager（deps.gf），而非 Orchestrator（deps.orch）。
            # UserManager 负责多用户路由 + 角色隔离；Orchestrator 只处理单条消息。
            connector = WeChatConnector(deps.gf)
            connector.run()
        except Exception as e:
            logger.exception("微信连接失败: %s", e)

    thread = threading.Thread(target=_do_connect, daemon=True)
    thread.start()
    return {"status": "connecting", "message": "微信连接已触发，请看终端/页面二维码扫码登录"}


@router.post("/api/channels/wechat/disconnect")
async def manual_disconnect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn:
        conn.stop()
    return {"status": "disconnected", "message": "微信已断开"}


@router.get("/api/channels/wechat/connection-status")
async def get_wechat_connection_status(_auth: bool = Security(verify_api_key_dep)):
    from wechat_direct import get_wechat_state
    state = get_wechat_state()
    if state.get("connected"):
        return {
            "status": "connected",
            "connected": True,
            "message": "已连接",
            "started_at": state.get("started_at", 0),
            "wxid": state.get("bot_id", ""),
        }
    return {"status": "idle", "connected": False, "message": "未连接"}


@router.get("/api/channels/wechat/status")
async def get_wechat_status(_auth: bool = Security(verify_api_key_dep)):
    from wechat_direct import get_wechat_state
    state = get_wechat_state()
    return {
        "connected": bool(state.get("connected")),
        "uptime_seconds": state.get("uptime_seconds", 0),
        "bot_id": state.get("bot_id", ""),
        "last_activity": state.get("last_activity"),
        "messages_today": state.get("messages_today", 0),
        "reconnect_attempts": state.get("reconnect_attempts", 0),
    }


@router.get("/api/channels/wechat/status-stream")
async def wechat_status_stream(_auth: bool = Security(verify_api_key_dep)):
    """SSE 实时推送微信连接状态，解决前端轮询导致的状态抖动问题。

    优化（B5）：
    - 只在状态变化时推送，避免无谓的重复数据
    - 30 秒心跳保活（SSE 注释行），防止代理超时断开
    - 客户端断开时立即退出循环（捕获 CancelledError）
    """
    from wechat_direct import get_wechat_state

    async def _event_generator():
        last_signature: tuple = ()
        heartbeat_counter = 0
        while True:
            try:
                state = get_wechat_state()
                payload = {
                    "connected": bool(state.get("connected")),
                    "uptime_seconds": state.get("uptime_seconds", 0),
                    "bot_id": state.get("bot_id", ""),
                    "last_activity": state.get("last_activity"),
                    "messages_today": state.get("messages_today", 0),
                    "reconnect_attempts": state.get("reconnect_attempts", 0),
                }
                # 计算状态签名，只在变化时推送
                current_signature = (
                    payload["connected"],
                    payload["bot_id"],
                    payload["messages_today"],
                    payload["reconnect_attempts"],
                )
                if current_signature != last_signature:
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_signature = current_signature
                    heartbeat_counter = 0
                else:
                    heartbeat_counter += 1
                    # 每 15 个周期（约 30 秒）发一次心跳保活
                    if heartbeat_counter >= 15:
                        yield ": heartbeat\n\n"
                        heartbeat_counter = 0
            except asyncio.CancelledError:
                logger.debug("WeChat status stream client disconnected")
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("WeChat status stream error: %s", e)
            await asyncio.sleep(2.0)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/channels/wechat/reconnect")
async def reconnect_wechat(_auth: bool = Security(verify_api_key_dep)):
    conn = deps.get_wechat_connector()
    if conn and conn.token:
        conn.stop()

    def _do_reconnect():
        import wechat_direct.wechat_connector as wc
        from wechat_direct import WeChatConnector
        time.sleep(1)
        wc._clear_credentials()
        new_conn = WeChatConnector(deps.gf)
        new_conn.run()

    thread = threading.Thread(target=_do_reconnect, daemon=True)
    thread.start()
    return {"status": "reconnecting"}
