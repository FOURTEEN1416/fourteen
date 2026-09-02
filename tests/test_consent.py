"""
使用即同意协议（W2-CONSENT）后端测试

覆盖:
  - POST /api/auth/consent 路由挂载
  - 登录/注册/刷新响应携带 needs_consent 标志
  - 同意记录落库（版本 + 服务端时间戳）
  - 幂等（重复同意同版本不重复落库）
  - 版本不匹配 422 / 未认证 401
  - 协议版本更新后需重新同意
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 项目根路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.auth_jwt import create_access_token, hash_password
from api.consent import CURRENT_AGREEMENT_VERSION, latest_consent
from api.database import Base, ConsentRecord, User, get_db

# ═══════════════════════════════════════════════════════
# 模块级 fixture（参照 test_invite_codes.py 范式）
# ═══════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def module_engine(tmp_path_factory):
    """模块级单例异步引擎；每次测试进程使用独立文件，避免并发污染。"""
    db_path = tmp_path_factory.mktemp("consent-db") / f"consent-{uuid.uuid4().hex}.db"
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
    """每个测试前重置表"""
    async with module_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


# ═══════════════════════════════════════════════════════
# 函数级 fixture
# ═══════════════════════════════════════════════════════

@pytest.fixture
def user_token():
    return create_access_token({"sub": "2", "email": "user@test.com", "role": "viewer"})


@pytest.fixture
async def _user(module_session_factory):
    """创建普通用户（id=2）"""
    async with module_session_factory() as db:
        db.add(User(
            id=2, email="user@test.com", username="user",
            hashed_password=hash_password("pass1234"),
            role="viewer", is_active=True,
        ))
        await db.commit()


# ═══════════════════════════════════════════════════════
# 路由挂载
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_consent_route_is_mounted(module_app):
    """POST /api/auth/consent 已挂载"""
    paths = set()
    for r in module_app.routes:
        # 兼容 FastAPI 0.139+ _IncludedRouter 包装（平铺展开子路由）
        stack = [r]
        while stack:
            cur = stack.pop()
            if hasattr(cur, "path") and hasattr(cur, "methods"):
                for m in (cur.methods or set()):
                    if m != "HEAD":
                        paths.add((m, cur.path))
            elif hasattr(cur, "original_router"):
                stack.extend(cur.original_router.routes)
    assert ("POST", "/api/auth/consent") in paths


# ═══════════════════════════════════════════════════════
# 登录响应携带 needs_consent
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_login_response_carries_needs_consent_true(module_app, _user):
    """未同意过协议的用户登录 → needs_consent=True + 当前版本号"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/api/auth/login", json={
            "login": "user@test.com", "password": "pass1234",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["needs_consent"] is True
    assert data["agreement_version"] == CURRENT_AGREEMENT_VERSION


@pytest.mark.asyncio
async def test_register_response_carries_needs_consent_true(module_app):
    """新注册用户 → needs_consent=True"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/api/auth/register", json={
            "email": "new@test.com", "username": "newuser",
            "password": "newpass123",
        })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["needs_consent"] is True


# ═══════════════════════════════════════════════════════
# 同意记录端点
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_consent_records_version_and_timestamp(module_app, module_session_factory, _user, user_token):
    """同意成功 → 落库版本号 + 服务端时间戳"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["agreement_version"] == CURRENT_AGREEMENT_VERSION
    assert data["agreed_at"], "agreed_at 时间戳缺失"

    async with module_session_factory() as db:
        record = await latest_consent(db, 2)
    assert record is not None
    assert record.agreement_version == CURRENT_AGREEMENT_VERSION
    assert record.agreed_at is not None


@pytest.mark.asyncio
async def test_consent_is_idempotent(module_app, module_session_factory, _user, user_token):
    """重复同意同版本 → 200 且不重复落库"""
    headers = {"Authorization": f"Bearer {user_token}"}
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        r1 = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
            headers=headers,
        )
        r2 = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
            headers=headers,
        )
    assert r1.status_code == 200
    assert r2.status_code == 200

    async with module_session_factory() as db:
        from sqlalchemy import func, select
        count = (await db.execute(
            select(func.count()).select_from(ConsentRecord)
            .where(ConsentRecord.user_id == 2)
        )).scalar()
    assert count == 1, f"期望 1 条同意记录，实际 {count} 条"


@pytest.mark.asyncio
async def test_consent_version_mismatch_rejected(module_app, _user, user_token):
    """版本号与当前协议版本不一致 → 422（防旧版本号绕过）"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": "0.0.1"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_consent_requires_auth(module_app):
    """未携带 token → 401"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_consent_unknown_user_rejected(module_app):
    """token 中 user_id 在库中不存在 → 404"""
    token = create_access_token({"sub": "999", "email": "ghost@test.com", "role": "viewer"})
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════
# 同意后状态流转
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_login_after_consent_needs_consent_false(module_app, _user, user_token):
    """同意后重新登录 → needs_consent=False"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        ok = await ac.post(
            "/api/auth/consent",
            json={"agreement_version": CURRENT_AGREEMENT_VERSION},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert ok.status_code == 200, ok.text

        resp = await ac.post("/api/auth/login", json={
            "login": "user@test.com", "password": "pass1234",
        })
    assert resp.status_code == 200, resp.text
    assert resp.json()["needs_consent"] is False


@pytest.mark.asyncio
async def test_refresh_response_carries_needs_consent(module_app, _user):
    """refresh 换发令牌时同样携带 needs_consent 标志（页面刷新后弹窗状态可恢复）"""
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        login_resp = await ac.post("/api/auth/login", json={
            "login": "user@test.com", "password": "pass1234",
        })
        assert login_resp.status_code == 200
        refresh_token = login_resp.json()["refresh_token"]

        resp = await ac.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["needs_consent"] is True  # 未同意过
    assert data["agreement_version"] == CURRENT_AGREEMENT_VERSION


@pytest.mark.asyncio
async def test_agreement_update_requires_reconsent(module_app, module_session_factory, _user, user_token):
    """协议版本更新后（旧版本同意记录 ≠ 新版本）→ 需重新同意"""
    from api.consent import has_consented

    # 用户同意了"旧版本 0.9.0"
    async with module_session_factory() as db:
        db.add(ConsentRecord(user_id=2, agreement_version="0.9.0"))
        await db.commit()

    # 对当前版本 1.0.0 仍未同意
    async with module_session_factory() as db:
        assert await has_consented(db, 2) is False

    # 对 0.9.0 已同意
    async with module_session_factory() as db:
        assert await has_consented(db, 2, version="0.9.0") is True

    # 登录响应要求重新同意
    async with AsyncClient(
        transport=ASGITransport(app=module_app), base_url="http://test"
    ) as ac:
        resp = await ac.post("/api/auth/login", json={
            "login": "user@test.com", "password": "pass1234",
        })
    assert resp.status_code == 200
    assert resp.json()["needs_consent"] is True
