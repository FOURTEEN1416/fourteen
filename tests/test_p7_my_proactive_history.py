"""P7 本人主动消息历史回归（2026-11-07）。

用户裁决：「最近主动消息我觉得可以放到状态页」。本文件锁两件事：
1. 普通用户只能看到**自己会话**的出站事实（admin 全量读面照旧 admin-only）；
2. 未绑定微信 → `bound=False` 且空列表（不猜键）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-p7-history-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, WechatBinding, get_db
from proactive import runtime_plane

_ALICE = 2
_BOB = 3


def _build_app(tmp_path):
    db_path = str(tmp_path / "p7.db")
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
            for uid, email, username in (
                (_ALICE, "alice@d.test", "alice"),
                (_BOB, "bob@d.test", "bob"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=username,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=username,
                        role="viewer",
                        is_active=True,
                        is_verified=True,
                    )
                )
            session.add(WechatBinding(user_id=_ALICE, wxid="alice_wx", character_card_id="default"))
            session.add(WechatBinding(user_id=_BOB, wxid="bob_wx", character_card_id="default"))
            await session.commit()

    asyncio.run(_init())
    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


@pytest.fixture
def d_app(tmp_path, monkeypatch):
    # 运行面 DB 重定向沙箱（conftest 全局夹具只覆盖 autouse 场景；本文件自建 app，
    # 需要显式重定向，防止测试行写进开发机真实 runtime_plane.db）
    monkeypatch.setattr(
        runtime_plane, "set_db_path", runtime_plane.set_db_path, raising=False
    )
    runtime_plane.set_db_path(tmp_path / "runtime_plane.db")

    client, engine = _build_app(tmp_path)
    yield client, engine
    asyncio.run(engine.dispose())
    configure_auth(False, "")


def _seed_send(session_key: str, character_id: str, message: str) -> None:
    runtime_plane.enqueue_send(
        kind="proactive",
        channel="wechat",
        session_key=session_key,
        character_id=character_id,
        message=message,
    )


async def _login(client: AsyncClient, name: str) -> str:
    resp = await client.post("/api/auth/login", json={"login": name, "password": "Passw0rd!123"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_user_sees_only_own_sends(d_app):
    client, _engine = d_app
    _seed_send("2:alice_wx", "ch_a", "给 alice 的消息")
    _seed_send("3:bob_wx", "ch_b", "给 bob 的消息")
    time.sleep(0.01)

    async def _run():
        alice = await _login(client, "alice")
        r = await client.get("/api/user/proactive/history", headers=_auth(alice))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["bound"] is True
        messages = [h["message"] for h in body["history"]]
        assert "给 alice 的消息" in messages
        assert all("给 bob 的消息" not in m for m in messages), "绝不能看到别人的主动消息"
        await client.aclose()

    asyncio.run(_run())


def test_character_filter_scopes_to_role(d_app):
    client, _engine = d_app
    _seed_send("2:alice_wx", "ch_a", "角色 A 的主动消息")
    _seed_send("2:alice_wx", "ch_b", "角色 B 的主动消息")

    async def _run():
        alice = await _login(client, "alice")
        r = await client.get(
            "/api/user/proactive/history", params={"character_id": "ch_a"}, headers=_auth(alice)
        )
        assert r.status_code == 200
        messages = [h["message"] for h in r.json()["history"]]
        assert messages == ["角色 A 的主动消息"]
        await client.aclose()

    asyncio.run(_run())


def test_unbound_user_gets_empty_with_bound_false(d_app):
    client, _engine = d_app

    async def _run():
        resp = await client.post(
            "/api/auth/register",
            json={"email": "carol@d.test", "username": "carol", "password": "Passw0rd!123"},
        )
        assert resp.status_code in (200, 201), resp.text
        token = str(resp.json()["access_token"])
        r = await client.get("/api/user/proactive/history", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body["history"] == []
        assert body["bound"] is False, "未绑定必须如实返回 bound=False，不猜键"
        await client.aclose()

    asyncio.run(_run())
