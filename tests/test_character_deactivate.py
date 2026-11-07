"""恢复默认人设（POST /api/characters/deactivate）— 2026-11-07 根治批。

用户报障：内置默认人设（十四）不在 `config/characters/` 卡库里，角色列表
列不出它；激活又是单向的，误点「设为活跃」后 UI 上没有任何路径回到「什么都
没选」。本文件钉住修复的三层契约：

1. **个人面三处复位**：`user_active_characters` 行删除、`wechat_bindings`
   该用户全部绑定回 `default`、运行中缓存热切（`set_user_character` /
   `upsert_binding`）——只改一层就会留下「列表已恢复但微信还说旧角色」的裂缝。
2. **隔离与幂等**：A 恢复不影响 B；未激活时重复调用仍成功。
3. **聊天链默认语义**：真人面显式 `character_id="default"` 必须解析为内置
   十四，不得被卡片级全局 `is_active` 覆盖（WS/HTTP 的默认人设真语义）；
   机器面（无 Bearer）保留旧的卡片级回落契约。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-deactivate-32chars-min-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import AuthPrincipal, hash_password
from api.database import (
    Base,
    User,
    UserActiveCharacter,
    WechatBinding,
    get_db,
)
from api.deps import deps as api_deps

_ALICE = 2
_BOB = 3


# ── App / DB 沙箱 ──────────────────────────────────────────


def _build_app(tmp_path):
    db_path = str(tmp_path / "deactivate.db")
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
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine, session_factory


class _FakeGf:
    """运行中缓存替身——记录热切调用，不触碰真实用户实例。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_user_character(self, user_id: str, card_id: str) -> bool:
        self.calls.append(("set", str(user_id), str(card_id)))
        return True

    async def upsert_binding(self, wxid: str, data: dict) -> None:
        self.calls.append(("upsert", str(wxid), dict(data)))


@pytest.fixture
def d_app(tmp_path, monkeypatch):
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    client, engine, session_factory = _build_app(tmp_path)

    # ⚠️ 必须在 create_api_app 之后注入：app 装配会写 deps.gf（先把替身放进去
    # 会被装配期覆盖 → 热切断言假绿）
    fake_gf = _FakeGf()
    monkeypatch.setattr(api_deps, "gf", fake_gf, raising=False)

    yield client, engine, session_factory, chars_dir, fake_gf
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


