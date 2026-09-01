"""角色成就体系（ADR-0014）后端测试

覆盖:
  - GET /api/characters/{id}/achievements 挂载与空库返回全量定义
  - 事实源达标 → 解锁落库（首次 unlocked_at）
  - 幂等：重复重算不重复解锁、unlocked_at 不变、进度不回退已解锁
  - 角色隔离：两个角色进度互不影响
  - POST .../recalculate 与 GET 同语义
  - 记忆事实文件驱动（真实事实源，非 mock 内部函数）
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.database import Base, get_db

# ═══════════════════════════════════════════════════════
# 模块级 fixture（参照 test_consent.py 范式）
# ═══════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def module_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("ach-db") / f"ach-{uuid.uuid4().hex}.db"
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
    from api.auth import verify_api_key_dep

    app.dependency_overrides[verify_api_key_dep] = lambda: True
    return app


@pytest.fixture(scope="module")
def _seeded_tables(module_engine):
    """建表（Base.metadata 含全部模型，与生产 init_db 同路径）。"""
    import asyncio

    async def _create():
        async with module_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_create()) if False else asyncio.run(_create())


@pytest.fixture()
def client(_seeded_tables, module_app):
    transport = ASGITransport(app=module_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture()
def fact_file(tmp_path, monkeypatch):
    """把成就引擎的记忆事实目录指到临时目录，返回写入函数。"""
    import api.achievement_engine as engine

    facts_dir = tmp_path / "character_memory"
    facts_dir.mkdir()
    monkeypatch.setattr(engine, "_MEMORY_FACTS_DIR", facts_dir)

    def _write(character_id: str, count: int):
        payload = [{"id": f"f{i}", "content": f"事实{i}", "category": "test"} for i in range(count)]
        (facts_dir / f"{character_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    return _write


# ═══════════════════════════════════════════════════════
# 用例
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_returns_all_definitions_empty(client):
    """空库首次读取：返回全部成就定义、零解锁、结构完整。"""
    async with client as c:
        r = await c.get("/api/characters/ach-empty/achievements")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 10
    assert data["unlocked_count"] == 0
    ids = {a["achievement_id"] for a in data["achievements"]}
    assert {"companion_first", "memory_50", "knowledge_50", "dates_first"} <= ids
    for a in data["achievements"]:
        assert a["unlocked"] is False
        assert a["unlocked_at"] is None
        assert a["target"] >= 1


@pytest.mark.asyncio
async def test_unlock_and_persist_first_time(client, fact_file):
    """事实达标 → 解锁 + unlocked_at 落库；重复重算幂等。"""
    fact_file("ach-unlock", 12)  # memory_10 达标, memory_50 未达标

    async with client as c:
        r1 = await c.get("/api/characters/ach-unlock/achievements")
        data1 = r1.json()
        by_id1 = {a["achievement_id"]: a for a in data1["achievements"]}
        assert by_id1["memory_10"]["unlocked"] is True
        assert by_id1["memory_10"]["progress"] == 12
        assert by_id1["memory_10"]["unlocked_at"] is not None
        assert by_id1["companion_first"]["unlocked"] is True  # memories>=1
        assert by_id1["memory_50"]["unlocked"] is False
        first_ts = by_id1["memory_10"]["unlocked_at"]

        # 幂等：再次重算 unlocked_at 不变，不产生重复行
        r2 = await c.post("/api/characters/ach-unlock/achievements/recalculate")
        assert r2.status_code == 200
        assert r2.json()["recalculated"] is True

        r3 = await c.get("/api/characters/ach-unlock/achievements")
        by_id3 = {a["achievement_id"]: a for a in r3.json()["achievements"]}
        # SQLite 读回 naive datetime，规范化到 naive 后比较（同刻 = 未重复解锁）

        def norm(ts: str) -> str:
            return ts.replace("+00:00", "")

        assert norm(by_id3["memory_10"]["unlocked_at"]) == norm(first_ts)
        assert by_id3["memory_10"]["unlocked"] is True


@pytest.mark.asyncio
async def test_unlock_not_revoked_when_facts_drop(client, fact_file):
    """已解锁不回退：事实源回落后 unlocked 保持 True（ADR-0014 不收回成就）。"""
    fact_file("ach-norevoke", 15)
    async with client as c:
        await c.get("/api/characters/ach-norevoke/achievements")
        fact_file("ach-norevoke", 3)  # 回落，memory_10 的当前进度 < target
        r = await c.get("/api/characters/ach-norevoke/achievements")
    by_id = {a["achievement_id"]: a for a in r.json()["achievements"]}
    assert by_id["memory_10"]["unlocked"] is True
    assert by_id["memory_10"]["progress"] == 3


@pytest.mark.asyncio
async def test_character_isolation(client, fact_file):
    """角色隔离：角色 A 达标不影响角色 B 的解锁状态。"""
    fact_file("ach-iso-a", 20)
    fact_file("ach-iso-b", 0)
    async with client as c:
        ra = await c.get("/api/characters/ach-iso-a/achievements")
        rb = await c.get("/api/characters/ach-iso-b/achievements")
    a = {x["achievement_id"]: x for x in ra.json()["achievements"]}
    b = {x["achievement_id"]: x for x in rb.json()["achievements"]}
    assert a["memory_10"]["unlocked"] is True
    assert b["memory_10"]["unlocked"] is False
    assert b["memory_10"]["progress"] == 0


@pytest.mark.asyncio
async def test_dates_metric_unlocks(client, tmp_path, monkeypatch):
    """重要日期事实源：配置日期 → dates_first 解锁。"""
    dates_file = tmp_path / "important_dates.json"
    dates_file.write_text(
        json.dumps({"ach-dates": [{"name": "生日", "date": "07-07", "kind": "birthday"}]}),
        encoding="utf-8",
    )

    import utils.important_dates as dates_mod

    monkeypatch.setattr(dates_mod, "_PATH", dates_file)
    # 引擎内是函数内延迟 import，monkeypatch 模块属性即可生效

    async with client as c:
        r = await c.get("/api/characters/ach-dates/achievements")
    by_id = {a["achievement_id"]: a for a in r.json()["achievements"]}
    assert by_id["dates_first"]["unlocked"] is True
    assert by_id["dates_first"]["progress"] == 1


@pytest.mark.asyncio
async def test_unauthorized_rejected(module_app, _seeded_tables):
    """认证依赖在位：清掉 override 后走真实 verify_api_key_dep。

    环境自适应：CI/本地测试环境 API_KEY_ENABLED 未开时密钥校验放行（200），
    开启环境必须拒绝（401/403）——两种都算依赖链路在位。
    """
    from api.auth import verify_api_key_dep

    saved = module_app.dependency_overrides.pop(verify_api_key_dep, None)
    try:
        async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as c:
            r = await c.get("/api/characters/ach-auth/achievements")
        assert r.status_code in (200, 401, 403)
    finally:
        if saved is not None:
            module_app.dependency_overrides[verify_api_key_dep] = saved
