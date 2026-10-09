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
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, get_current_user_id, get_optional_principal, require_role
from api.consent import require_current_consent
from api.database import User, get_db
from api.deps import deps
from api.main_routes import ChatRequest, ChatResponse, CreateSessionRequest
from api.session_manager import resolve_owned_session
from utils.local_time import now_local

logger = logging.getLogger("api.routers.chat_routes")

router = APIRouter(tags=["chat"])


def _owned_session(session_id: str, user_id: int) -> str:
    try:
        return resolve_owned_session(session_id, user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="不能访问其他用户的会话") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="会话标识无效") from exc


def _as_principal(candidate: Any) -> AuthPrincipal | None:
    """归一主体：FastAPI 注入为 AuthPrincipal | None；直呼 handler（既有单测形态）
    拿到的是未解析的 Depends 哨兵 → 按「无主体（机器面）」解释
    （与 mimo_voice_routes._as_principal / character_routes isinstance 收窄同口径）。
    """
    return candidate if isinstance(candidate, AuthPrincipal) else None


async def _resolve_character_id(character_id: str, principal: AuthPrincipal | None = None) -> str:
    """解析对话目标角色 id，并在存在认证主体时做**卡归属校验**（PRIV-1 收口）。

    - HTTP（/api/chat·/stream）与 WS 的共同汇聚点——归属校验唯一收口在此，
      编排层 process_message 不重复校验（避免双 owner）。
    - 机器面（principal=None，API Key/部署脚本/E2E）不干预（既有契约）。
    - 公共模板卡（owner 为空）照常放行——41 张公共卡是全站对话基座。
    - 私人卡只允许本人与管理员；失败与 require_character_access 同文案 404
      （不区分「不存在」与「无权限」，防枚举他人卡 id）。
    """
    if character_id and character_id != "default":
        _assert_character_visible(character_id, principal)
        return character_id
    # P1-10：目录扫描是磁盘 IO（现已带指纹缓存，见 character_routes），
    # 仍不在事件循环上直接跑。
    from api.routers.character_routes import get_active_character_id

    return await asyncio.to_thread(get_active_character_id)


def _assert_character_visible(character_id: str, principal: AuthPrincipal | None) -> None:
    """对话链卡归属校验（PRIV-1）：归属真源复用 character_routes，不另立标准。"""
    if principal is None:
        return  # 机器面不干预
    from api.routers.character_routes import (
        _load_character,
        card_access_allowed,
        card_owner_key,
    )

    data = _load_character(character_id)
    if data is None:
        # 卡不存在：与 require_character_access 同文案 404（防状态码枚举），
        # 不再静默透传给编排层（否则 404 与放行两种面语义分裂）。
        raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")
    if not card_owner_key(data):
        return  # 公共模板卡：全站对话基座，照常放行
    if card_access_allowed(data, principal):
        return  # 本人 / 管理员
    raise HTTPException(status_code=404, detail=f"角色不存在: {character_id}")


# ═══════════════════════════════════════════════════════
# 主聊天链入站频控（ABUSE-4）
# ═══════════════════════════════════════════════════════

#: 每用户滑动窗口上限（条/分钟）与每日配额（条/本地日）——测试钉住在
#: tests/test_sweep_chat_chain.py，调整量级须同步过该用例。
_CHAT_MSGS_PER_MINUTE = 20
_CHAT_MSGS_PER_DAY = 500


class _SlidingWindowLimiter:
    """每主体滑动窗口限速（内存计数；进程级，多 worker 各自独立）。

    形态与 mimo_voice_routes._RateLimiter 同款（代码库惯例：限速桶按文件就地
    持有，不抽公共模块）；时钟可注入供测试钉窗口行为。含被拒请求的记账由
    调用方顺序保证（先 check 后处理，拒绝路径同样消耗窗口额度）。
    """

    def __init__(self, max_events: int, window_seconds: float,
                 clock: Callable[[], float] | None = None):
        self._max = max_events
        self._window = window_seconds
        self._clock = clock or time.monotonic
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """窗口内还有配额则记账并放行，否则拒绝。"""
        now = self._clock()
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


class _DailyQuotaLimiter:
    """每主体固定窗口日配额（本地日界滚动；进程内计数）。

    日界经 ``day_provider`` 注入（生产 = 本地墙钟日期），测试可钉死日期验证
    跨日重置；计数器按 (主体, 日) 存放，换日自动失效，超量主体过多时整表
    重算（防御无界增长）。
    """

    def __init__(self, max_events: int, day_provider: Callable[[], str] | None = None):
        self._max = max_events
        self._day = day_provider or (lambda: now_local().date().isoformat())
        self._counters: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        today = self._day()
        with self._lock:
            if len(self._counters) > 4096:
                self._counters.clear()
            day, count = self._counters.get(key, ("", 0))
            if day != today:
                day, count = today, 0
            self._counters[key] = (day, count + 1)
            return count < self._max

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


