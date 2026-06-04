"""
绑定 API 测试 — 微信账号 ↔ 注册用户绑定 CRUD

覆盖：
- POST /api/wechat/bind（创建 / 重复绑定）
- GET  /api/wechat/bindings（列表）
- PUT  /api/wechat/bindings/{wxid}（更新角色）
- DELETE /api/wechat/bindings/{wxid}（解绑）
- 边界：空 wxid、wxid 过长、他人 wxid 冲突
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.app_factory import create_api_app
from api.auth_jwt import get_current_user_id
from api.database import Base, get_db

# ── 帮助函数 ──

_MOCK_USER_ID = 42


def _build_app_and_engine(tmp_db_path: str):
    """创建测试用 FastAPI 实例 + engine，SQLite 临时文件 + mock 用户"""
    db_url = f"sqlite+aiosqlite:///{tmp_db_path}"
    engine = create_async_engine(db_url, echo=False)
    test_session = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with test_session() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _mock_get_current_user_id():
        return _MOCK_USER_ID

    # 建表
    import asyncio

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user_id] = _mock_get_current_user_id
    return app, engine


@pytest.fixture
def app_with_db(tmp_path):
    """返回 (client, engine, app) 供测试使用"""
    db_path = str(tmp_path / "test_bindings.db")
    app, engine = _build_app_and_engine(db_path)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client, engine, app

    import asyncio
    asyncio.run(engine.dispose())


async def _db_check_one(engine, wxid: str):
    """用 engine 直查某 wxid 的绑定记录，返回 WechatBinding 或 None"""
    from sqlalchemy import text

    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT id, user_id, wxid, nickname, character_card_id FROM wechat_bindings WHERE wxid = :wxid"),
            {"wxid": wxid},
        )).mappings().one_or_none()
    return row


# ═══════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bind_create(app_with_db):
    """POST /api/wechat/bind — 正常创建绑定"""
    client, engine, _ = app_with_db

    resp = await client.post("/api/wechat/bind", json={
        "wxid": "wx_test_001",
        "nickname": "测试微信",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # 验证响应
    assert body["status"] == "bound"
    assert body["wxid"] == "wx_test_001"
    assert body["binding"]["nickname"] == "测试微信"
    assert body["binding"]["user_id"] == _MOCK_USER_ID
    assert body["binding"]["character_card_id"] == "default"

    # 验证 DB 持久化
    row = await _db_check_one(engine, "wx_test_001")
    assert row is not None
    assert row["user_id"] == _MOCK_USER_ID
    assert row["character_card_id"] == "default"


@pytest.mark.asyncio
async def test_bind_duplicate_same_user(app_with_db):
    """POST /api/wechat/bind — 同一用户重复绑定返回 201（更新信息）"""
    client, engine, _ = app_with_db

    # 第一次绑定
    r1 = await client.post("/api/wechat/bind", json={"wxid": "wx_dup", "nickname": "原始"})
    assert r1.status_code == 201

    # 第二次绑定（同一 wxid，同一 token，更新昵称）
    r2 = await client.post("/api/wechat/bind", json={"wxid": "wx_dup", "nickname": "更新昵称"})
    assert r2.status_code == 201
    assert r2.json()["binding"]["nickname"] == "更新昵称"

    # DB 验证：只有一条记录
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT COUNT(*) as cnt FROM wechat_bindings WHERE wxid = 'wx_dup'")
        )).mappings().one()
        assert rows["cnt"] == 1
    row = await _db_check_one(engine, "wx_dup")
    assert row["nickname"] == "更新昵称"


@pytest.mark.asyncio
async def test_bind_empty_wxid(app_with_db):
    """POST /api/wechat/bind — 空 wxid 返回 400"""
    client, _, _ = app_with_db
    resp = await client.post("/api/wechat/bind", json={"wxid": "", "nickname": "空"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bind_wxid_too_long(app_with_db):
    """POST /api/wechat/bind — 超长 wxid 返回 400"""
    client, _, _ = app_with_db
    resp = await client.post("/api/wechat/bind", json={
        "wxid": "x" * 300,
        "nickname": "超长",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_bindings(app_with_db):
    """GET /api/wechat/bindings — 列出当前用户的绑定"""
    client, _, _ = app_with_db

    # 建立两条绑定
    await client.post("/api/wechat/bind", json={"wxid": "wx_a", "nickname": "A"})
    await client.post("/api/wechat/bind", json={"wxid": "wx_b", "nickname": "B"})

    resp = await client.get("/api/wechat/bindings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    wxids = {b["wxid"] for b in body["bindings"]}
    assert wxids == {"wx_a", "wx_b"}


@pytest.mark.asyncio
async def test_update_binding_role(app_with_db):
    """PUT /api/wechat/bindings/{wxid} — 切换角色卡"""
    client, engine, _ = app_with_db

    # 先创建绑定
    await client.post("/api/wechat/bind", json={"wxid": "wx_role", "nickname": "原角色"})

    # 切换角色
    new_card = "e3f2a1b4-8c7d-4a5e-9b6c-1d2e3f4a5b6c"
    resp = await client.put("/api/wechat/bindings/wx_role", json={
        "character_card_id": new_card,
        "nickname": "新昵称",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "updated"
    assert body["binding"]["character_card_id"] == new_card
    assert body["binding"]["nickname"] == "新昵称"

    # DB 验证
    row = await _db_check_one(engine, "wx_role")
    assert row is not None
    assert row["character_card_id"] == new_card


@pytest.mark.asyncio
async def test_update_binding_not_found(app_with_db):
    """PUT /api/wechat/bindings/{wxid} — 不存在的 wxid 返回 404"""
    client, _, _ = app_with_db
    resp = await client.put("/api/wechat/bindings/wx_nonexist", json={
        "character_card_id": "some_card",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_binding(app_with_db):
    """DELETE /api/wechat/bindings/{wxid} — 正常解绑"""
    client, engine, _ = app_with_db

    # 先创建
    await client.post("/api/wechat/bind", json={"wxid": "wx_del", "nickname": "待删除"})

    # 删除
    resp = await client.delete("/api/wechat/bindings/wx_del")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unbound"

    # DB 验证：已被删除
    row = await _db_check_one(engine, "wx_del")
    assert row is None


@pytest.mark.asyncio
async def test_delete_binding_not_found(app_with_db):
    """DELETE /api/wechat/bindings/{wxid} — 不存在的 wxid 返回 404"""
    client, _, _ = app_with_db
    resp = await client.delete("/api/wechat/bindings/wx_nonexist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_other_user_cannot_claim(app_with_db):
    """POST /api/wechat/bind — 他人不能抢占已绑定的 wxid"""
    client, engine, app = app_with_db

    # 用户 42 绑定 wx_conflict
    await client.post("/api/wechat/bind", json={"wxid": "wx_conflict", "nickname": "用户42的"})

    # 模拟另一个用户（user_id=99）
    async def _other_user():
        return 99
    app.dependency_overrides[get_current_user_id] = _other_user

    resp = await client.post("/api/wechat/bind", json={"wxid": "wx_conflict", "nickname": "想抢"})
    assert resp.status_code == 409

    # 恢复
    app.dependency_overrides[get_current_user_id] = lambda: _MOCK_USER_ID
