"""P0 安全红测：微信通道槽位校验只拦正向上界、负数 slot 直通（审计 medium）。

缺陷（两轮安全审计确认，复核员实测复现）：
- ``wechat_direct/connector_registry.py::_pick_slot`` 对 prefer=-1 返回 -1
  （旧代码只判 ``prefer >= MAX_CHANNELS_PER_USER``，负向无界）；
- ``api/routers/wechat_channel_routes.py::ChannelConnectRequest.slot`` 无 ge/le 约束；
- ``ConnectorRegistry.start_login`` 在 slot 非 None 时 ``int(slot)`` 直通不经
  ``_pick_slot``（同时绕开负向与上界两向校验）。
⇒ 绕过「一人最多 2 条通道」硬限制，slot-1/slot-2 孤儿目录真实落盘
（admin 面 status_for_user / force-disconnect 均难处理）。

修复后口径（本文件全部用例）：
- slot<0 或 slot>=``channel_paths.MAX_CHANNELS_PER_USER``：
  pydantic 层 422 / registry 层 ``ChannelSlotError``，任何路径不再落盘孤儿目录；
- 正常 slot 0/1 与缺省自动分配不受影响（绿钉）。
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-channel-slot-sec-32chars-ok")


class _DummyConnector:
    """替身连接器：只验证「实例是否被创建/注册」，不跑真实登录循环。"""

    def __init__(self, user_manager=None, **kw):
        self.owner_user_id = kw.get("owner_user_id")
        self.slot = kw.get("slot", 0)
        self.token = ""
        self.bot_id = ""
        self.session_dir = kw.get("session_dir")

    def stop(self):
        pass


@pytest.fixture()
def sandbox_registry(monkeypatch, tmp_path):
    """隔离 sessions_root 到 tmp_path + 替身连接器；registry 用全新实例。"""
    import wechat_direct.channel_paths as channel_paths_mod
    from wechat_direct.connector_registry import ConnectorRegistry

    monkeypatch.setattr(
        channel_paths_mod, "sessions_root", lambda: tmp_path / "wechat_sessions"
    )
    monkeypatch.setattr("wechat_direct.wechat_connector.WeChatConnector", _DummyConnector)
    return ConnectorRegistry(), tmp_path / "wechat_sessions"


@pytest.fixture()
def api_client(monkeypatch, tmp_path):
    """JWT 鉴权的 /api/wechat/channel 子集：内存库 + 磁盘/单例全隔离。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import wechat_direct.channel_paths as channel_paths_mod
    from api.auth_jwt import create_access_token
    from api.database import Base, User, get_db
    from api.deps import deps
    from api.routers.wechat_channel_routes import router

    sessions_root = tmp_path / "wechat_sessions"
    monkeypatch.setattr(channel_paths_mod, "sessions_root", lambda: sessions_root)
    monkeypatch.setattr("wechat_direct.wechat_connector.WeChatConnector", _DummyConnector)
    # 单例重置：防止跨用例携带 slot 占用状态
    monkeypatch.setattr("wechat_direct.connector_registry._registry", None)

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id=2, email="u@t.com", username="u", hashed_password="x", role="viewer"))
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = _get_db

    class _GF:
        async def upsert_binding(self, *a, **k):
            return None

    monkeypatch.setattr(deps, "gf", _GF(), raising=False)

    token = create_access_token({"sub": "2", "type": "access"})
    return TestClient(app), {"Authorization": f"Bearer {token}"}, sessions_root


# ═══════════════ pydantic 层（ChannelConnectRequest.slot） ═══════════════


def test_connect_request_rejects_negative_slot():
    """修复前：slot=-1/-2 无约束直通 → 通道创建成功（红）；修复后 ValidationError。"""
    from pydantic import ValidationError

    from api.routers.wechat_channel_routes import ChannelConnectRequest

    for bad in (-1, -2, -100):
        with pytest.raises(ValidationError):
            ChannelConnectRequest(slot=bad)


def test_connect_request_upper_bound_uses_true_source_constant():
    """上界取真源常量（勿硬编码）：le=MAX_CHANNELS_PER_USER，超出即 422 级拒绝。"""
    from pydantic import ValidationError

    from api.routers.wechat_channel_routes import ChannelConnectRequest
    from wechat_direct import channel_paths

    max_per_user = channel_paths.MAX_CHANNELS_PER_USER
    with pytest.raises(ValidationError):
        ChannelConnectRequest(slot=max_per_user + 1)


def test_connect_request_accepts_valid_and_default():
    """绿钉：缺省 None 与合法 slot 0/1…MAX 不受影响。"""
    from api.routers.wechat_channel_routes import ChannelConnectRequest
    from wechat_direct import channel_paths

    assert ChannelConnectRequest().slot is None
    for ok in range(channel_paths.MAX_CHANNELS_PER_USER + 1):
        assert ChannelConnectRequest(slot=ok).slot == ok


