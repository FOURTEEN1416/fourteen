"""对抗验收补充用例（2026-11-07）— 执行位矩阵中作者测试未覆盖的格。

发现即修原则：这些缺口由对抗验收发现，当场补测试钉住。
- A1 多条微信绑定全部复位（作者用例只造 1 条）
- A2 零绑定用户恢复不崩（wxids=[] 分支）
- A3 deps.gf=None 时恢复不崩（热切整段跳过）
- A4 API_KEY_ENABLED=true 时无 Bearer 恢复被拒（机器面不能匿名清全局 is_active）
- A5 扫码人会话键 {uid}:{wxid} 被热切（谁扫码谁聊的活实例）
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
os.environ.setdefault("JWT_SECRET", "test-secret-for-deactivate-adv-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, WechatBinding, get_db
from api.deps import deps as api_deps

_ALICE = 2


def _build_app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'adv.db'}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with session_factory() as s:
            yield s

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id=_ALICE, email="a@t", username="alice",
                       hashed_password=hash_password("Passw0rd!123"),
                       display_name="alice", role="viewer",
                       is_active=True, is_verified=True))
            await s.commit()

    asyncio.run(_init())
    app = create_api_app()
    app.dependency_overrides[get_db] = _override
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine, session_factory


class _FakeGf:
    def __init__(self, with_upsert: bool = True) -> None:
        self.calls: list[tuple] = []
        self._with_upsert = with_upsert

    def set_user_character(self, user_id: str, card_id: str) -> bool:
        self.calls.append(("set", str(user_id), str(card_id)))
        return True

    async def upsert_binding(self, wxid: str, data: dict) -> None:
        self.calls.append(("upsert", str(wxid), dict(data)))


@pytest.fixture
def adv_app(tmp_path, monkeypatch):
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    client, engine, session_factory = _build_app(tmp_path)
    fake_gf = _FakeGf()
    # 必须在 create_api_app 之后注入（装配期会写 deps.gf）
    monkeypatch.setattr(api_deps, "gf", fake_gf, raising=False)
    yield client, engine, session_factory, chars_dir, fake_gf
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _login(client: AsyncClient) -> dict:
    r = await client.post("/api/auth/login", json={"login": "alice", "password": "Passw0rd!123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client: AsyncClient, auth: dict, chars_dir, card_id: str) -> None:
    path = chars_dir / f"{card_id}.json"
    path.write_text(json.dumps({
        "id": card_id, "name": "卡", "description": "x",
        "user_id": str(_ALICE), "is_active": False, "schema_version": 1,
    }, ensure_ascii=False), encoding="utf-8")
    r = await client.post(f"/api/characters/{card_id}/activate", headers=auth)
    assert r.status_code == 200, r.text


# ── A1：多条微信绑定全部复位 ────────────────────────────────


async def test_deactivate_resets_every_binding_of_user(adv_app):
    client, _e, session_factory, chars_dir, gf = adv_app
    auth = await _login(client)
    await _activate(client, auth, chars_dir, "adv1")

    async with session_factory() as s:
        s.add(WechatBinding(user_id=_ALICE, wxid="wx_main", character_card_id="adv1"))
        s.add(WechatBinding(user_id=_ALICE, wxid="wx_second", character_card_id="adv1"))
        await s.commit()

    r = await client.post("/api/characters/deactivate", headers=auth)
    assert r.status_code == 200, r.text

    async with session_factory() as s:
        rows = (await s.execute(select(WechatBinding))).scalars().all()
    by_wxid = {b.wxid: b.character_card_id for b in rows}
    assert by_wxid == {"wx_main": "default", "wx_second": "default"}, \
        f"两条绑定都必须复位，实得 {by_wxid}"
    # 每条绑定的内存缓存都要刷
    upserts = {c[1]: c[2]["character_card_id"] for c in gf.calls if c[0] == "upsert"}
    assert upserts == {"wx_main": "default", "wx_second": "default"}
    # 扫码人会话键 {uid}:{wxid} 热切（谁扫码谁聊的活实例）
    assert ("set", f"{_ALICE}:wx_main", "default") in gf.calls
    assert ("set", f"{_ALICE}:wx_second", "default") in gf.calls


# ── A2：零绑定用户恢复不崩 ──────────────────────────────────


async def test_deactivate_without_bindings_ok(adv_app):
    client, _e, session_factory, chars_dir, gf = adv_app
    auth = await _login(client)
    await _activate(client, auth, chars_dir, "adv2")

    r = await client.post("/api/characters/deactivate", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "reset", "character_id": "default"}
    # 无绑定 → 无 upsert，但用户键实例仍要热切
    assert ("set", str(_ALICE), "default") in gf.calls
    assert not [c for c in gf.calls if c[0] == "upsert"]


# ── A3：deps.gf=None 时恢复不崩 ─────────────────────────────


async def test_deactivate_without_gf_ok(adv_app, monkeypatch):
    client, _e, session_factory, chars_dir, _gf = adv_app
    monkeypatch.setattr(api_deps, "gf", None, raising=False)
    auth = await _login(client)
    await _activate(client, auth, chars_dir, "adv3")

    r = await client.post("/api/characters/deactivate", headers=auth)
    assert r.status_code == 200, r.text
    # DB 层复位不依赖 gf
    async with session_factory() as s:
        rows = (await s.execute(select(WechatBinding))).scalars().all()
    assert all(b.character_card_id == "default" for b in rows)


# ── A4：API Key 启用时机器面不得匿名恢复 ────────────────────


async def test_deactivate_machine_face_rejected_when_auth_enabled(adv_app, monkeypatch):
    client, engine, session_factory, chars_dir, _gf = adv_app

    # 先在 auth 关闭态激活（写全局 is_active，模拟机器面激活）
    configure_auth(False, "")
    path = chars_dir / "adv4.json"
    path.write_text(json.dumps({
        "id": "adv4", "name": "全局活跃卡", "description": "x",
        "user_id": "default", "is_active": True, "schema_version": 1,
    }, ensure_ascii=False), encoding="utf-8")
    r = await client.post("/api/characters/adv4/activate")
    assert r.status_code == 200, r.text

    # 切换到 API Key 认证启用态：无 Bearer 无 X-API-Key → 必须 401
    configure_auth(True, "adv-machine-key-32chars-minimum-ok!!!")
    try:
        r = await client.post("/api/characters/deactivate")
        assert r.status_code == 401, \
            f"认证启用时匿名恢复必须 401，实得 {r.status_code}"
        # 全局 is_active 不被匿名清掉
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["is_active"] is True, "匿名请求不得清全局 is_active"

        # 带正确 X-API-Key 的机器面可以恢复
        r = await client.post(
            "/api/characters/deactivate", headers={"X-API-Key": "adv-machine-key-32chars-minimum-ok!!!"}
        )
        assert r.status_code == 200, r.text
        after = json.loads(path.read_text(encoding="utf-8"))
        assert after["is_active"] is False
    finally:
        configure_auth(False, "")
