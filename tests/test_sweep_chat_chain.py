"""chat-chain 扫荡批红测（EXT-2 / PRIV-1 / ABUSE-4）— 红测先行。

三条 finding 共属对话主链的认证/授权/配额收口：

- EXT-2（P1）：WS 通道 JWT 认证只验签（verify_token：签名+exp+type），不做
  主体校验——已改密（token_version 已 bump）/已停用/已删除账号的旧 token 在
  HTTP 全站 401，却仍可经 ``ws://host/ws/?jwt=<旧token>`` 以受害者身份收发
  对话直至 token 自然过期。修法：api/auth_jwt 新增公共入口
  ``authenticate_access_token``（复用 _load_enabled_user + _assert_token_version，
  与 HTTP ``_resolve_principal`` 同源），WS 侧 ``_verify_jwt_subject`` 经它
  三段校验，失败 1008 关闭；role 进入 WS identity dict。

- PRIV-1（P1）：对话链 character_id 无归属校验——任意注册用户可用他人私人
  角色卡 id 直接对话并逐步套取私有卡内容（GET /api/characters 已按归属过滤，
  对话链完全绕过 W1 归属模型）。修法：汇聚点 ``_resolve_character_id`` 加
  principal 参数，复用 character_routes 归属真源（_load_character +
  card_owner_key + card_access_allowed）；两条红线：公共卡（owner 为空）照常
  放行（41 张模板卡是全站对话基座）、机器面（principal=None）不干预；
  校验失败与 require_character_access 同文案 404（防枚举）。

- ABUSE-4（P1）：主聊天链三入站（HTTP /api/chat·/stream、WS、微信入站）均无
  每用户频控/日配额，开放注册下每条消息直烧平台 LLM 凭证。修法：HTTP+WS 共桶
  ``_chat_rate_guard``（20 msg/min 滑窗 + 500 msg/天，进程内）；微信入站在
  ``_handle_message`` 最前加 per-owner 日配额快速失败（幂等认领语义零触碰）。

隔离纪律：users.db 逐用例独立 sqlite 引擎（tmp_path）且 teardown dispose；
角色卡目录钉临时目录；限速桶逐用例清零——零接触真实 data/ 与 config/characters/。
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("AI_GF_ENV", "dev")

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth_jwt import (
    AuthPrincipal,
    create_access_token,
    token_claims,
)
from api.database import Base, User, get_db
from api.main_routes import ChatRequest
from api.routers import chat_routes
from api.websocket_server import WebSocketServer

_PASSWORD = "not-used-here"  # 本文件只签 token，不走密码校验


# ═══════════════════════════════════════════════════════
# 夹具与工具
# ═══════════════════════════════════════════════════════


@pytest.fixture
async def sandbox_db(tmp_path, monkeypatch):
    """逐用例独立 sqlite 引擎 + 工厂；并把 api.database._async_session 钉到沙箱。

    WS 侧（authenticate 主体校验 / consent）直连 ``api.database._async_session``，
    不钉则写真实 data/users.db——与既有 test_attribution_context_contract 同纪律。
    """
    db_path = tmp_path / "chat_chain.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    import api.database as database

    monkeypatch.setattr(database, "_async_session", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def sandbox_characters(tmp_path, monkeypatch):
    """角色卡目录钉临时目录（真实 config/characters 是 gitignore 的 41 张卡，禁触碰）。"""
    from api.routers import character_routes

    sandbox = tmp_path / "characters"
    sandbox.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", sandbox)
    return sandbox


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """限速桶逐用例前后清零（红测期桶尚不存在 → getattr 跳过；绿测期防跨用例污染）。"""
    from api.routers import chat_routes as cr

    def _clear() -> None:
        for name in ("_chat_minute_limiter", "_chat_daily_limiter"):
            box = getattr(cr, name, None)
            if box is not None and hasattr(box, "reset"):
                box.reset()

    _clear()
    yield
    _clear()


async def _aseed_user(factory, uid: int, email: str, *, active: bool = True, tv: int = 0) -> None:
    async with factory() as db:
        db.add(User(
            id=uid, email=email, username=email.split("@")[0],
            hashed_password=_PASSWORD, display_name=email.split("@")[0],
            role="viewer", is_active=active, is_verified=True, token_version=tv,
        ))
        await db.commit()


def _write_card(directory, cid: str, owner: str) -> None:
    """写一张卡；owner 传 "default"/"" = 公共模板卡，传用户 id 字符串 = 私人实例。"""
    (directory / f"{cid}.json").write_text(
        json.dumps({"id": cid, "user_id": owner, "name": f"card-{cid}",
                    "description": "私人内容描述", "personality": {}}),
        encoding="utf-8",
    )


def _principal(uid: int, role: str = "viewer") -> AuthPrincipal:
    """构造真实 AuthPrincipal（card_access_allowed 只读 .role/.user_id）。"""
    return AuthPrincipal(user_id=uid, role=role, token_version=0,
                         user=SimpleNamespace(role=role))


def _make_server(orch=None) -> WebSocketServer:
    import os as _os

    prev = _os.environ.get("API_KEY_ENABLED")
    _os.environ["API_KEY_ENABLED"] = "false"
    try:
        return WebSocketServer(orch if orch is not None else SimpleNamespace(components={}))
    finally:
        if prev is None:
            _os.environ.pop("API_KEY_ENABLED", None)
        else:
            _os.environ["API_KEY_ENABLED"] = prev


class FakeSocket:
    """最小 WS 替身：URL path + 帧序列 + close 记录（与既有 WS 契约测试同型）。"""

    def __init__(self, path: str = "/", frames: list[dict] | None = None):
        self.request = SimpleNamespace(path=path)
        self._frames = frames or []
        self.messages: list[dict] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        # websockets 库契约：已关闭连接的再次 close 是 no-op（handler finally
        # 会兜底再 close 一次，不得覆盖业务语义的 close code）
        if self.close_code is not None:
            return
        self.close_code = code
        self.close_reason = reason

    async def __aiter__(self):
        for frame in self._frames:
            yield json.dumps(frame)


async def _signed_token(factory, uid: int) -> str:
    async with factory() as db:
        user = await db.get(User, uid)
        return create_access_token(token_claims(user))


# ═══════════════════════════════════════════════════════
# EXT-2 — WS JWT 通道主体校验（存在 + is_active + 撤销版本）
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ext2_revoked_token_rejected_by_authenticate_access_token(sandbox_db):
    """token_version bump（改密/管理员重置）后，旧 token 在公共入口被 401。

    HTTP 侧 verify_api_key_dep 已有同款校验（P0-F1）；本入口是 WS 侧的同源真源。
    """
    from api.auth_jwt import authenticate_access_token, bump_token_version

    factory = sandbox_db
    await _aseed_user(factory, 1, "victim@chat.test", tv=0)
    token = await _signed_token(factory, 1)
    async with factory() as db:
        user = await db.get(User, 1)
        bump_token_version(user)
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        async with factory() as db:
            await authenticate_access_token(db, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_ext2_disabled_account_rejected_by_authenticate_access_token(sandbox_db):
    """停用账号（is_active=False）的 token 即使验签通过也被 401。"""
    from api.auth_jwt import authenticate_access_token

    factory = sandbox_db
    await _aseed_user(factory, 2, "disabled@chat.test", active=False)
    token = await _signed_token(factory, 2)

    with pytest.raises(HTTPException) as exc:
        async with factory() as db:
            await authenticate_access_token(db, token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_ext2_valid_token_yields_principal_with_role(sandbox_db):
    """语义不变量：有效启用用户的 token 放行，且 WS identity 携带库内现值 role。"""
    factory = sandbox_db
    await _aseed_user(factory, 3, "alive@chat.test")
    token = await _signed_token(factory, 3)

    from api.auth_jwt import authenticate_access_token

    async with factory() as db:
        principal = await authenticate_access_token(db, token)
    assert principal.user_id == 3
    assert principal.role == "viewer"

    server = _make_server()
    identity = await server._verify_jwt_subject({"user_id": 3, "method": "jwt"}, token)
    assert identity is not None
    assert identity["user_id"] == 3
    assert identity["method"] == "jwt"
    assert identity["role"] == "viewer"


@pytest.mark.asyncio
async def test_ext2_revoked_token_ws_connection_closed_1008(sandbox_db, monkeypatch):
    """攻击面实证：受害者改密后，攻击者持旧 token 连 WS 必须被 1008 拒绝。

    修复前：_authenticate 只验签 → 连接成立，进入消息循环（撤销语义在 WS 面失效）。
    """
    factory = sandbox_db
    await _aseed_user(factory, 1, "victim2@chat.test", tv=0)
    token = await _signed_token(factory, 1)
    from api.auth_jwt import bump_token_version

    async with factory() as db:
        user = await db.get(User, 1)
        bump_token_version(user)
        await db.commit()

    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    server = WebSocketServer(orch)
    socket = FakeSocket(
        path=f"/?jwt={token}",
        frames=[{"type": "chat", "message": "以受害者身份说话", "session_id": ""}],
    )
    await server._handler(socket)
    assert socket.close_code == 1008, (
        f"被撤销 token 的 WS 连接未按 1008 关闭（close={socket.close_code}），"
        f"收到的帧: {socket.messages}"
    )
    orch.process_message.assert_not_called()


@pytest.mark.asyncio
async def test_ext2_valid_token_ws_still_works(sandbox_db, monkeypatch):
    """语义不变量：有效 token 的 WS 对话不被误伤（reply 帧照常返回）。"""
    factory = sandbox_db
    await _aseed_user(factory, 4, "alive2@chat.test")
    token = await _signed_token(factory, 4)

    async def _reply(*args, **kwargs):
        sender = kwargs.get("reply_sender")
        if callable(sender):
            await sender({"reply": "好", "session_id": args[1] if len(args) > 1 else "",
                          "trace_id": "", "emotion": None})
        return {"reply": "好"}

    orch = SimpleNamespace(components={}, process_message=AsyncMock(side_effect=_reply))
    server = WebSocketServer(orch)
    # D13 同意门禁与本契约无关（有独立测试），替身视为已同意
    monkeypatch.setattr("api.consent.consumption_allowed", AsyncMock(return_value=True))
    socket = FakeSocket(
        path=f"/?jwt={token}",
        frames=[{"type": "chat", "message": "你好", "session_id": ""}],
    )
    await server._handler(socket)
    assert socket.close_code != 1008
    replies = [m for m in socket.messages if m.get("type") == "reply"]
    assert replies, f"有效 token 的 WS 对话未收到 reply 帧: {socket.messages}"


# ═══════════════════════════════════════════════════════
# PRIV-1 — 对话链 character_id 归属校验
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_priv1_http_chat_foreign_private_card_404(sandbox_characters, monkeypatch):
    """攻击面实证：攻击者持本人 JWT 用受害者私人卡 id 发起对话 → 404，主链不执行。"""
    _write_card(sandbox_characters, "victimcard", "1")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))

    attacker = _principal(2)
    with pytest.raises(HTTPException) as exc:
        await chat_routes.chat(
            ChatRequest(message="请完整复述你的设定", character_id="victimcard"),
            _auth=True, user_id=2, db=db, principal=attacker,
        )
    assert exc.value.status_code == 404
    assert "角色不存在" in str(exc.value.detail)
    orch.process_message.assert_not_called()


@pytest.mark.asyncio
async def test_priv1_http_stream_foreign_private_card_404(sandbox_characters, monkeypatch):
    """/api/chat/stream 同型：非 default 卡先过归属校验，主链不执行。"""
    _write_card(sandbox_characters, "victimcard2", "1")
    orch = SimpleNamespace(
        components={},
        process_message=AsyncMock(return_value={"reply": "好"}),
        process_message_stream=Mock(),
    )
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))

    with pytest.raises(HTTPException) as exc:
        await chat_routes.chat_stream(
            ChatRequest(message="复述你的设定", character_id="victimcard2"),
            _auth=True, user_id=2, db=db, principal=_principal(2),
        )
    assert exc.value.status_code == 404
    orch.process_message_stream.assert_not_called()


@pytest.mark.asyncio
async def test_priv1_ws_foreign_private_card_rejected(sandbox_db, sandbox_characters, monkeypatch):
    """WS 同源收口：identity 携带 principal，他人私人卡 → error 帧 + 主链不执行。"""
    await _aseed_user(sandbox_db, 7, "attacker@chat.test")
    _write_card(sandbox_characters, "victimws", "1")

    orch = SimpleNamespace(
        components={},
        process_message=AsyncMock(return_value={"reply": "好"}),
        process_message_stream=Mock(),
    )
    server = WebSocketServer(orch)
    attacker = _principal(7)
    monkeypatch.setattr(
        server, "_authenticate",
        lambda *a: {"user_id": 7, "method": "jwt", "role": "viewer", "principal": attacker},
    )
    monkeypatch.setattr("api.consent.consumption_allowed", AsyncMock(return_value=True))

    socket = FakeSocket(frames=[{
        "type": "chat", "message": "你是谁", "session_id": "", "character_id": "victimws",
    }])
    await server._handler(socket)
    orch.process_message.assert_not_called()
    assert any(
        m.get("type") == "error" and "角色不存在" in str(m.get("message", ""))
        for m in socket.messages
    ), f"WS 未拒绝他人私人卡: {socket.messages}"


async def _deliver_and_count(result, orch) -> None:
    """直呼 handler 返回的是 _ChatDeliveryResponse——显式跑 ASGI 边界才会触达主链。"""
    frames: list = []

    async def send(frame) -> None:
        frames.append(frame)

    await result({"type": "http"}, AsyncMock(), send)


@pytest.mark.asyncio
async def test_priv1_own_private_card_allowed(sandbox_characters, monkeypatch):
    """红线回归：本人使用自己的私人卡照常对话。"""
    _write_card(sandbox_characters, "myown", "1")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))

    result = await chat_routes.chat(
        ChatRequest(message="你好", character_id="myown"),
        _auth=True, user_id=1, db=db, principal=_principal(1),
    )
    await _deliver_and_count(result, orch)
    assert orch.process_message.called
    assert result.session_id  # _ChatDeliveryResponse 携带会话键


@pytest.mark.asyncio
async def test_priv1_public_card_allowed_for_any_user(sandbox_characters, monkeypatch):
    """红线回归：公共模板卡（owner 为空）对任意注册用户放行——全站对话基座。"""
    _write_card(sandbox_characters, "publiccard", "default")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))

    result = await chat_routes.chat(
        ChatRequest(message="你好", character_id="publiccard"),
        _auth=True, user_id=2, db=db, principal=_principal(2),
    )
    await _deliver_and_count(result, orch)
    assert orch.process_message.called
    assert result.session_id


@pytest.mark.asyncio
async def test_priv1_machine_face_unrestricted(sandbox_characters, monkeypatch):
    """红线回归：机器面（principal=None，API Key/部署脚本/E2E）不做归属收窄。

    直呼 handler（既有单测形态）拿到的是 Depends 哨兵 → 按机器面解释
    （与 character_routes/mimo_voice_routes 同口径）。
    """
    _write_card(sandbox_characters, "someones", "1")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))

    result = await chat_routes.chat(
        ChatRequest(message="你好", character_id="someones"),
        _auth=True, user_id=999, db=db,
    )
    await _deliver_and_count(result, orch)
    assert orch.process_message.called
    assert result.session_id


@pytest.mark.asyncio
async def test_priv1_admin_can_chat_with_any_private_card(sandbox_characters, monkeypatch):
    """管理员面：admin 主体可对话任意卡（与 card_access_allowed 真源一致）。"""
    _write_card(sandbox_characters, "usercard", "1")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="admin")))

    result = await chat_routes.chat(
        ChatRequest(message="你好", character_id="usercard"),
        _auth=True, user_id=10, db=db, principal=_principal(10, role="admin"),
    )
    await _deliver_and_count(result, orch)
    assert orch.process_message.called
    assert result.session_id


# ═══════════════════════════════════════════════════════
# ABUSE-4 — 主聊天链入站频控（HTTP+WS 共桶 20/min + 500/天；微信 per-owner 日配额）
# ═══════════════════════════════════════════════════════


def _bare_chat_app(factory, monkeypatch, orch):
    """最小 app：只挂 chat 路由 + 沙箱 DB/同意替身（与既有集成测同纪律）。"""
    from fastapi import FastAPI

    from api.auth import verify_api_key_dep
    from api.consent import require_current_consent

    app = FastAPI()
    app.include_router(chat_routes.router)

    async def _db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_current_consent] = lambda: 1
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    return app


@pytest.mark.asyncio
async def test_abuse4_http_chat_429_after_20_per_minute(sandbox_db, sandbox_characters, monkeypatch):
    """攻击面实证：第 21 条/分钟 → 429；前 20 条不误伤。"""
    await _aseed_user(sandbox_db, 1, "flood@chat.test")
    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}))
    app = _bare_chat_app(sandbox_db, monkeypatch, orch)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    for i in range(20):
        r = await client.post("/api/chat", json={"message": f"m{i}"})
        assert r.status_code == 200, f"第 {i + 1} 条不应被限速（正常对话节奏不得误伤）: {r.status_code}"
    r21 = await client.post("/api/chat", json={"message": "m21"})
    assert r21.status_code == 429, f"第 21 条未被限速: {r21.status_code}"
    await client.aclose()


@pytest.mark.asyncio
async def test_abuse4_http_stream_429_after_20_per_minute(sandbox_db, sandbox_characters, monkeypatch):
    """/api/chat/stream 同桶同限：第 21 条 → 429。"""
    await _aseed_user(sandbox_db, 1, "flood2@chat.test")

    async def _events(*a, **kw):
        yield {"type": "done", "reply": "好"}

    orch = SimpleNamespace(
        components={},
        process_message=AsyncMock(return_value={"reply": "好"}),
        process_message_stream=Mock(side_effect=_events),
    )
    app = _bare_chat_app(sandbox_db, monkeypatch, orch)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    for i in range(20):
        r = await client.post("/api/chat/stream", json={"message": f"s{i}"})
        assert r.status_code == 200, f"stream 第 {i + 1} 条不应被限速: {r.status_code}"
    r21 = await client.post("/api/chat/stream", json={"message": "s21"})
    assert r21.status_code == 429, f"stream 第 21 条未被限速: {r21.status_code}"
    await client.aclose()


def test_abuse4_minute_limiter_unit_with_fake_clock():
    """滑窗限速器单元契约：窗口内记账、窗口滚动释放、含失败请求。"""
    tick = [0.0]

    def clock() -> float:
        return tick[0]

    limiter = chat_routes._SlidingWindowLimiter(2, 60.0, clock=clock)
    assert limiter.check("u1") and limiter.check("u1")
    assert not limiter.check("u1"), "窗口内第 3 条必须拒绝"
    tick[0] = 61.0
    assert limiter.check("u1"), "窗口滚动后必须重新放行"
    assert limiter.check("u2"), "不同主体独立计桶"


def test_abuse4_daily_quota_unit_with_fake_day():
    """日配额单元契约：当日累计、跨本地日界重置。"""
    day = ["2026-10-09"]
    limiter = chat_routes._DailyQuotaLimiter(3, day_provider=lambda: day[0])
    assert limiter.check("u1") and limiter.check("u1") and limiter.check("u1")
    assert not limiter.check("u1"), "当日第 4 条必须拒绝"
    day[0] = "2026-10-10"
    assert limiter.check("u1"), "跨日界必须重置配额"


def test_abuse4_defaults_pinned():
    """钉住默认量级：20 msg/min + 500 msg/天（测试契约，改动须显式过本用例）。"""
    assert chat_routes._CHAT_MSGS_PER_MINUTE == 20
    assert chat_routes._CHAT_MSGS_PER_DAY == 500


@pytest.mark.asyncio
async def test_abuse4_ws_chat_rate_limited_1013(sandbox_db, sandbox_characters, monkeypatch):
    """WS recv 循环按 user 计数：第 21 条 → RATE_LIMITED 错误帧 + 1013 关闭。"""
    await _aseed_user(sandbox_db, 1, "wsflood@chat.test")
    orch = SimpleNamespace(
        components={},
        process_message=AsyncMock(return_value={"reply": "好"}),
        process_message_stream=Mock(),
    )
    server = WebSocketServer(orch)
    alice = _principal(1)
    monkeypatch.setattr(
        server, "_authenticate",
        lambda *a: {"user_id": 1, "method": "jwt", "role": "viewer", "principal": alice},
    )
    monkeypatch.setattr("api.consent.consumption_allowed", AsyncMock(return_value=True))

    frames = [{"type": "chat", "message": f"w{i}", "session_id": ""} for i in range(25)]
    socket = FakeSocket(frames=frames)
    await server._handler(socket)
    assert socket.close_code == 1013, (
        f"WS 未按 1013 关闭限速连接（close={socket.close_code}）"
    )
    assert any(m.get("error") == "RATE_LIMITED" for m in socket.messages), socket.messages
    assert orch.process_message.call_count == 20, (
        f"放行条数应为 20，实际 {orch.process_message.call_count}"
    )


@pytest.mark.asyncio
async def test_abuse4_wechat_inbound_daily_quota_fast_fail(tmp_path, monkeypatch):
    """微信入站 per-owner 日配额：超限消息在幂等认领/串行处理之前快速失败。"""
    from wechat_direct import wechat_connector as wc

    assert wc._INBOUND_DAILY_LIMIT >= 500, "入站日配额默认量级必须 ≥500（真人远低于此）"

    conn = wc.WeChatConnector(
        SimpleNamespace(_orch=None),
        owner_user_id=42,
        session_dir=tmp_path / "sess",
        credentials_path=str(tmp_path / "c.json"),
        state_path=str(tmp_path / "s.json"),
        qrcode_path=str(tmp_path / "q.json"),
        context_tokens_path=str(tmp_path / "ct.json"),
    )
    conn._inbound_daily_limit = 3  # 测试契约：实例级可调
    serial = Mock()
    conn._handle_message_serial = serial

    def msg(i: int) -> dict:
        return {
            "message_type": 1,
            "message_id": str(i),
            "from_user_id": "peer1",
            "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
        }

    for i in range(3):
        conn._handle_message(msg(i))
    assert serial.call_count == 3
    conn._handle_message(msg(999))
    assert serial.call_count == 3, "超配额消息必须在串行处理之前快速失败"


@pytest.mark.asyncio
async def test_abuse4_wechat_legacy_ownerless_channel_not_blocked(tmp_path):
    """遗留全局通道（owner_user_id=None）无法归属账号 → 不做配额（保持既有行为）。"""
    from wechat_direct import wechat_connector as wc

    conn = wc.WeChatConnector(
        SimpleNamespace(_orch=None),
        session_dir=tmp_path / "sess",
        credentials_path=str(tmp_path / "c.json"),
        state_path=str(tmp_path / "s.json"),
        qrcode_path=str(tmp_path / "q.json"),
        context_tokens_path=str(tmp_path / "ct.json"),
    )
    serial = Mock()
    conn._handle_message_serial = serial
    conn._handle_message({
        "message_type": 1, "message_id": "a1", "from_user_id": "peer1",
        "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
    })
    assert serial.call_count == 1
