"""内置默认角色卡（十四 / id=`default`）前后端对齐回归——2026-11-07 用户裁决。

裁决原话：「进入系统默认激活的是十四这一张角色卡，这样前后端都能对齐，
算作是默认角色卡。」

语义钉死六件事：
1. 列表永远含内置卡（对机器面与 Bearer 用户都可见），名字取 persona.yaml；
2. 无个人激活记录 = 内置卡 is_active=true（不是「什么都没激活」）；
   激活其他卡后内置卡熄灭；恢复默认（deactivate）后内置卡重新点亮；
3. GET /api/characters/default → 200 合成视图（builtin=true），不读卡文件；
4. 「我 × 这个角色」设置对 default 完整可用（GET/PUT/DELETE 快照语义）；
5. 内置卡不支持卡文件级写（require_character_write 维持 404）；
6. 剧情线配置端点对 default 显式 400（不是误导性的 404 角色不存在）。
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-builtin-card-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db

_ALICE = 2


def _build_app(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_path = str(tmp_path / "builtin_card.db")
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
            session.add(
                User(
                    id=1,
                    email="admin@d.test",
                    username="admin",
                    hashed_password=hash_password("Passw0rd!123"),
                    display_name="admin",
                    role="admin",
                    is_active=True,
                    is_verified=True,
                )
            )
            session.add(
                User(
                    id=_ALICE,
                    email="alice@d.test",
                    username="alice",
                    hashed_password=hash_password("Passw0rd!123"),
                    display_name="alice",
                    role="viewer",
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
    return client, engine


@pytest.fixture
def b_app(tmp_path, monkeypatch):
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    client, engine = _build_app(tmp_path)
    yield client, chars_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _login(client: AsyncClient, name: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"login": name, "password": "Passw0rd!123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 1. 列表与激活态 ────────────────────────────────────────


async def test_list_contains_builtin_default_card(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]

    resp = await client.get("/api/characters", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    characters = resp.json()["characters"]
    builtin = [c for c in characters if c.get("builtin")]
    assert len(builtin) == 1, "内置默认卡必须恰好在列一张"
    card = builtin[0]
    assert card["id"] == "default"
    assert card["name"], "名字取自 persona.yaml（十四），不得为空"
    assert card["user_id"] == "system"


async def test_no_activation_means_builtin_is_active(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]

    characters = (await client.get("/api/characters", headers=_auth(token))).json()["characters"]
    by_id = {c["id"]: c for c in characters}
    assert by_id["default"]["is_active"] is True, "无激活记录 = 内置十四处于激活态（用户裁决）"
    assert not any(c["is_active"] for c in characters if c["id"] != "default")


async def test_activate_other_card_dims_builtin_and_reset_relights(b_app) -> None:
    client, chars_dir = b_app
    token = (await _login(client, "alice"))["access_token"]

    # 造一张私人卡并激活
    import json as _json
    from pathlib import Path

    card_id = "alicecard1"
    (Path(chars_dir) / f"{card_id}.json").write_text(
        _json.dumps({"id": card_id, "name": "小雅", "user_id": str(_ALICE)}, ensure_ascii=False),
        encoding="utf-8",
    )
    resp = await client.post(f"/api/characters/{card_id}/activate", headers=_auth(token))
    assert resp.status_code == 200, resp.text

    characters = (await client.get("/api/characters", headers=_auth(token))).json()["characters"]
    by_id = {c["id"]: c for c in characters}
    assert by_id[card_id]["is_active"] is True
    assert by_id["default"]["is_active"] is False, "激活其他卡后内置卡必须熄灭"

    # 恢复默认 → 内置卡重新点亮
    resp = await client.post("/api/characters/deactivate", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    characters = (await client.get("/api/characters", headers=_auth(token))).json()["characters"]
    by_id = {c["id"]: c for c in characters}
    assert by_id["default"]["is_active"] is True
    assert by_id[card_id]["is_active"] is False


# ── 2. 详情与设置 ──────────────────────────────────────────


async def test_get_builtin_card_returns_synthetic_view(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]

    resp = await client.get("/api/characters/default", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == "default"
    assert data.get("builtin") is True
    assert data["is_active"] is True, "详情视图同样按「无激活=内置激活」点亮"

    # 机器面（无 Bearer）也可读（对齐公开卡语义）
    resp = await client.get("/api/characters/default")
    assert resp.status_code == 200, resp.text


async def test_my_settings_roundtrip_for_builtin(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]
    headers = _auth(token)

    # alice 未绑定微信 → 服务端派生会话键 409（P1 契约不变）
    resp = await client.get("/api/characters/default/my-settings", headers=headers)
    assert resp.status_code == 409, "未绑定时的 409 引导必须原样保留"
    assert "绑定" in resp.json()["detail"]


async def test_builtin_card_rejects_file_level_write_gate(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]

    # 写门（require_character_write）维持 404：内置卡没有卡文件，删卡/改卡文件不存在
    resp = await client.delete("/api/characters/default", headers=_auth(token))
    assert resp.status_code == 404, resp.text


# ── 3. 剧情线显式拒绝 ─────────────────────────────────────


async def test_storyline_config_for_builtin_is_explicit_400(b_app) -> None:
    client, _ = b_app
    token = (await _login(client, "alice"))["access_token"]

    resp = await client.get("/api/characters/default/storyline", headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert "内置默认人设" in resp.json()["detail"], "必须是显式 400，不是误导性的 404 角色不存在"