# ═══════════════ registry 层（_pick_slot / ensure / start_login） ═══════════════


@pytest.mark.parametrize("bad_slot", [-1, -2, -100])
def test_pick_slot_rejects_negative(sandbox_registry, bad_slot):
    """修复前：_pick_slot(501, -1) 返回 -1（-1>=2 为假）→ 红；修复后 ChannelSlotError。"""
    from wechat_direct.connector_registry import ChannelSlotError

    reg, _root = sandbox_registry
    with pytest.raises(ChannelSlotError):
        reg._pick_slot(501, bad_slot)


def test_pick_slot_rejects_above_upper(sandbox_registry):
    """绿钉：正向上界校验（既有行为）不得被收口破坏。"""
    from wechat_direct import channel_paths
    from wechat_direct.connector_registry import ChannelSlotError

    reg, _root = sandbox_registry
    with pytest.raises(ChannelSlotError):
        reg._pick_slot(501, channel_paths.MAX_CHANNELS_PER_USER)


def test_pick_slot_valid_explicit_and_auto(sandbox_registry):
    """绿钉：显式合法槽位与缺省自动分配不受影响。"""
    reg, _root = sandbox_registry
    assert reg._pick_slot(511) == 0  # 缺省自动分配
    assert reg._pick_slot(511, None) == 0
    assert reg._pick_slot(512, 0) == 0  # 显式合法
    reg.ensure(513, slot=0)
    assert reg._pick_slot(513, None) == 1  # 已占 0 → 自动分配 1


def test_ensure_rejects_negative_slot_and_no_orphan_dir(sandbox_registry):
    """修复前：ensure(slot=-1) 创建连接器 + slot-1 目录落盘（红）。"""
    from wechat_direct.connector_registry import ChannelSlotError

    reg, root = sandbox_registry
    with pytest.raises(ChannelSlotError):
        reg.ensure(514, slot=-1)
    assert not (root / "514" / "slot-1").exists()  # 孤儿目录不得落盘


def test_start_login_rejects_negative_slot_and_no_orphan_dir(sandbox_registry):
    """修复前：start_login slot=-2 走 int(slot) 直通 → connecting + slot-2 落盘（红）。"""
    from wechat_direct.connector_registry import ChannelSlotError

    reg, root = sandbox_registry
    with pytest.raises(ChannelSlotError):
        reg.start_login(515, slot=-2, user_manager=None)
    assert not (root / "515" / "slot-2").exists()


def test_quota_cap_survives_slot_hardening(sandbox_registry):
    """绿钉：占满 0/1 后缺省自动分配仍被「一人最多 2 条」上限拦（429 语义源头）。"""
    from wechat_direct.connector_registry import ChannelSlotError

    reg, _root = sandbox_registry
    reg.ensure(516, slot=0)
    reg.ensure(516, slot=1)
    with pytest.raises(ChannelSlotError):
        reg._pick_slot(516, None)


# ═══════════════ API 层（/api/wechat/channel/connect） ═══════════════


def test_api_connect_negative_slot_rejected(api_client):
    """修复前：POST connect {"slot": -1} → 200 connecting + slot-1 孤儿目录（红）。"""
    _client, auth, root = api_client
    client: TestClient = _client
    r = client.post("/api/wechat/channel/connect", json={"slot": -1}, headers=auth)
    assert r.status_code in (400, 422)
    assert not (root / "2" / "slot-1").exists()


def test_api_connect_default_slot_unaffected(api_client):
    """绿钉：缺省自动分配 slot=0 正常。"""
    _client, auth, _root = api_client
    client: TestClient = _client
    r = client.post("/api/wechat/channel/connect", json={}, headers=auth)
    assert r.status_code == 200
    assert r.json()["slot"] == 0


def test_api_connect_valid_slots_0_and_1_unaffected(api_client):
    """绿钉：正常 slot 0/1 不受影响；占满后缺省自动分配 429（上限语义保留）。"""
    _client, auth, _root = api_client
    client: TestClient = _client
    r0 = client.post("/api/wechat/channel/connect", json={"slot": 0}, headers=auth)
    assert r0.status_code == 200
    assert r0.json()["slot"] == 0
    r1 = client.post("/api/wechat/channel/connect", json={"slot": 1}, headers=auth)
    assert r1.status_code == 200
    assert r1.json()["slot"] == 1
    r_full = client.post("/api/wechat/channel/connect", json={}, headers=auth)
    assert r_full.status_code == 429


def test_api_connect_slot_at_upper_bound_goes_429(api_client):
    """slot=MAX 过 pydantic（le=MAX）后由 registry 层拦为 429（既有分层语义）。"""
    _client, auth, _root = api_client
    client: TestClient = _client
    r = client.post("/api/wechat/channel/connect", json={"slot": 2}, headers=auth)
    assert r.status_code == 429
