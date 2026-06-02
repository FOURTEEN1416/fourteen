"""
邀请码系统后端测试

覆盖:
  - 路由挂载检查
  - 使用邀请码注册（成功）
  - 管理员创建/列出/撤销邀请码
  - 错误场景（无效、重复、过期、已撤销、已使用）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 项目根路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.auth_jwt import create_access_token, hash_password
from api.database import Base, InviteCode, User, get_db


# ═══════════════════════════════════════════════════════
# 模块级 fixture（共享引擎，减少开销）
# ═══════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def module_engine():
    """模块级单例异步引擎（文件 DB 避免内存竞争）"""
    db_path = os.path.join(os.path.dirname(__file__), "_test_invite.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    return engine


@pytest.fixture(scope="module")
def module_session_factory(module_engine):
    return async_sessionmaker(module_engine, expire_on_commit=False)


@pytest.fixture(scope="module")
def module_app(module_engine, module_session_factory):
    from api.app_factory import create_api_app

    async def _get_test_db():
        async with module_session_factory() as session:
            yield session

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_test_db
    # Bypass API key auth
    from api.auth import verify_api_key_dep
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    return app


@pytest.fixture(autouse=True)
async def _reset_db(module_engine):
    """每个测试后重置表"""
    async with module_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


# ═══════════════════════════════════════════════════════
# 函数级 fixture
# ═══════════════════════════════════════════════════════

@pytest.fixture
def admin_token():
    return create_access_token({"sub": "1", "email": "admin@test.com", "role": "admin"})


@pytest.fixture
def viewer_token():
    return create_access_token({"sub": "2", "email": "user@test.com", "role": "viewer"})


@pytest.fixture
async def _admin(module_session_factory):
    """创建 admin 用户（id=1）"""
    async with module_session_factory() as db:
        u = User(
            id=1, email="admin@test.com", username="admin",
            hashed_password=hash_password("admin123"),
            role="admin", is_active=True,
        )
        db.add(u)
        await db.commit()


@pytest.fixture
async def _viewer(module_session_factory):
    """创建 viewer 用户（id=2）"""
    async with module_session_factory() as db:
        u = User(
            id=2, email="user@test.com", username="viewer",
            hashed_password=hash_password("pass123"),
            role="viewer", is_active=True,
        )
        db.add(u)
        await db.commit()


@pytest.fixture
async def _invite(module_session_factory):
    """创建一个有效邀请码 testcode1"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(
            code="testcode1", created_by=1, created_at=now,
            expires_at=now + timedelta(days=30),
        ))
        await db.commit()


@pytest.fixture
async def _used_invite(module_session_factory):
    """创建一个已使用邀请码"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(
            code="usedcode1", created_by=1, created_at=now,
            expires_at=now + timedelta(days=30),
            used_by=999, used_at=now,
        ))
        await db.commit()


@pytest.fixture
async def _revoked_invite(module_session_factory):
    """创建一个已撤销邀请码"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(
            code="revoked1", created_by=1, created_at=now,
            expires_at=now + timedelta(days=30), is_revoked=True,
        ))
        await db.commit()


# ═══════════════════════════════════════════════════════
# 路由挂载检查
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_invite_routes_are_mounted(module_app):
    """所有邀请码路由已挂载"""
    routes = set()
    for r in module_app.routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            for m in (r.methods or set()):
                if m != "HEAD":
                    routes.add((m, r.path))
    expected = [
        ("POST", "/api/auth/register-invite"),
        ("POST", "/api/admin/invites"),
        ("GET", "/api/admin/invites"),
        ("DELETE", "/api/admin/invites/{code}"),
    ]
    for method, path in expected:
        assert (method, path) in routes, f"路由 {method} {path} 未挂载"


# ═══════════════════════════════════════════════════════
# 注册成功场景
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_register_with_valid_invite(module_app, module_session_factory, _admin, _invite):
    """使用有效邀请码注册成功"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "testcode1",
            "email": "newuser@test.com",
            "username": "newuser",
            "password": "password123",
            "display_name": "新用户",
        })
        assert resp.status_code == 200, f"注册失败: {resp.text}"
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == "newuser@test.com"

    # 验证邀请码已使用
    async with module_session_factory() as db:
        invite = await db.get(InviteCode, "testcode1")
        assert invite is not None
        assert invite.used_by is not None
        assert invite.used_at is not None


# ═══════════════════════════════════════════════════════
# 注册错误场景
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_register_invalid_code(module_app, _admin):
    """无效邀请码 → 400"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "nonexist",
            "email": "a@b.com", "username": "u1", "password": "pass123",
        })
        data = resp.json()
        assert resp.status_code == 400
        assert data["error_code"] == "INVITE_INVALID"


