"""W9 自服务生命周期 HTTP 面（D13）：撤回 / 状态 / 注销 / 导出端点钉住。

BOARD W9 条目声称的四个端点（consent/withdraw|status、account/delete、
account/export(+ /chats)）在 abcde64 落库时未随库层接线——本文件先把
「库层已测、HTTP 面缺失」钉成红，再由接线转绿：
1. 无登录主体 → 四端点一律 401（自持契约，不并入机器面）；
2. POST /consent/withdraw → withdrawn；重新同意 → granted（D13 状态机走真源）；
3. POST /account/delete → 冻结即时生效（is_active=False），响应只受理、无 deleted 宣称；
4. GET /account/export(+ /chats) → 只含本人数据；他人 session_key → 404（防越权枚举）。
全部数据落 tmp_path 沙箱（库层复用 api.lifecycle 既有契约，不重造第二真源）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.consent import CURRENT_AGREEMENT_VERSION
from api.database import Base, ConsentRecord, User

UID = 7
OTHER_UID = 8
OWN_KEY = f"{UID}:a@im.wechat"
OTHER_KEY = f"{OTHER_UID}:b@im.wechat"


def _seed_sm(db_path: Path) -> None:
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(db_path))
    with sm.get_connection(write=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_profile ("
            "user_key TEXT PRIMARY KEY, nickname TEXT DEFAULT '')"
        )
        for i in range(1200):
            conn.execute(
                "INSERT INTO chat_history(role, content, session_id, user_key) "
                "VALUES (?, ?, ?, ?)",
                ("user" if i % 2 == 0 else "assistant", f"消息{i}", OWN_KEY, OWN_KEY),
            )
        conn.execute(
            "INSERT INTO chat_history(role, content, session_id, user_key) "
            "VALUES ('user', '别人的', ?, ?)",
            (OTHER_KEY, OTHER_KEY),
        )
        conn.execute(
            "INSERT INTO user_facts(fact, category, user_key) VALUES ('事实A', 'general', ?)",
            (OWN_KEY,),
        )
        conn.execute(
            "INSERT INTO user_profile(user_key, nickname) VALUES (?, '昵称A')",
            (OWN_KEY,),
        )
        conn.commit()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立 users.db + 沙箱结构化记忆库 + 两种鉴权形态的 TestClient。"""
    from api import lifecycle as lifecycle_mod
    from api.auth_jwt import get_current_user_id
    from api.routers import auth_routes

    root = tmp_path / "w9selfsvc"
    root.mkdir()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(root / 'users.db').as_posix()}"
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    async def _seed():
        async with maker() as db:
            db.add(
                User(
                    id=UID, email="u7@example.com", username="user7",
                    hashed_password="x" * 60,
                )
            )
            db.add(
                ConsentRecord(user_id=UID, agreement_version=CURRENT_AGREEMENT_VERSION)
            )
            await db.commit()

    asyncio.run(_seed())

    sm_path = root / "mem.sqlite"
    _seed_sm(sm_path)

    async def _get_db():
        async with maker() as session:
            yield session

    def _build(with_principal: bool) -> TestClient:
        app = FastAPI()
        app.include_router(auth_routes.router)
        # get_db 必须指向沙箱库：绝不触碰宿主真实 data/users.db（本夹具曾漏挂
        # 该 override 导致端点连真实库——已修，作为多窗沙箱纪律的反面教材留痕）
        app.dependency_overrides[auth_routes.get_db] = _get_db
        if with_principal:
            app.dependency_overrides[get_current_user_id] = lambda: UID
        return TestClient(app, raise_server_exceptions=False)

    # 库层默认工厂/导出真源/作业目录全部沙箱化（端点走生产路径、不落宿主真库）
    monkeypatch.setattr(lifecycle_mod, "_default_db_factory", lambda: maker)
    monkeypatch.setattr(lifecycle_mod, "job_root", lambda: root / "jobs")
    monkeypatch.setattr(
        lifecycle_mod,
        "_export_sm",
        lambda sm, sqlite_db: _fresh_sm(sm_path),
    )

    yield {
        "build": _build,
        "maker": maker,
        "root": root,
        "sm_path": sm_path,
    }
    asyncio.run(engine.dispose())


