"""认证：有效 JWT 可通过 verify_api_key_dep（用户侧不再需要前端打进 API Key）。

P0 收尾批 F1（2026-10-05）改写 test_jwt_passes_without_api_key：
旧用例用无 DB 主体的合成 token（``create_access_token({"sub": "2"})``）断言
「任意可验签 JWT 通过 verify_api_key_dep」——这正是 P0 修复批 F1 收口的
漏洞本体（JWT 分支只验签不校验主体）。新契约：JWT 分支追加主体校验
（存在 + is_active + 撤销版本），故本文件的有效 JWT 用例必须种真库用户、
经 token_claims 签发（仿 tests/test_sec_p0_auth.py seeded_db 模式）。
"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends, FastAPI, Security
from fastapi.testclient import TestClient

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-jwt-or-apikey-auth-32chars-ok!")

_MACHINE_KEY = "super-secret-api-key-32chars-minimum!!"


@pytest.fixture()
def client():
    from api.auth import configure_auth, verify_api_key_dep

    configure_auth(True, _MACHINE_KEY)
    app = FastAPI()

    @app.get("/protected")
    def protected(_ok: bool = Security(verify_api_key_dep)):
        return {"ok": True}

    @app.get("/protected2")
    def protected2(_ok: bool = Depends(verify_api_key_dep)):
        return {"ok": True}

    return TestClient(app)


# ═══════════════════════════════════════════════════════
# P0 收尾批 F1：JWT 分支主体校验（seeded_db 夹具，仿 test_sec_p0_auth）
# ═══════════════════════════════════════════════════════


@pytest.fixture()
async def seeded_db(tmp_path):
    """tmp sqlite 建表 + 种启用用户（id=2, viewer, active）+ get_db 沙箱覆盖。

    逐用例独立引擎，teardown dispose（Windows 文件锁）。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.database import Base, User, get_db

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jwt_or_key.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(
                User(
                    id=2,
                    email="u2@test",
                    username="u2",
                    hashed_password="x",
                    display_name="u2",
                    role="viewer",
                    is_active=True,
                    is_verified=True,
                    token_version=0,
                )
            )
            await db.commit()

    await _init()

    app = FastAPI()
    from api.auth import configure_auth, verify_api_key_dep

    configure_auth(True, _MACHINE_KEY)

    @app.get("/protected")
    def protected(_ok: bool = Security(verify_api_key_dep)):
        return {"ok": True}

    async def _db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db

    yield app, factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_jwt_passes_without_api_key(seeded_db):
    """有效且启用用户的 JWT 放行 200；同 token 停用后 401。

    修复前缺陷口径（已废）：无 DB 主体的合成 token 也能借验签放行——
    主体校验收口后，任意可验签 JWT 不再等价于合法登录态。
    """
    from api.auth_jwt import create_access_token, token_claims
    from api.database import User

    app, factory = seeded_db

    async with factory() as db:
        user = await db.get(User, 2)
        token = create_access_token(token_claims(user))

    c = TestClient(app)
    r = c.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # 同一 token：主体被停用后立即失效 → 401（落入 API Key 分支且无 key）
    async with factory() as db:
        user = await db.get(User, 2)
        user.is_active = False
        await db.commit()

    r2 = c.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 401, f"停用账号 token 实得 {r2.status_code}，必须 401"


def test_api_key_still_works_for_machine(client):
    r = client.get("/protected", headers={"X-API-Key": _MACHINE_KEY})
    assert r.status_code == 200


def test_anonymous_rejected_when_api_key_enabled(client):
    r = client.get("/protected")
    assert r.status_code == 401


def test_invalid_jwt_falls_back_to_api_key_check(client):
    r = client.get("/protected", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
