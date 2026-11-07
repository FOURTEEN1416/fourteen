"""公开角色卡「每人一份」私人覆盖回归（2026-11-07 用户裁决）。

用户原话：「直接就是默认每一个人都可以拥有所有的公开模版，并且能够对该模型
进行自定义修改，不然太复杂而且冗余了，还会多出一个页面来」。

锁定：
1. 普通用户改公开卡 → 只写自己的覆盖（`scope=personal`），共享卡文件不变；
2. 该用户列表/详情看到的是自己的修改；另一个用户仍看到原样；
3. 嵌套字段（speaking_style）合并而不是整块覆盖；
4. 覆盖只允许白名单字段（内部字段如 user_id 改不动）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-public-overrides-32ch!")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, WechatBinding, get_db

_ALICE = 2
_BOB = 3


def _public_card() -> dict:
    return {
        "id": "pub01",
        "name": "林挽夏",
        "description": "公共卡原描述",
        "user_id": "default",
        "personality": {"gentle": 0.8, "shy": 0.4},
        "speaking_style": {"tone": "soft", "catchphrases": ["嗯"]},
        "core_anchors": ["喜欢雨"],
        "is_active": False,
        "schema_version": 1,
        "version": 1,
    }


def _build_app(tmp_path):
    db_path = str(tmp_path / "users.db")  # users/绑定（与业务库分离——见 conftest 隔离口径）
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
                (1, "admin@d.test", "admin"),
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
                        role="admin" if uid == 1 else "viewer",
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
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    (chars_dir / "pub01.json").write_text(
        json.dumps(_public_card(), ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)
    client, engine = _build_app(tmp_path)
    yield client, engine, chars_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _login(client: AsyncClient, name: str) -> str:
    resp = await client.post("/api/auth/login", json={"login": name, "password": "Passw0rd!123"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_public_card_edit_is_personal_and_does_not_touch_file(d_app):
    client, _engine, chars_dir = d_app

    async def _run():
        alice = await _login(client, "alice")
        bob = await _login(client, "bob")

        put = await client.put(
            "/api/characters/pub01",
            json={"name": "我的林挽夏", "description": "只改我这份"},
            headers=_auth(alice),
        )
        assert put.status_code == 200, put.text
        assert put.json()["scope"] == "personal"

        # 自己看到修改
        mine = await client.get("/api/characters/pub01", headers=_auth(alice))
        assert mine.json()["name"] == "我的林挽夏"
        # 别人仍是原样
        other = await client.get("/api/characters/pub01", headers=_auth(bob))
        assert other.json()["name"] == "林挽夏"
        # 共享卡文件零改动
        on_disk = json.loads((chars_dir / "pub01.json").read_text(encoding="utf-8"))
        assert on_disk["name"] == "林挽夏"
        await client.aclose()

    asyncio.run(_run())


def test_persona_edit_is_personal_and_merges_nested(d_app):
    client, _engine, _chars = d_app

    async def _run():
        alice = await _login(client, "alice")
        # persona GET 返回的是 PersonaCardV3 规范化形状（数值维度），
        # 因此用数值字段断言覆盖生效。
        put = await client.put(
            "/api/characters/pub01/persona",
            json={"personality": {"gentle": 0.1}},
            headers=_auth(alice),
        )
        assert put.status_code == 200 and put.json()["scope"] == "personal"

        # persona GET 会把卡规范化成 V3 数值维度（自定义键会被丢），
        # 因此用原始卡视图断言覆盖值本身生效。
        got = await client.get("/api/characters/pub01", headers=_auth(alice))
        assert got.json()["personality"].get("gentle") == 0.1

        bob = await _login(client, "bob")
        original = await client.get("/api/characters/pub01", headers=_auth(bob))
        assert original.json()["personality"].get("gentle") == 0.8
        await client.aclose()

    asyncio.run(_run())


def test_overlay_rejects_non_whitelisted_fields(tmp_path):
    from utils import character_overrides as co

    # 直接打模块：白名单外的键被静默忽略，不报错也不写脏
    merged = co.write(9, "pub01", {"name": "A", "user_id": "999", "is_active": True})
    assert merged["name"] == "A"
    assert "user_id" not in merged and "is_active" not in merged


def test_apply_merges_nested_dicts(tmp_path, monkeypatch):
    from utils import character_overrides as co

    monkeypatch.setattr(co, "_DB_PATH", tmp_path / "ov.db", raising=False)
    co.write(9, "pub01", {"personality": {"shy": 0.9}})
    merged = co.apply(9, "pub01", _public_card())
    assert merged["personality"]["shy"] == 0.9
    assert merged["personality"]["gentle"] == 0.8  # 未覆盖字段保留
    assert merged["name"] == "林挽夏"
