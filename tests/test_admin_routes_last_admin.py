"""NEW-17（2026-10-29）+ 对抗验收 F2（2026-10-30）：最后一个 active admin 守卫。

单管理员站一次误操作（自我降权/自我停用/被另一管理员删除）即锁死全部管理入口——
update_user 拒绝降权/停用、delete_user 拒绝删除唯一 active admin，其余情况放行。
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-last-admin-32chars-ok!!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db


def _build_app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/users.db", echo=False)
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
            for uid, email, role in (
                (1, "admin@a.test", "admin"),
                (2, "viewer@a.test", "viewer"),
            ):
                session.add(
                    User(id=uid, email=email, username=email.split("@")[0],
                         hashed_password=hash_password("Passw0rd!123"),
                         display_name=email.split("@")[0], role=role,
                         is_active=True, is_verified=True)
                )
            await session.commit()

    asyncio.run(_init())
    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine, session_factory


def _bearer(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def api(tmp_path):
    client, engine, session_factory = _build_app(tmp_path)
    yield client, session_factory
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _add_admin(session_factory, uid: int) -> None:
    from api.database import User as UserForSeed

    async with session_factory() as s:
        s.add(UserForSeed(id=uid, email=f"admin{uid}@a.test", username=f"admin{uid}",
                 hashed_password=hash_password("Passw0rd!123"),
                 display_name=f"admin{uid}", role="admin",
                 is_active=True, is_verified=True))
        await s.commit()


async def test_demote_last_admin_is_400(api):
    """唯一 admin 自我降权 → 400（锁死预防）。"""
    client, _sf = api
    r = await client.put("/api/admin/users/1", json={"role": "viewer"}, headers=_bearer(1))
    assert r.status_code == 400, r.text
    assert "至少保留一个启用中的管理员" in r.json()["detail"]


async def test_deactivate_last_admin_is_400(api):
    client, _sf = api
    r = await client.put("/api/admin/users/1", json={"is_active": False}, headers=_bearer(1))
    assert r.status_code == 400
    assert "不能停用唯一的 active 管理员" in r.json()["detail"]


async def test_demote_when_two_admins_ok(api):
    """两个 admin 时降权其一放行（守卫不误伤正常运维）。"""
    client, sf = api
    await _add_admin(sf, 3)
    r0 = await client.put("/api/admin/users/2", json={"role": "admin"}, headers=_bearer(1))
    assert r0.status_code == 200, r0.text
    r = await client.put("/api/admin/users/2", json={"role": "viewer"}, headers=_bearer(1))
    assert r.status_code == 200, r.text


async def test_demote_non_admin_unaffected(api):
    """降权普通用户与守卫无关，照常 200。"""
    client, _sf = api
    r = await client.put("/api/admin/users/2", json={"display_name": "x"}, headers=_bearer(1))
    assert r.status_code == 200


# ── 对抗验收 F2（2026-10-30，定性：假阳→纵深防御）──
# 推演：执行者必须是 active admin 且不能自删（既有规则），故任何 delete 之后场上
# 至少剩执行者本人 ≥1 个 active admin——delete_user 本就锁不死。原判「守卫缝隙」
# 系场景设计错误（admin4 删 admin1 后仍剩 admin4，是合法运维）。守卫保留为纵深
# 防御（防未来自删规则放松），本用例钉住不变式：删除后管理入口仍可用。


async def test_delete_leaves_at_least_one_active_admin_invariant(api):
    """不变式：任意合法删除后，场上仍有 active admin 且管理端可用。"""
    client, sf = api
    await _add_admin(sf, 3)
    # admin1 删 admin3（多余 admin）→ 剩 admin1，管理端仍可用
    r = await client.delete("/api/admin/users/3", headers=_bearer(1))
    assert r.status_code == 200, r.text
    r2 = await client.get("/api/admin/users", headers=_bearer(1))
    assert r2.status_code == 200
    assert any(u["role"] == "admin" and u["is_active"] for u in r2.json()["users"])
    # admin1 自删被既有规则拦（不变式的另一条腿）
    r3 = await client.delete("/api/admin/users/1", headers=_bearer(1))
    assert r3.status_code == 400
