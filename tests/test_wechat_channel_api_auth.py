"""每人独立微信通道 API — JWT 隔离与权限测试。"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-wx-channel-isolation-32chars-ok")


@pytest.fixture()
def client(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from api.auth_jwt import create_access_token
    from api.database import Base, User, get_db
    from api.routers.wechat_channel_routes import admin_router, router

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id=1, email="admin@t.com", username="admin", hashed_password="x", role="admin"))
            s.add(User(id=2, email="u2@t.com", username="u2", hashed_password="x", role="viewer"))
            await s.commit()

    import asyncio

    asyncio.get_event_loop_policy()
    asyncio.run(_init())

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.dependency_overrides[get_db] = _get_db

    class _GF:
        async def upsert_binding(self, *a, **k):
            return None

    from api.deps import deps

    deps.gf = _GF()

    token_admin = create_access_token({"sub": "1", "type": "access"})
    token_user = create_access_token({"sub": "2", "type": "access"})
    c = TestClient(app)
    return c, token_admin, token_user


def test_channel_status_is_per_user_not_global(client):
    c, _ta, tu = client
    r = c.get("/api/wechat/channel", headers={"Authorization": f"Bearer {tu}"})
    assert r.status_code == 200
    data = r.json()
    assert data["owner_user_id"] == 2
    assert data["connected"] is False
    # 不得泄露他人 bot
    assert not data.get("bot_id")


def test_channel_requires_jwt(client):
    c, _ta, _tu = client
    r = c.get("/api/wechat/channel")
    assert r.status_code == 401


def test_admin_channels_list_no_token_field(client):
    c, ta, _tu = client
    r = c.get("/api/admin/wechat/channels", headers={"Authorization": f"Bearer {ta}"})
    assert r.status_code == 200
    body = r.json()
    assert "channels" in body
    for item in body.get("channels", []):
        assert "token" not in item


def test_admin_route_forbidden_for_viewer(client):
    c, _ta, tu = client
    r = c.get("/api/admin/wechat/channels", headers={"Authorization": f"Bearer {tu}"})
    assert r.status_code == 403
