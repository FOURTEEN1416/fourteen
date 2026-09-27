"""W1 统一身份与资源授权 — 行为契约测试（红测先行）。

覆盖任务书验收面：
- A：停用后旧 access token 对管理端/读域/配置/通道全链拒绝（is_active + 撤销版本 DB 真源，多请求即时一致）；
     admin 降 viewer 后旧 token 不再有全量读域（scope 从 DB 主体取）。
- 撤销语义：改密 / 管理员重置 → 旧 access 与旧 refresh 同时失效；refresh 库期限与
  JWT_REFRESH_EXPIRE_DAYS 一致。
- 机器 API Key 独立契约：无 Bearer 放行管理面，但不能冒充用户身份（/api/auth/me 401），
  数据面 scope 契约不变。
- B：角色资源 owner 检查（列表/详情/CRUD/激活/知识/音色/记忆/收藏/剧情），owner 从认证主体
     导出，禁止客户端自封；无法判 owner 的存量卡不得自动归属首个访问者。
- D2：激活为 per-user 语义，两用户激活互不覆盖；不再写全局 is_active。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-w1-identity-auth-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    hash_password,
    hash_refresh_token,
)
from api.database import Base, User, UserSession, get_db

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3

_MACHINE_KEY = "w1-machine-api-key-32chars-minimum-ok!!"


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流，不 override 身份依赖）
# ═══════════════════════════════════════════════════════════


def _build_app(tmp_path, *, machine_auth: bool = False):
    db_path = str(tmp_path / "w1_auth.db")
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
                (_ADMIN_ID, "admin@w1.test", "admin", "admin"),
                (_ALICE_ID, "alice@w1.test", "alice", "viewer"),
                (_BOB_ID, "bob@w1.test", "bob", "viewer"),
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
    if machine_auth:
        configure_auth(True, _MACHINE_KEY)
    else:
        configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine, session_factory


async def _login(client: AsyncClient, login: str) -> dict:
    resp = await client.post("/api/auth/login", json={"login": login, "password": "Passw0rd!123"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _db_get_user(session_factory, user_id: int) -> User | None:
    async with session_factory() as session:
        return await session.get(User, user_id)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def w1_app(tmp_path, monkeypatch):
    """标准三账号 app + 角色目录/记忆事实目录沙箱。"""
    from api.routers import character_routes

    chars_dir = tmp_path / "characters"
    facts_dir = tmp_path / "character_memory"
    presets_dir = tmp_path / "presets"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr(character_routes, "MEMORY_FACTS_DIR", facts_dir)
    monkeypatch.setattr(character_routes, "PRESETS_DIR", presets_dir)

    app, client, engine, session_factory = _build_app(tmp_path)
    yield client, engine, session_factory, chars_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


@pytest.fixture
def machine_app(tmp_path):
    """启用机器 API Key 的 app。"""
    app, client, engine, session_factory = _build_app(tmp_path, machine_auth=True)
    yield client, engine, session_factory, app
    asyncio.run(engine.dispose())
    configure_auth(False, "")


# ═══════════════════════════════════════════════════════════
# A：停用账号 → 旧 access token 全链拒绝（多请求即时一致）
# ═══════════════════════════════════════════════════════════

_REJECTED_PATHS = [
    # (方法, 路径, 被停用的主体) —— /api/admin/users 只有 admin 能拿到 200，
    # 故该路径停用的是 admin 自己（update_user 允许自我停用，仅 delete 禁止自删）。
    ("GET", "/api/admin/users", "admin"),
    ("GET", "/api/logs", "alice"),
    ("GET", "/api/memory/facts", "alice"),
    ("GET", "/api/memory/diary", "alice"),
    ("GET", "/api/config", "alice"),
    ("GET", "/api/channels", "alice"),
]


@pytest.mark.parametrize("method,path,owner", _REJECTED_PATHS)
async def test_disabled_account_old_token_rejected_everywhere(w1_app, method, path, owner):
    client, engine, session_factory, _ = w1_app
    old_token = (await _login(client, owner))["access_token"]
    auth = {"Authorization": f"Bearer {old_token}"}

    # 停用前：该主体在自身权限范围内可访问（200）
    pre = await client.request(method, path, headers=auth)
    assert pre.status_code == 200, f"停用前应可访问 {path}: {pre.status_code}"

    # 管理员停用该主体（admin 用例为自我停用）
    if owner == "admin":
        resp = await client.request(
            "PUT", f"/api/admin/users/{_ADMIN_ID}", json={"is_active": False}, headers=auth
        )
    else:
        admin_token = (await _login(client, "admin"))["access_token"]
        resp = await client.request(
            "PUT",
            f"/api/admin/users/{_ALICE_ID}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200, resp.text

    # 停用后：同一旧 token 连续三次请求（多 worker 立即一致的进程内投影）
    for i in range(3):
        r = await client.request(method, path, headers=auth)
        assert r.status_code == 401, f"第{i+1}次请求 {path} 应 401，实得 {r.status_code}"


async def test_disabled_account_relogin_blocked(w1_app):
    client, engine, session_factory, _ = w1_app
    await _login(client, "alice")
    admin_token = (await _login(client, "admin"))["access_token"]
    await client.put(
        f"/api/admin/users/{_ALICE_ID}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    resp = await client.post("/api/auth/login", json={"login": "alice", "password": "Passw0rd!123"})
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════
# A：admin 降 viewer → 旧 token 仍可用但 scope 收窄为本人、管理端 403
# ═══════════════════════════════════════════════════════════


async def test_demoted_admin_memory_scope_is_personal_prefix(w1_app):
    client, engine, session_factory, _ = w1_app
    admin_tokens = await _login(client, "admin")
    old_token = admin_tokens["access_token"]

    resp = await client.put(
        f"/api/admin/users/{_ADMIN_ID}",
        json={"role": "viewer"},
        headers={"Authorization": f"Bearer {(await _login(client, 'admin'))['access_token']}"},
    )
    assert resp.status_code == 200, resp.text

    # 管理端：旧 token 的 require_role 从 DB 取 role → 403
    r = await client.get(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert r.status_code == 403, f"降权后旧 token 管理端应 403，实得 {r.status_code}"

    # 数据面：scope 从 DB 主体取 → 本人前缀（不再是全量）
    # 直接调 scope 依赖（unit 级）：降权后 scope 必须是本人前缀
    from fastapi import Request

    from api.routers import misc_routes

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [(b"authorization", f"Bearer {old_token}".encode())],
            "query_string": b"",
        }
    )
    async with session_factory() as session:
        scope = await misc_routes.memory_scope(request=request, db=session)
    assert scope == f"{_ADMIN_ID}:", f"降权后 scope 应为本人前缀，实得 {scope!r}"

    # 未降权 admin → 全量（None）
    fresh_token = (await _login(client, "alice"))["access_token"]
    request2 = Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [(b"authorization", f"Bearer {fresh_token}".encode())],
            "query_string": b"",
        }
    )
    async with session_factory() as session:
        scope2 = await misc_routes.memory_scope(request=request2, db=session)
    assert scope2 == f"{_ALICE_ID}:"


# ═══════════════════════════════════════════════════════════
# 撤销语义：改密 / 管理员重置 → access+refresh 同时失效
# ═══════════════════════════════════════════════════════════


async def test_change_password_revokes_old_tokens(w1_app):
    client, engine, session_factory, _ = w1_app
    tokens = await _login(client, "alice")
    old_access = tokens["access_token"]
    old_refresh = tokens["refresh_token"]
    auth = {"Authorization": f"Bearer {old_access}"}

    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": "Passw0rd!123", "new_password": "NewPass0rd!456"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text

    r = await client.get("/api/channels", headers=auth)
    assert r.status_code == 401, f"改密后旧 access 应 401，实得 {r.status_code}"

    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401, f"改密后旧 refresh 应 401，实得 {r.status_code}"


async def test_admin_reset_revokes_target_tokens(w1_app):
    client, engine, session_factory, _ = w1_app
    tokens = await _login(client, "alice")
    old_access = tokens["access_token"]
    old_refresh = tokens["refresh_token"]

    admin_token = (await _login(client, "admin"))["access_token"]
    resp = await client.post(
        f"/api/auth/admin/reset-password/{_ALICE_ID}",
        json={"new_password": "ResetPass0rd!789"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text

    r = await client.get("/api/channels", headers={"Authorization": f"Bearer {old_access}"})
    assert r.status_code == 401, f"重置后旧 access 应 401，实得 {r.status_code}"
    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401, f"重置后旧 refresh 应 401，实得 {r.status_code}"


async def test_refresh_db_expiry_matches_config(w1_app, monkeypatch):
    """refresh 库期限必须与 JWT_REFRESH_EXPIRE_DAYS 同源（旧实现硬编码 7 天）。"""
    client, engine, session_factory, _ = w1_app
    assert REFRESH_TOKEN_EXPIRE_DAYS != 0
    tokens = await _login(client, "alice")

    async with session_factory() as session:
        from sqlalchemy import select

        row = (
            await session.execute(
                select(UserSession).where(
                    UserSession.refresh_token_hash == hash_refresh_token(tokens["refresh_token"])
                )
            )
        ).scalar_one_or_none()
        assert row is not None, "refresh 会话未落库"
        expected = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        actual = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
        delta = abs((expected - actual).total_seconds())
        assert delta < 60, f"refresh 库期限 {actual} 应≈配置 {REFRESH_TOKEN_EXPIRE_DAYS} 天"


# ═══════════════════════════════════════════════════════════
# 机器 API Key：独立契约不扩大不缩小
# ═══════════════════════════════════════════════════════════


async def test_machine_key_still_opens_admin_plane(machine_app):
    client, engine, session_factory, _ = machine_app
    r = await client.get("/api/stats", headers={"X-API-Key": _MACHINE_KEY})
    assert r.status_code == 200


async def test_machine_key_cannot_impersonate_user(machine_app):
    client, engine, session_factory, _ = machine_app
    r = await client.get("/api/auth/me", headers={"X-API-Key": _MACHINE_KEY})
    assert r.status_code == 401


async def test_machine_key_memory_scope_contract_unchanged(machine_app):
    """机器 key 数据面 scope=全量（既有管理面契约，不因本批收窄）。"""
    client, engine, session_factory, _ = machine_app
    r = await client.get("/api/memory/facts", headers={"X-API-Key": _MACHINE_KEY})
    assert r.status_code == 200


async def test_disabled_user_token_rejected_even_with_machine_key(machine_app):
    client, engine, session_factory, _ = machine_app
    tokens = await _login(client, "alice")
    old_token = tokens["access_token"]
    admin_token = (await _login(client, "admin"))["access_token"]
    await client.put(
        f"/api/admin/users/{_ALICE_ID}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.get(
        "/api/channels",
        headers={"Authorization": f"Bearer {old_token}", "X-API-Key": _MACHINE_KEY},
    )
    assert r.status_code == 401, "停用用户 token 不得借机器 key 复活"


# ═══════════════════════════════════════════════════════════
# B：角色资源 owner 检查
# ═══════════════════════════════════════════════════════════


async def _alice_creates_character(client: AsyncClient) -> str:
    alice = await _login(client, "alice")
    resp = await client.post(
        "/api/characters",
        json={"name": "爱丽丝的卡", "description": "private", "user_id": "999"},
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_create_character_owner_forced_from_principal(w1_app):
    client, engine, session_factory, chars_dir = w1_app
    cid = await _alice_creates_character(client)
    card = json.loads((chars_dir / f"{cid}.json").read_text(encoding="utf-8"))
    assert card["user_id"] == str(_ALICE_ID), f"owner 必须从认证主体导出，实得 {card['user_id']!r}（客户端自封 999 被拒）"


async def test_list_scoped_to_owner(w1_app):
    client, engine, session_factory, chars_dir = w1_app
    cid = await _alice_creates_character(client)

    bob = await _login(client, "bob")
    r = await client.get(
        "/api/characters", headers={"Authorization": f"Bearer {bob['access_token']}"}
    )
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["characters"]]
    assert cid not in ids, "B 不得在列表中看到 A 的私有卡"

    alice = await _login(client, "alice")
    r = await client.get(
        "/api/characters", headers={"Authorization": f"Bearer {alice['access_token']}"}
    )
    ids = [c["id"] for c in r.json()["characters"]]
    assert cid in ids


async def test_cross_user_character_rud_rejected(w1_app):
    client, engine, session_factory, _ = w1_app
    cid = await _alice_creates_character(client)
    bob_auth = {"Authorization": f"Bearer {(await _login(client, 'bob'))['access_token']}"}

    r = await client.get(f"/api/characters/{cid}", headers=bob_auth)
    assert r.status_code == 404, f"B 读 A 的卡应 404，实得 {r.status_code}"
    r = await client.put(f"/api/characters/{cid}", json={"name": "hacked"}, headers=bob_auth)
    assert r.status_code == 404, f"B 改 A 的卡应 404，实得 {r.status_code}"
    r = await client.delete(f"/api/characters/{cid}", headers=bob_auth)
    assert r.status_code == 404, f"B 删 A 的卡应 404，实得 {r.status_code}"
    r = await client.post(f"/api/characters/{cid}/activate", headers=bob_auth)
    assert r.status_code == 404, f"B 激活 A 的卡应 404，实得 {r.status_code}"


async def test_cross_user_character_resources_rejected(w1_app):
    client, engine, session_factory, _ = w1_app
    cid = await _alice_creates_character(client)
    bob_auth = {"Authorization": f"Bearer {(await _login(client, 'bob'))['access_token']}"}

    checks = [
        ("GET", f"/api/characters/{cid}/knowledge/stats"),
        ("POST", f"/api/characters/{cid}/knowledge/search"),
        ("GET", f"/api/characters/{cid}/voice"),
        ("PUT", f"/api/characters/{cid}/voice"),
        ("GET", f"/api/characters/{cid}/storyline"),
        ("GET", f"/api/characters/{cid}/favorites"),
        ("POST", f"/api/characters/{cid}/favorites"),
        ("GET", f"/api/characters/{cid}/memory/facts"),
        ("POST", f"/api/characters/{cid}/memory/facts"),
        ("GET", f"/api/characters/{cid}/persona-card"),
        ("PUT", f"/api/characters/{cid}/persona-card"),
        ("GET", f"/api/characters/{cid}/persona"),
        ("PUT", f"/api/characters/{cid}/persona"),
        ("GET", f"/api/characters/{cid}/export"),
    ]
    for method, path in checks:
        r = await client.request(method, path, headers=bob_auth, json={})
        assert r.status_code == 404, f"B 访问 A 的资源 {method} {path} 应 404，实得 {r.status_code}"


async def test_owner_still_accesses_own_character(w1_app):
    client, engine, session_factory, _ = w1_app
    cid = await _alice_creates_character(client)
    alice_auth = {"Authorization": f"Bearer {(await _login(client, 'alice'))['access_token']}"}

    r = await client.get(f"/api/characters/{cid}", headers=alice_auth)
    assert r.status_code == 200
    r = await client.put(f"/api/characters/{cid}", json={"description": "self"}, headers=alice_auth)
    assert r.status_code == 200
    r = await client.post(f"/api/characters/{cid}/activate", headers=alice_auth)
    assert r.status_code == 200


async def test_unowned_legacy_card_not_auto_claimed(w1_app):
    """存量无主卡（user_id=default）：普通用户不可见不可改，访问后归属不变。"""
    client, engine, session_factory, chars_dir = w1_app
    legacy = {
        "id": "legacy01",
        "name": "存量卡",
        "description": "orphan",
        "user_id": "default",
        "is_active": False,
        "schema_version": 1,
    }
    (chars_dir / "legacy01.json").write_text(json.dumps(legacy), encoding="utf-8")

    bob_auth = {"Authorization": f"Bearer {(await _login(client, 'bob'))['access_token']}"}
    r = await client.get("/api/characters", headers=bob_auth)
    ids = [c["id"] for c in r.json()["characters"]]
    assert "legacy01" not in ids, "无主存量卡不得进入普通用户列表"

    r = await client.get("/api/characters/legacy01", headers=bob_auth)
    assert r.status_code == 404
    r = await client.put("/api/characters/legacy01", json={"name": "x"}, headers=bob_auth)
    assert r.status_code == 404
    r = await client.post("/api/characters/legacy01/activate", headers=bob_auth)
    assert r.status_code == 404

    after = json.loads((chars_dir / "legacy01.json").read_text(encoding="utf-8"))
    assert after["user_id"] == "default", "访问不得自动把存量卡归属给首个访问者"

    # admin 仍可管理存量卡
    admin_auth = {"Authorization": f"Bearer {(await _login(client, 'admin'))['access_token']}"}
    r = await client.get("/api/characters/legacy01", headers=admin_auth)
    assert r.status_code == 200


async def test_activation_is_per_user_not_global(w1_app):
    """D2：两用户激活互不覆盖；不再写全局 is_active。"""
    client, engine, session_factory, chars_dir = w1_app
    cid_a = await _alice_creates_character(client)
    bob = await _login(client, "bob")
    resp = await client.post(
        "/api/characters",
        json={"name": "鲍勃的卡"},
        headers={"Authorization": f"Bearer {bob['access_token']}"},
    )
    cid_b = resp.json()["id"]
    bob_auth = {"Authorization": f"Bearer {bob['access_token']}"}

    alice_auth = {"Authorization": f"Bearer {(await _login(client, 'alice'))['access_token']}"}
    r = await client.post(f"/api/characters/{cid_a}/activate", headers=alice_auth)
    assert r.status_code == 200
    r = await client.post(f"/api/characters/{cid_b}/activate", headers=bob_auth)
    assert r.status_code == 200

    # A 的列表视角：A 的卡仍是激活态（未被 B 覆盖）
    r = await client.get("/api/characters", headers=alice_auth)
    by_id = {c["id"]: c for c in r.json()["characters"]}
    assert by_id[cid_a]["is_active"] is True, "B 激活后 A 的激活态被覆盖（全局 is_active 缺陷仍在）"

    r = await client.get("/api/characters", headers=bob_auth)
    by_id = {c["id"]: c for c in r.json()["characters"]}
    assert by_id[cid_b]["is_active"] is True
    # B 的视角里根本没有 A 的私有卡（归属隔离优先于「看得到但未点亮」）——
    # 这比「A 的卡对 B 显示为非激活」更强：A 的激活态不可能被 B 观察到或改写。
    assert cid_a not in by_id, "B 列表不得出现 A 的私有卡"

    # 全局卡文件不再承担个人选择（is_active 不被激活端点改写）
    card_a = json.loads((chars_dir / f"{cid_a}.json").read_text(encoding="utf-8"))
    card_b = json.loads((chars_dir / f"{cid_b}.json").read_text(encoding="utf-8"))
    assert card_a["is_active"] is False and card_b["is_active"] is False


async def test_viewer_no_admin_side_effects(w1_app):
    client, engine, session_factory, _ = w1_app
    alice_auth = {"Authorization": f"Bearer {(await _login(client, 'alice'))['access_token']}"}
    r = await client.post("/api/config", json={"config": {}}, headers=alice_auth)
    assert r.status_code == 403
    r = await client.get("/api/routes", headers=alice_auth)
    assert r.status_code == 403
