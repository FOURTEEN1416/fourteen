"""「我 × 这个角色」设置 API 回归（2026-11-07 痛点批 P1）。

锁定契约：
- 普通用户**自己**读写自己的角色设置（公共池卡也可），无需管理员；
- `user_key` 只从 token + 绑定派生，未绑定 → 409（不猜键）；
- 越界/未知键 → 400（不回执假成功）；
- 两个用户的同名角色互不影响（两维隔离）；
- `DELETE` 复位后按出厂默认重新物化。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-character-settings-32ch!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, WechatBinding, get_db

_ALICE = 2
_BOB = 3


def _public_card(card_id: str, name: str) -> dict:
    return {
        "id": card_id,
        "name": name,
        "user_id": "default",
        "personality": {},
        "speaking_style": {},
        "core_anchors": [],
        "is_active": False,
    }


def _build_app(tmp_path):
    db_path = str(tmp_path / "settings.db")
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
            for uid, email, username, role in (
                (1, "admin@d.test", "admin", "admin"),
                (_ALICE, "alice@d.test", "alice", "viewer"),
                (_BOB, "bob@d.test", "bob", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=username,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=username,
                        role=role,
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
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine, session_factory


@pytest.fixture
def d_app(tmp_path, monkeypatch):
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    (chars_dir / "pub01.json").write_text(
        json.dumps(_public_card("pub01", "公共卡")), encoding="utf-8"
    )
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    client, engine, session_factory = _build_app(tmp_path)
    yield client, engine, session_factory, chars_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _login(client: AsyncClient, name: str) -> str:
    resp = await client.post("/api/auth/login", json={"login": name, "password": "Passw0rd!123"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_materializes_full_snapshot(d_app):
    client, *_ = d_app

    async def _run():
        token = await _login(client, "alice")
        resp = await client.get("/api/characters/pub01/my-settings", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scope"] == "user_character"
        assert body["config"]["per_char_ms"] == 45
        assert "proactive" in body["groups"] and "llm" in body["groups"]
        await client.aclose()

    asyncio.run(_run())


def test_put_persists_and_isolated_between_users(d_app):
    client, *_ = d_app

    async def _run():
        alice = await _login(client, "alice")
        bob = await _login(client, "bob")

        put = await client.put(
            "/api/characters/pub01/my-settings",
            json={"config": {"per_char_ms": 88, "max_daily_messages": 3}},
            headers=_auth(alice),
        )
        assert put.status_code == 200, put.text
        assert put.json()["config"]["per_char_ms"] == 88

        got_alice = await client.get("/api/characters/pub01/my-settings", headers=_auth(alice))
        got_bob = await client.get("/api/characters/pub01/my-settings", headers=_auth(bob))
        assert got_alice.json()["config"]["per_char_ms"] == 88
        assert got_bob.json()["config"]["per_char_ms"] == 45  # 另一个用户不受影响
        await client.aclose()

    asyncio.run(_run())


def test_put_rejects_invalid_value(d_app):
    client, *_ = d_app

    async def _run():
        alice = await _login(client, "alice")
        resp = await client.put(
            "/api/characters/pub01/my-settings",
            json={"config": {"per_char_ms": 9999}},
            headers=_auth(alice),
        )
        assert resp.status_code == 400, resp.text
        assert "越界" in resp.json()["detail"] or "per_char_ms" in resp.json()["detail"]
        await client.aclose()

    asyncio.run(_run())


def test_delete_resets_to_defaults(d_app):
    client, *_ = d_app

    async def _run():
        alice = await _login(client, "alice")
        await client.put(
            "/api/characters/pub01/my-settings",
            json={"config": {"per_char_ms": 99}},
            headers=_auth(alice),
        )
        dele = await client.delete("/api/characters/pub01/my-settings", headers=_auth(alice))
        assert dele.status_code == 200 and dele.json()["reset"] is True

        got = await client.get("/api/characters/pub01/my-settings", headers=_auth(alice))
        assert got.json()["config"]["per_char_ms"] == 45
        await client.aclose()

    asyncio.run(_run())


def test_unbound_user_gets_409(d_app):
    """未绑定微信 → 409（不猜键）。admin 也不例外：没有代设入口。"""
    client, engine, session_factory, _ = d_app

    async def _run():
        # 新建一个未绑定的用户
        async with session_factory() as session:
            session.add(
                User(
                    id=9,
                    email="carol@d.test",
                    username="carol",
                    hashed_password=hash_password("Passw0rd!123"),
                    display_name="carol",
                    role="viewer",
                    is_active=True,
                    is_verified=True,
                )
            )
            await session.commit()

        token = await _login(client, "carol")
        resp = await client.get("/api/characters/pub01/my-settings", headers=_auth(token))
        assert resp.status_code == 409, resp.text
        await client.aclose()

    asyncio.run(_run())
