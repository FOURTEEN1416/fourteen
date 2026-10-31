"""聊天历史 per-user 归属（R6-1）：普通用户只能读本人会话，admin 保留全量。

被测：``GET /api/chat/history``（``api/routers/chat_routes.py``）。

归属只从认证主体派生：``user.id`` 必须等于会话键 owner 段（``utils.session_key``
为唯一真源）。客户端传入的 ``session_id`` 只是**待比对候选**，永不作为归属依据；
请求他人会话一律 404（不区分「不存在」与「无权限」，防状态码枚举）。一段式遗留
裸键无 owner 段 → 对普通用户 fail-closed（与 psych ``_owned_targets`` 边界同口径）。

全部数据落 tmp_path（users 库 + 结构化记忆库），不触真实 ``data/``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth import verify_api_key_dep
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db
from api.deps import deps
from api.routers import chat_routes
from shisi.memory.legacy.structured_memory import StructuredMemory

ADMIN_ID = 1
ALICE_ID = 2
BOB_ID = 3
CAROL_ID = 4

ALICE_WEB = f"{ALICE_ID}:web:alice001"
ALICE_WX = f"{ALICE_ID}:alice@im.wechat"
BOB_WEB = f"{BOB_ID}:web:bob001"
# 一段式遗留裸键（无 owner 段）：普通用户不可见，admin 可查
LEGACY_BARE = "legacy@im.wechat"

_ROWS = [
    (ALICE_WEB, "alice-web-1"),
    (ALICE_WEB, "alice-web-2"),
    (ALICE_WX, "alice-wx-1"),
    (BOB_WEB, "bob-web-1"),
    (BOB_WEB, "bob-web-2"),
    (LEGACY_BARE, "legacy-row-1"),
]


def _bearer(uid: int) -> dict[str, str]:
    """真实 access token：端点走 get_current_user 的完整主体链（验签 + 存在 + 版本）。"""
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


def _contents(resp) -> list[str]:
    return [m["content"] for m in resp.json().get("messages", [])]


@pytest.fixture
def history_scope(tmp_path):
    """四账号（admin/alice/bob/carol 无会话）+ 沙箱结构化记忆库（五种会话键）。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'users.db'}", echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, name, role in (
                (ADMIN_ID, "chat-admin", "admin"),
                (ALICE_ID, "chat-alice", "viewer"),
                (BOB_ID, "chat-bob", "viewer"),
                (CAROL_ID, "chat-carol", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=f"{name}@chat-history.test",
                        username=name,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=name,
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            await session.commit()

    asyncio.run(_init())

    sm = StructuredMemory(str(tmp_path / "memory.sqlite"))
    with sm.get_connection(write=True) as conn:
        for session_id, content in _ROWS:
            conn.execute(
                "INSERT INTO chat_history(role, content, session_id, user_key) "
                "VALUES (?, ?, ?, ?)",
                ("user", content, session_id, session_id),
            )
        conn.commit()

    orch = SimpleNamespace(_memory=SimpleNamespace(structured_memory=sm))
    saved_orch, saved_sessions = deps.orch, deps.sessions
    deps.orch = orch
    deps.sessions = None  # 归属判定不依赖进程内会话登记（重启后历史仍可读）

    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    app.dependency_overrides[get_db] = _get_db_override

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield SimpleNamespace(client=client, sm=sm)
    asyncio.run(client.aclose())
    deps.orch, deps.sessions = saved_orch, saved_sessions
    sm.close()
    asyncio.run(engine.dispose())


# ── 归属 isolation：本人可读（web / 微信两种键形态）─────────────────────


async def test_viewer_reads_own_web_session_history(history_scope):
    """本人 web 会话：200 且只返回该会话的行，不夹带他人内容。"""
    r = await history_scope.client.get(
        "/api/chat/history", params={"session_id": ALICE_WEB}, headers=_bearer(ALICE_ID)
    )

    assert r.status_code == 200, f"本人会话应 200，实得 {r.status_code}: {r.text}"
    assert _contents(r) == ["alice-web-1", "alice-web-2"], r.text
    assert "bob-web" not in r.text


async def test_viewer_reads_own_wechat_session_history(history_scope):
    """本人微信会话（``N:wxid`` 二段键）同样是本人可读路径。"""
    r = await history_scope.client.get(
        "/api/chat/history", params={"session_id": ALICE_WX}, headers=_bearer(ALICE_ID)
    )

    assert r.status_code == 200, f"本人微信会话应 200，实得 {r.status_code}: {r.text}"
    assert _contents(r) == ["alice-wx-1"], r.text


# ── 越权：他人会话 404（防枚举，不回显内容）──────────────────────────


@pytest.mark.parametrize(
    ("viewer_id", "other_session"),
    [
        (ALICE_ID, BOB_WEB),
        (BOB_ID, ALICE_WEB),
        (ALICE_ID, ALICE_WX.replace(str(ALICE_ID), str(BOB_ID), 1)),  # 伪造 owner 段
    ],
)
async def test_viewer_cannot_read_other_users_session_history(
    history_scope, viewer_id, other_session
):
    """请求他人会话：一律 404，且回执不得回显他人消息。"""
    r = await history_scope.client.get(
        "/api/chat/history",
        params={"session_id": other_session},
        headers=_bearer(viewer_id),
    )

    assert r.status_code == 404, (
        f"越权读取 {other_session!r} 应 404，实得 {r.status_code}: {r.text}"
    )
    assert r.json()["detail"] == "会话不存在", r.json()
    assert "bob-web" not in r.text and "alice-web" not in r.text


async def test_viewer_cannot_read_legacy_bare_session_history(history_scope):
    """一段式遗留裸键无 owner 段：普通用户 fail-closed 404（不猜归属）。"""
    r = await history_scope.client.get(
        "/api/chat/history", params={"session_id": LEGACY_BARE}, headers=_bearer(ALICE_ID)
    )

    assert r.status_code == 404, f"遗留裸键对普通用户应 404，实得 {r.status_code}"
    assert "legacy-row" not in r.text


# ── admin 保留：任意会话全量可读 ─────────────────────────────────────


async def test_admin_can_read_any_users_session_history(history_scope):
    """admin 不受 owner 段限制：本人/他人/遗留裸键都可查。"""
    headers = _bearer(ADMIN_ID)

    for session_id, expected in (
        (ALICE_WEB, ["alice-web-1", "alice-web-2"]),
        (BOB_WEB, ["bob-web-1", "bob-web-2"]),
        (LEGACY_BARE, ["legacy-row-1"]),
    ):
        r = await history_scope.client.get(
            "/api/chat/history", params={"session_id": session_id}, headers=headers
        )
        assert r.status_code == 200, (
            f"admin 查 {session_id!r} 应 200，实得 {r.status_code}: {r.text}"
        )
        assert _contents(r) == expected, f"{session_id!r}: {r.text}"


async def test_admin_history_without_session_id_returns_empty(history_scope):
    """admin 不带 session_id：保持既有语义 200 空列表（不是 422/403）。"""
    r = await history_scope.client.get("/api/chat/history", headers=_bearer(ADMIN_ID))

    assert r.status_code == 200, r.text
    assert r.json()["messages"] == [], r.json()


# ── 无会话 / 未登录边界 ─────────────────────────────────────────────


async def test_viewer_without_session_id_returns_empty(history_scope):
    """普通用户不带 session_id：200 空列表（既有语义），不猜测要读哪个会话。"""
    r = await history_scope.client.get("/api/chat/history", headers=_bearer(ALICE_ID))

    assert r.status_code == 200, r.text
    assert r.json()["messages"] == [], r.json()


async def test_viewer_without_sessions_gets_empty_own_session(history_scope):
    """无任何会话的用户：本人的空会话键 → 200 空列表（不是 404，也不是他人数据）。"""
    r = await history_scope.client.get(
        "/api/chat/history",
        params={"session_id": f"{CAROL_ID}:web:carol001"},
        headers=_bearer(CAROL_ID),
    )

    assert r.status_code == 200, f"本人空会话应 200，实得 {r.status_code}: {r.text}"
    assert r.json()["messages"] == [], r.json()


async def test_history_requires_bearer(history_scope):
    """无 Bearer：401（该面原本就要求登录，不因开放给普通用户退化成机器面）。"""
    r = await history_scope.client.get(
        "/api/chat/history", params={"session_id": ALICE_WEB}
    )

    assert r.status_code == 401, f"无 Bearer 应 401，实得 {r.status_code}: {r.text}"