def _fresh_sm(db_path: Path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    return StructuredMemory(str(db_path))


ENDPOINTS = [
    ("POST", "/api/auth/consent/withdraw"),
    ("GET", "/api/auth/consent/status"),
    ("POST", "/api/auth/account/delete"),
    ("GET", "/api/auth/account/export"),
]


def test_all_four_endpoints_require_principal(env):
    """无登录主体 → 一律 401（新端点自持契约，不并入机器面）。"""
    client = env["build"](with_principal=False)
    for method, path in ENDPOINTS:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} 未自持 401: {resp.status_code}"


def test_withdraw_and_reagree_cycle(env):
    """撤回 → withdrawn；重新同意 → granted（D13 状态机走真源）。"""
    client = env["build"](with_principal=True)
    resp = client.post("/api/auth/consent/withdraw")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "withdrawn"

    status = client.get("/api/auth/consent/status").json()
    assert status["status"] == "withdrawn"
    assert status["agreement_version"] == CURRENT_AGREEMENT_VERSION

    # 重新同意走既有 /consent 面（同库同真源）
    reagree = client.post(
        "/api/auth/consent", json={"agreement_version": CURRENT_AGREEMENT_VERSION}
    )
    assert reagree.status_code == 200, reagree.text
    assert client.get("/api/auth/consent/status").json()["status"] == "granted"


def test_self_service_delete_freezes_and_queues_job(env):
    """注销：冻结即时生效；响应只受理（queued/purging），无 deleted 宣称。"""
    client = env["build"](with_principal=True)
    resp = client.post("/api/auth/account/delete")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in ("queued", "purging")
    assert body.get("completed") is False
    assert "deleted" not in {k.lower() for k in body}

    # 冻结落库实证：账号停用
    async def _check():
        from sqlalchemy import select

        async with env["maker"]() as db:
            user = (await db.execute(select(User).where(User.id == UID))).scalar_one()
            return user.is_active

    assert asyncio.run(_check()) is False


def test_export_manifest_scoped_to_owner(env):
    """导出清单：只含本人数据与他萝会话键，不含他人行。"""
    client = env["build"](with_principal=True)
    resp = client.get("/api/auth/account/export")
    assert resp.status_code == 200, resp.text
    manifest = resp.json()
    assert manifest["user_id"] == UID
    assert manifest["categories"]["chats"] == 1200
    assert manifest["categories"]["facts"] == 1
    assert manifest["categories"]["profile"] == 1
    payload = json.dumps(manifest, ensure_ascii=False)
    assert OWN_KEY in payload
    assert OTHER_KEY not in payload, "导出清单混入他人会话键"


def test_export_chats_pagination_and_cross_user_404(env):
    """分页导出本人会话原文；他人 session_key → 404（不泄露存在性）。"""
    client = env["build"](with_principal=True)

    # 他人会话 → 404
    other = client.get(
        "/api/auth/account/export/chats", params={"session_key": OTHER_KEY}
    )
    assert other.status_code == 404, other.text

    # 本人会话：分页取完 1200 条且逐条归属正确
    collected: list[dict] = []
    before_id = 0
    for _ in range(10):
        page = client.get(
            "/api/auth/account/export/chats",
            params={"session_key": OWN_KEY, "before_id": before_id, "limit": 500},
        )
        assert page.status_code == 200, page.text
        data = page.json()
        collected.extend(data["messages"])
        if data.get("next_before_id") is None:
            break
        before_id = data["next_before_id"]
    assert len(collected) == 1200
    assert all(m["session_id"] == OWN_KEY for m in collected)
    assert not any("别人的" in json.dumps(m) for m in collected)
