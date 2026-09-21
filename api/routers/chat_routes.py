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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user_id, require_role, verify_token
from api.database import User, get_db
from api.deps import deps
from api.main_routes import ChatRequest, ChatResponse, CreateSessionRequest

logger = logging.getLogger("api.routers.chat_routes")

router = APIRouter(tags=["chat"])
_bearer_scheme = HTTPBearer(auto_error=False)


async def _resolve_character_id(character_id: str) -> str:
    if character_id and character_id != "default":
        return character_id
    # P1-10：目录扫描是磁盘 IO（现已带指纹缓存，见 character_routes），
    # 仍不在事件循环上直接跑。
    from api.routers.character_routes import get_active_character_id

    return await asyncio.to_thread(get_active_character_id)


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
            await _resolve_character_id(req.character_id),
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
            await _resolve_character_id(req.character_id),
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
    before: int = Query(default=0, ge=0, description="Unix 秒时间戳：只取该时刻之前的消息"),
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
                messages = await asyncio.to_thread(
                    _query_history_sync, sm, session_id, limit, before
                )
                return {"messages": messages, "session_id": session_id}
            except Exception as e:
                logger.warning("Failed to query chat history by session_id: %s", e)
    messages = orch._memory.working.get_recent(limit)
    return {"messages": messages, "session_id": session_id}


def _query_history_sync(sm, session_id: str, limit: int, before: int) -> list[dict]:
    """同步 SQLite 查询（由 asyncio.to_thread 调度，避免阻塞事件循环）。

    分页条件类型修复（2026-09-17）：
    ``chat_history.created_at`` 由 ``TIMESTAMP DEFAULT CURRENT_TIMESTAMP`` 写入，
    SQLite 实际以 TEXT ``'YYYY-MM-DD HH:MM:SS'`` 存储。旧实现把整数秒直接
    ``str(before)`` 后与之比较，字符串字典序下 ``'2026-…' > '1758…'``，
    导致 ``created_at < ?`` 恒为 false —— 带 ``before`` 的分页**永远返回空**。
    现按 UTC 格式化为同构文本再比较（CURRENT_TIMESTAMP 即 UTC）。
    """
    with sm.get_connection() as conn:
        conditions = ["session_id = ?"]
        params: list = [session_id]
        if before > 0:
            conditions.append("created_at < ?")
            params.append(
                datetime.fromtimestamp(before, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )
        where_clause = " AND ".join(conditions)
        params.append(str(limit))
        rows = conn.execute(
            f"SELECT role, content, emotion_tag, created_at FROM chat_history "
            f"WHERE {where_clause} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows][::-1]


# ═══════════════════════════════════════════════════════
# WeChat Channel Management（2026-09-19：改为 admin-only 兼容面）
# 用户侧一律走 /api/wechat/channel*（JWT，只操作自己的通道）。
# ═══════════════════════════════════════════════════════


@router.post("/api/channels/wechat/connect")
async def manual_connect_wechat(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    """Admin 兼容：启动遗留全局/管理员通道。普通用户请用 /api/wechat/channel/connect。"""
    from wechat_direct.connector_registry import get_registry

    admin_id = _admin[0]
    try:
        result = get_registry().start_login(admin_id, slot=0, user_manager=deps.gf)
        return {**result, "message": "已触发管理员通道扫码（每人独立通道请使用 /api/wechat/channel）"}
    except Exception as e:  # noqa: BLE001
        logger.exception("管理员微信连接失败: %s", e)
        return {"status": "error", "message": str(e)}


@router.post("/api/channels/wechat/disconnect")
async def manual_disconnect_wechat(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    from wechat_direct.connector_registry import get_registry

    admin_id = _admin[0]
    get_registry().disconnect(admin_id, 0)
    conn = deps.get_wechat_connector()
    if conn:
        with contextlib.suppress(Exception):
            conn.stop()
    return {"status": "disconnected", "message": "已断开管理员兼容通道"}


@router.get("/api/channels/wechat/connection-status")
async def get_wechat_connection_status(
    _auth: bool = Security(verify_api_key_dep),
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
):
    """状态：带 JWT 且为普通用户时返回**自己的**通道；admin 无参兼容返回遗留全局。

    禁止再向未登录/普通用户广播全局 bot 在线状态。
    """
    from wechat_direct.wechat_connector import get_wechat_state

    uid = _try_user_id(credentials)
    if uid is not None:
        state = get_wechat_state(user_id=uid)
        return {
            "status": "connected" if state.get("connected") else "idle",
            "connected": bool(state.get("connected")),
            "message": "已连接" if state.get("connected") else "未连接",
            "started_at": state.get("started_at", 0),
            "wxid": state.get("bot_id", ""),
            "owner_user_id": uid,
        }
    # 无 JWT：仅在 API Key 场景下由 admin 使用；否则视为未连接（不泄露全局状态）
    raise HTTPException(status_code=401, detail="需要登录后查看你的微信通道状态")


@router.get("/api/channels/wechat/status")
async def get_wechat_status(
    _auth: bool = Security(verify_api_key_dep),
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
):
    from wechat_direct.wechat_connector import get_wechat_state

    uid = _try_user_id(credentials)
    if uid is None:
        raise HTTPException(status_code=401, detail="需要登录后查看你的微信通道状态")
    state = get_wechat_state(user_id=uid)
    return {
        "connected": bool(state.get("connected")),
        "uptime_seconds": state.get("uptime_seconds", 0),
        "bot_id": state.get("bot_id", ""),
        "last_activity": state.get("last_activity"),
        "messages_today": state.get("messages_today", 0),
        "reconnect_attempts": state.get("reconnect_attempts", 0),
        "owner_user_id": uid,
        "channels": state.get("channels"),
    }


def _try_user_id(credentials) -> int | None:
    if credentials is None:
        return None
    try:
        payload = verify_token(credentials.credentials, expected_type="access")
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, HTTPException, TypeError, ValueError):
        return None


@router.get("/api/channels/wechat/status-stream")
async def wechat_status_stream(
    _auth: bool = Security(verify_api_key_dep),
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
):
    """SSE 实时推送**当前登录用户**的微信连接状态。"""
    from wechat_direct.wechat_connector import get_wechat_state

    uid = _try_user_id(credentials)
    if uid is None:
        # EventSource 可能走 query api_key；再试 query 中的 JWT 不可行，直接拒绝
        raise HTTPException(status_code=401, detail="需要登录后订阅微信状态")

    async def _event_generator():
        last_signature: tuple = ()
        heartbeat_counter = 0
        while True:
            try:
                state = get_wechat_state(user_id=uid)
                payload = {
                    "connected": bool(state.get("connected")),
                    "uptime_seconds": state.get("uptime_seconds", 0),
                    "bot_id": state.get("bot_id", ""),
                    "last_activity": state.get("last_activity"),
                    "messages_today": state.get("messages_today", 0),
                    "reconnect_attempts": state.get("reconnect_attempts", 0),
                    "owner_user_id": uid,
                }
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
async def reconnect_wechat(
    _auth: bool = Security(verify_api_key_dep),
    _admin: tuple[int, User] = Depends(require_role("admin")),
):
    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import get_registry

    admin_id = _admin[0]
    path = channel_paths.credentials_path(admin_id, 0)
    if path.exists():
        path.unlink()
    get_registry().disconnect(admin_id, 0)
    return {"status": "reconnecting", "message": "已清除管理员通道凭证并触发重连"}