async def _create_card(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/characters", json={"name": name}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _snapshot(session_factory):
    async with session_factory() as session:
        active = (await session.execute(select(UserActiveCharacter))).scalars().all()
        bindings = (await session.execute(select(WechatBinding))).scalars().all()
        return (
            {int(r.user_id): r.character_id for r in active},
            {r.wxid: r.character_card_id for r in bindings},
        )


async def _add_binding(session_factory, user_id: int, wxid: str, card_id: str) -> None:
    async with session_factory() as session:
        session.add(
            WechatBinding(user_id=user_id, wxid=wxid, character_card_id=card_id)
        )
        await session.commit()


# ── 1. 个人面三处复位 ──────────────────────────────────────


async def test_deactivate_resets_all_three_layers(d_app):
    client, _engine, session_factory, chars_dir, fake_gf = d_app
    token = (await _login(client, "alice"))["access_token"]

    card_id = await _create_card(client, token, "误选的角色")
    resp = await client.post(
        f"/api/characters/{card_id}/activate", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    await _add_binding(session_factory, _ALICE, "wx_alice", card_id)

    resp = await client.post("/api/characters/deactivate", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reset"
    assert body["character_id"] == "default"

    # ① 个人激活行已删
    active, bindings = await _snapshot(session_factory)
    assert _ALICE not in active, "个人激活行必须删除（列表回到无激活）"
    # ② 微信绑定回内置十四
    assert bindings["wx_alice"] == "default", "微信绑定必须回 default（回复人设真源）"
    # ③ 运行中缓存热切：用户键 + 绑定缓存
    assert ("set", str(_ALICE), "default") in fake_gf.calls
    assert ("upsert", "wx_alice", {"wxid": "wx_alice", "character_card_id": "default"}) in fake_gf.calls

    # 列表视角：恢复默认 = **内置十四卡点亮**（2026-11-07 用户裁决：default 是
    # 正式默认角色卡，「什么都没选」的旧语义由「默认激活十四」取代），
    # 其余卡文件全部熄灭
    resp = await client.get("/api/characters", headers=_auth(token))
    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["characters"]}
    assert by_id["default"]["is_active"] is True
    assert all(not c["is_active"] for c in resp.json()["characters"] if c["id"] != "default")

    # 卡文件本体的 is_active 不被个人恢复动作改写（D2：个人选择与卡文件解耦）
    card = json.loads((chars_dir / f"{card_id}.json").read_text(encoding="utf-8"))
    assert card["is_active"] is False


async def test_deactivate_is_per_user(d_app):
    """A 恢复默认不得影响 B 的激活与绑定。"""
    client, _engine, session_factory, _chars_dir, _gf = d_app
    alice = (await _login(client, "alice"))["access_token"]
    bob = (await _login(client, "bob"))["access_token"]

    card_a = await _create_card(client, alice, "Alice 的卡")
    card_b = await _create_card(client, bob, "Bob 的卡")
    assert (await client.post(f"/api/characters/{card_a}/activate", headers=_auth(alice))).status_code == 200
    assert (await client.post(f"/api/characters/{card_b}/activate", headers=_auth(bob))).status_code == 200
    await _add_binding(session_factory, _BOB, "wx_bob", card_b)

    resp = await client.post("/api/characters/deactivate", headers=_auth(alice))
    assert resp.status_code == 200

    active, bindings = await _snapshot(session_factory)
    assert _ALICE not in active
    assert active.get(_BOB) == card_b, "B 的激活行被 A 的恢复动作改掉了"
    assert bindings["wx_bob"] == card_b, "B 的微信绑定被 A 的恢复动作改掉了"


async def test_deactivate_is_idempotent(d_app):
    client, _engine, _session_factory, _chars_dir, _gf = d_app
    token = (await _login(client, "alice"))["access_token"]

    for _ in range(2):
        resp = await client.post("/api/characters/deactivate", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "reset"


# ── 2. 机器面：复位卡片级系统默认 ──────────────────────────


async def test_deactivate_machine_face_clears_global_is_active(d_app):
    client, _engine, _session_factory, chars_dir, _gf = d_app
    token = (await _login(client, "alice"))["access_token"]
    card_id = await _create_card(client, token, "全局活跃卡")

    # 模拟机器面 /activate 写出的卡片级 is_active
    path = chars_dir / f"{card_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["is_active"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    resp = await client.post("/api/characters/deactivate")  # 无 Bearer = 机器面
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reset"
    assert card_id in body["reset_cards"]

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["is_active"] is False


# ── 3. 聊天链默认语义：真人面 default ≠ 全局 is_active ─────


async def test_chat_explicit_default_ignores_global_active_for_humans(d_app):
    """WS/HTTP 显式传 default 时必须是内置十四，不能被卡片级 is_active 顶替。

    旧实现把 "default" 当「未指定」→ 恢复默认后 WS 仍按全局卡表说话，
    与个人激活语义分裂（本批一并根治）。
    """
    from api.routers.character_routes import get_active_character_id
    from api.routers.chat_routes import _resolve_character_id

    client, _engine, _session_factory, chars_dir, _gf = d_app
    token = (await _login(client, "alice"))["access_token"]
    card_id = await _create_card(client, token, "全局活跃卡")
    path = chars_dir / f"{card_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["is_active"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 缓存指纹依赖目录 mtime，先确认全局解析确实指向该卡
    assert get_active_character_id() == card_id

    principal = AuthPrincipal(
        user_id=_ALICE,
        role="viewer",
        token_version=0,
        user=User(
            id=_ALICE,
            email="alice@d.test",
            username="alice",
            hashed_password="x",
            role="viewer",
        ),
    )
    # 真人面：显式 default → 内置十四
    assert await _resolve_character_id("default", principal) == "default"
    # 真人面：空串（未指定）仍回落卡片级系统默认（兼容面不回归）
    assert await _resolve_character_id("", principal) == card_id
    # 机器面：保留旧契约
    assert await _resolve_character_id("default", None) == card_id
