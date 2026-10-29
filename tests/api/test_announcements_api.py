"""网站公告 API（V2-5，2026-10-29）— 行为契约测试。

- GET  /api/announcements/active：时间窗内 enabled 行 + 本人已读标记（未读在前）
- POST /api/announcements/{id}/read：幂等落库（唯一约束）
- /api/admin/announcements CRUD：admin 门（viewer 403）+ 操作员审计

数据全部合成：tmp users.db（Base.metadata.create_all——announcements 两张
纯新增表由 create_all 幂等创建，无 ALTER 迁移）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-announcements-32chars-ok!!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db

_ADMIN_ID = 1
_VIEWER_ID = 2


def _build_app(tmp_path):
    db_path = str(tmp_path / "announcements_api.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, email, role in (
                (_ADMIN_ID, "admin@ann.test", "admin"),
                (_VIEWER_ID, "viewer@ann.test", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=email.split("@")[0],
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine


def _bearer(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def api(tmp_path):
    app, client, engine = _build_app(tmp_path)
    yield client, engine
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


_CREATE = {
    "type": "banner",
    "title": "欢迎使用",
    "content_md": "先看使用说明，遇到问题联系 QQ 2053769154",
    "link": "/intro",
    "link_label": "查看使用说明",
    "dismissible": True,
    "starts_at": None,
    "ends_at": None,
    "enabled": True,
}


async def test_active_empty_then_visible_after_create(api):
    client, _engine = api
    r0 = await client.get("/api/announcements/active", headers=_bearer(_VIEWER_ID))
    assert r0.status_code == 200
    assert r0.json()["announcements"] == []

    r1 = await client.post("/api/admin/announcements", json=_CREATE, headers=_bearer(_ADMIN_ID))
    assert r1.status_code == 200, r1.text
    ann_id = r1.json()["id"]

    r2 = await client.get("/api/announcements/active", headers=_bearer(_VIEWER_ID))
    items = r2.json()["announcements"]
    assert len(items) == 1
    assert items[0]["id"] == ann_id
    assert items[0]["read"] is False
    assert items[0]["link"] == "/intro"


async def test_mark_read_idempotent_and_flagged(api):
    client, _engine = api
    r1 = await client.post("/api/admin/announcements", json=_CREATE, headers=_bearer(_ADMIN_ID))
    ann_id = r1.json()["id"]

    r2 = await client.post(f"/api/announcements/{ann_id}/read", headers=_bearer(_VIEWER_ID))
    assert r2.status_code == 200
    # 幂等：重复标记不炸
    r3 = await client.post(f"/api/announcements/{ann_id}/read", headers=_bearer(_VIEWER_ID))
    assert r3.status_code == 200

    r4 = await client.get("/api/announcements/active", headers=_bearer(_VIEWER_ID))
    assert r4.json()["announcements"][0]["read"] is True


async def test_expired_announcement_not_in_active(api):
    client, _engine = api
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r1 = await client.post(
        "/api/admin/announcements",
        json={**_CREATE, "ends_at": past},
        headers=_bearer(_ADMIN_ID),
    )
    assert r1.status_code == 200

    r2 = await client.get("/api/announcements/active", headers=_bearer(_VIEWER_ID))
    assert r2.json()["announcements"] == []


async def test_viewer_cannot_manage_announcements(api):
    client, _engine = api
    r = await client.get("/api/admin/announcements", headers=_bearer(_VIEWER_ID))
    assert r.status_code == 403
    r2 = await client.post("/api/admin/announcements", json=_CREATE, headers=_bearer(_VIEWER_ID))
    assert r2.status_code == 403


async def test_admin_update_and_delete(api):
    client, _engine = api
    r1 = await client.post("/api/admin/announcements", json=_CREATE, headers=_bearer(_ADMIN_ID))
    ann_id = r1.json()["id"]

    r2 = await client.put(
        f"/api/admin/announcements/{ann_id}",
        json={**_CREATE, "enabled": False},
        headers=_bearer(_ADMIN_ID),
    )
    assert r2.status_code == 200
    assert r2.json()["enabled"] is False

    # 停用后 active 消失
    r3 = await client.get("/api/announcements/active", headers=_bearer(_VIEWER_ID))
    assert r3.json()["announcements"] == []

    r4 = await client.delete(f"/api/admin/announcements/{ann_id}", headers=_bearer(_ADMIN_ID))
    assert r4.status_code == 200
    r5 = await client.get("/api/admin/announcements", headers=_bearer(_ADMIN_ID))
    assert r5.json()["announcements"] == []