@pytest.mark.asyncio
async def test_register_used_code(module_app, _admin, _used_invite):
    """已使用邀请码 → 400"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "usedcode1",
            "email": "b@c.com", "username": "u2", "password": "pass123",
        })
        data = resp.json()
        assert resp.status_code == 400
        assert data["error_code"] == "INVITE_INVALID"


@pytest.mark.asyncio
async def test_register_revoked_code(module_app, _admin, _revoked_invite):
    """已撤销邀请码 → 400"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "revoked1",
            "email": "c@d.com", "username": "u3", "password": "pass123",
        })
        data = resp.json()
        assert resp.status_code == 400
        assert data["error_code"] == "INVITE_INVALID"


@pytest.mark.asyncio
async def test_register_duplicate_email(module_app, module_session_factory, _admin, _invite):
    """重复邮箱 → 409"""
    # 先手动注册一个用户
    async with module_session_factory() as db:
        db.add(User(id=101, email="dupe@test.com", username="dupeuser",
                    hashed_password=hash_password("pass123"), role="viewer", is_active=True))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "testcode1",
            "email": "dupe@test.com",
            "username": "another",
            "password": "pass123",
        })
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_duplicate_username(module_app, _admin, _invite):
    """重复用户名 → 409"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post("/api/auth/register-invite", json={
            "invite_code": "testcode1",
            "email": "unique@test.com",
            "username": "admin",  # 已存在（_admin fixture id=1）
            "password": "pass123",
        })
        assert resp.status_code == 409


# ═══════════════════════════════════════════════════════
# Admin 端点
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_create_invites(module_app, _admin, admin_token):
    """管理员创建邀请码"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post(
            "/api/admin/invites",
            json={"count": 3, "expires_days": 30, "note": "测试"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, f"创建失败: {resp.text}"
        data = resp.json()
        assert data["total"] == 3
        assert len(data["codes"]) == 3
        for code in data["codes"]:
            assert len(code) == 8


@pytest.mark.asyncio
async def test_admin_create_invites_requires_admin(module_app, _admin, _viewer, viewer_token):
    """非管理员 → 403"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.post(
            "/api/admin/invites",
            json={"count": 1, "expires_days": 30},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert resp.status_code == 403, f"预期 403, 得到 {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_admin_list_invites(module_app, module_session_factory, _admin, admin_token):
    """管理员列出邀请码"""
    # 创建几个邀请码
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add_all([
            InviteCode(code="list1", created_by=1, created_at=now, expires_at=now + timedelta(days=30)),
            InviteCode(code="list2", created_by=1, created_at=now, expires_at=now + timedelta(days=30)),
        ])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.get(
            "/api/admin/invites",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_admin_list_invites_with_status_filter(module_app, module_session_factory, _admin, admin_token):
    """管理员按状态筛选"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add_all([
            InviteCode(code="v1", created_by=1, created_at=now, expires_at=now + timedelta(days=30)),
            InviteCode(code="u1", created_by=1, created_at=now, expires_at=now + timedelta(days=30), used_by=10, used_at=now),
            InviteCode(code="r1", created_by=1, created_at=now, expires_at=now + timedelta(days=30), is_revoked=True),
        ])
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        for status in ("valid", "used", "revoked"):
            r = await client.get(
                f"/api/admin/invites?status={status}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_revoke_invite(module_app, module_session_factory, _admin, admin_token):
    """管理员撤销有效邀请码"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(code="revokeme", created_by=1, created_at=now, expires_at=now + timedelta(days=30)))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.delete(
            "/api/admin/invites/revokeme",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    async with module_session_factory() as db:
        inv = await db.get(InviteCode, "revokeme")
        assert inv.is_revoked is True


@pytest.mark.asyncio
async def test_admin_revoke_nonexistent(module_app, _admin, admin_token):
    """撤销不存在的邀请码 → 404"""
    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.delete(
            "/api/admin/invites/noexist123",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_revoke_used_code(module_app, module_session_factory, _admin, admin_token):
    """撤销已使用的邀请码 → 400"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(code="used4revoke", created_by=1, created_at=now,
                          expires_at=now + timedelta(days=30), used_by=10, used_at=now))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.delete(
            "/api/admin/invites/used4revoke",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_revoke_revoked_code(module_app, module_session_factory, _admin, admin_token):
    """重复撤销 → 400"""
    now = datetime.now(timezone.utc)
    async with module_session_factory() as db:
        db.add(InviteCode(code="alreadyrev", created_by=1, created_at=now,
                          expires_at=now + timedelta(days=30), is_revoked=True))
        await db.commit()

    async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as client:
        resp = await client.delete(
            "/api/admin/invites/alreadyrev",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400