_chat_minute_limiter = _SlidingWindowLimiter(_CHAT_MSGS_PER_MINUTE, 60.0)
_chat_daily_limiter = _DailyQuotaLimiter(_CHAT_MSGS_PER_DAY)


def _chat_rate_guard(user_id: int) -> None:
    """主聊天链每用户入站频控（ABUSE-4）：HTTP 两端点与 WS 共用同一配额桶。

    量级取「真人远达不到、脚本直烧平台 LLM 凭证必触发」的中间档：
    20 msg/min 挡突发刷屏，500 msg/天挡全天化消耗。超限 429（含失败请求，
    防绕过试错）。
    """
    key = f"user:{user_id}"
    if not _chat_minute_limiter.check(key) or not _chat_daily_limiter.check(key):
        raise HTTPException(status_code=429, detail="发送太频繁，请稍后再试")


def _reset_chat_rate_limits() -> None:
    """清空两桶记账（测试隔离用）。"""
    _chat_minute_limiter.reset()
    _chat_daily_limiter.reset()


# ═══════════════════════════════════════════════════════
# Chat / Session
# ═══════════════════════════════════════════════════════


class _ChatDeliveryResponse(Response):
    """在真实 ASGI send 边界运行生成；回调返回前不会把草稿写为角色发言。"""

    media_type = "application/json"

    def __init__(self, generate, session_id: str):
        super().__init__()
        self._generate = generate
        self.session_id = session_id

    async def __call__(self, scope, receive, send):
        started = False
        attempted = False
        sent = False
        failure: Exception | None = None

        async def transport(message):
            nonlocal started
            await send(message)
            if message["type"] == "http.response.start":
                started = True

        async def publish(result):
            nonlocal attempted, sent, failure
            attempted = True
            content = ChatResponse(
                reply=result.get("reply", ""), session_id=self.session_id,
                trace_id=result.get("trace_id", ""), emotion=result.get("emotion"),
            )
            try:
                await JSONResponse(content.model_dump())(scope, receive, transport)
                sent = True
                return content.reply
            except Exception as exc:
                failure = exc
                return ""

        try:
            result = await self._generate(publish)
            if not attempted:
                # 只有不接受回执的第三方编排器才走此分支；本项目入口均接回调。
                await publish(result)
        except (TimeoutError, ConnectionError) as exc:
            if started:
                raise
            status = 504 if isinstance(exc, TimeoutError) else 502
            await JSONResponse({"detail": "LLM response unavailable"}, status_code=status)(scope, receive, send)
        if failure is not None:
            raise failure
        if attempted and not sent:
            logger.warning("HTTP 正文未获传输确认 session=%s", self.session_id)


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    _auth: bool = Security(verify_api_key_dep),
    # D13 同意门禁：未同意/旧版本/撤回 → 403 CONSENT_REQUIRED（四通道同源）
    user_id: int = Security(require_current_consent),
    db: AsyncSession = Depends(get_db),
    # PRIV-1：卡归属校验需要主体（role）；机器面（无 Bearer）→ None 不干预
    principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    orch = deps.orch
    if not orch:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not initialized",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    # ABUSE-4：每用户入站频控（与 WS 共桶）
    _chat_rate_guard(user_id)

    session_id = _owned_session(req.session_id, user_id)

    from api.byok import load_user_llm_config

    user_llm_config = await load_user_llm_config(user_id, orch, db)
    from llm_provider import select_request_llm

    try:
        select_request_llm(orch.components.get("llm"), user_id, user_llm_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    character_id = await _resolve_character_id(req.character_id, _as_principal(principal))

    async def generate(publish):
        return await orch.process_message(
            req.message, session_id, req.message_type, character_id,
            user_llm_config=user_llm_config, user_id=user_id, reply_sender=publish,
        )

    return _ChatDeliveryResponse(generate, session_id)


@router.post("/api/chat/stream")
async def chat_stream(
    req: ChatRequest,
    _auth: bool = Security(verify_api_key_dep),
    # D13 同意门禁（同 /api/chat）
    user_id: int = Security(require_current_consent),
    db: AsyncSession = Depends(get_db),
    # PRIV-1：同 /api/chat，卡归属校验需要主体
    principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    orch = deps.orch
    if not orch or not hasattr(orch, "process_message_stream"):
        raise HTTPException(
            status_code=503,
            detail="Stream not available",
            headers={"X-Error-Code": "FEATURE_UNAVAILABLE"},
        )

    # ABUSE-4：每用户入站频控（与 /api/chat、WS 共桶）
    _chat_rate_guard(user_id)

    session_id = _owned_session(req.session_id, user_id)

    from api.byok import load_user_llm_config

    user_llm_config = await load_user_llm_config(user_id, orch, db)
    from llm_provider import select_request_llm

    try:
        select_request_llm(orch.components.get("llm"), user_id, user_llm_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    character_id = await _resolve_character_id(req.character_id, _as_principal(principal))

    async def event_generator():
        stream_gen = orch.process_message_stream(
            req.message,
            session_id,
            req.message_type,
            character_id,
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
                    # 恢复迭代说明 ASGI send 已返回；不是浏览器已读回执。
                    ack = event.get("_ack")
                    if callable(ack):
                        ack()
                elif event.get("type") == "done":
                    yield f"data: {json.dumps({'done': True, 'session_id': session_id, 'reply': event.get('reply', ''), 'emotion': event.get('emotion'), 'process_time': event.get('process_time')}, ensure_ascii=False)}\n\n"
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
async def create_session(
    req: CreateSessionRequest,
    _auth: bool = Security(verify_api_key_dep),
    # D13 同意门禁（同 /api/chat）
    user_id: int = Security(require_current_consent),
):
    # 请求体旧 user_id 字段不再决定归属；浏览器只创建 web 会话。
    sessions = deps.sessions
    session_id = sessions.create_session(str(user_id), "web") if sessions else _owned_session("", user_id)
    return {"session_id": session_id}


@router.get("/api/sessions")
async def list_sessions(
    _auth: bool = Security(verify_api_key_dep),
    user_id: int = Security(get_current_user_id),
):
    sessions = deps.sessions
    owned = sessions.get_active_sessions(str(user_id)) if sessions else []
    return {"sessions": owned, "active_count": len(owned)}


@router.get("/api/chat/history")
async def chat_history(
    session_id: str = "",
    limit: int = Query(default=20, ge=1, le=100),
    before: int = Query(default=0, ge=0, description="旧UTC秒时间过滤；精确分页请用before_id"),
    before_id: int = Query(default=0, ge=0, description="上一页最小消息ID，严格小于该ID"),
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
                    _query_history_sync, sm, session_id, limit, before, before_id
                )
                return {"messages": messages, "session_id": session_id,
                        "next_before_id": messages[0]["id"] if messages else None}
            except Exception as e:
                logger.warning("Failed to query chat history by session_id: %s", e)
    # 定向查询失败不得回落全局工作记忆（会把其他会话内容冒充本会话）。
    return {"messages": [], "session_id": session_id, "next_before_id": None}


def _query_history_sync(sm, session_id: str, limit: int, before: int, before_id: int = 0) -> list[dict]:
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
        if before_id > 0:
            conditions.append("id < ?")
            params.append(before_id)
        elif before > 0:
            conditions.append("created_at < ?")
            params.append(
                datetime.fromtimestamp(before, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )
        where_clause = " AND ".join(conditions)
        params.append(str(limit))
        rows = conn.execute(
            f"SELECT id, role, content, emotion_tag, created_at, character_id, turn_id FROM chat_history "
            f"WHERE {where_clause} ORDER BY id DESC LIMIT ?",
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
    principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """状态：带 JWT 且为普通用户时返回**自己的**通道；admin 无参兼容返回遗留全局。

    禁止再向未登录/普通用户广播全局 bot 在线状态。
    P0 修复批 F6：主体解析改走 get_optional_principal（W1 单一主体，
    含 is_active + 撤销版本校验），旧 _try_user_id 只验签的旁路已删。
    """
    from wechat_direct.wechat_connector import get_wechat_state

    uid = principal.user_id if principal is not None else None
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
    principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    from wechat_direct.wechat_connector import get_wechat_state

    uid = principal.user_id if principal is not None else None
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


@router.get("/api/channels/wechat/status-stream")
async def wechat_status_stream(
    _auth: bool = Security(verify_api_key_dep),
    principal: AuthPrincipal | None = Depends(get_optional_principal),
):
    """SSE 实时推送**当前登录用户**的微信连接状态。

    P0 修复批 F6：主体解析改走 get_optional_principal（含 is_active + 撤销
    版本校验）；无有效主体直接 401，EventSource 无法带 Authorization 头
    即无法订阅（既有契约）。
    """
    from wechat_direct.wechat_connector import get_wechat_state

    uid = principal.user_id if principal is not None else None
    if uid is None:
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
