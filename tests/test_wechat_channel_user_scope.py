"""微信通道**归属级**对抗测试（D16 ｜ 一人一 bot 一条通道）。

背景（本域取证结论，2026-10-30 批）：
- per-user 通道能力**早已存在且已接线** —— ``/api/wechat/channel/*``
  （``api/routers/wechat_channel_routes.py``），身份键一律由 token 派生
  （``Security(get_current_user_id)``），客户端自报归属不参与寻址。
- 遗留 ``/api/channels/wechat/{connect,disconnect,reconnect}``（``chat_routes.py``）
  是 **admin 兼容面**：实操对象是 ``slot=0`` 的**管理员自有**通道，
  故保留 ``require_role("admin")``。去 admin 门 = 让任意用户操作管理员通道，
  既不解决用户诉求，又与既有 user 端点构成第二套 owner（AGENTS §1.2）。

此前空白：``tests/test_sec_p0_auth.py`` 只钉了遗留**状态**端点的鉴权，
``tests/test_wechat_channel_api_auth.py`` 只钉了「状态查询按用户」——
**归属层**（A 能不能断开 B 的通道 / A 能不能拿到 B 的二维码 /
重连等待是否只盯 A 的槽位）从未被验证。

🔴 红测判据（突变即红）：把 handler 的 ``user_id`` 换成客户端可传参数
（body ``user_id`` 或 query ``user_id``）后，用例 ①②③ 必须变红 ——
断言的是 spy 收到的**身份实参**，不是状态码，也不是回执文案。
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-wx-channel-scope-32chars-ok")

ADMIN_ID = 1
USER_A = 2
USER_B = 3


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """最小 app：两个 user 面 + 遗留 admin 面；registry 全部换成 spy。

    - 磁盘通道根目录重定向到 empty ``tmp_path``（防御生产宿主真实在连状态，
      与 ``test_wechat_channel_api_auth.py:20-31`` 同一道防线）；
    - ``ConnectorRegistry`` 的三个方法打为 spy —— 真 ``disconnect`` 会写全局
      期望态并停真实连接器，测试只需记录**调用实参**。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import wechat_direct.channel_paths as channel_paths_mod
    from api.auth_jwt import create_access_token
    from api.database import Base, User, get_db
    from api.deps import deps
    from api.routers import chat_routes as chat_routes_mod
    from api.routers.wechat_channel_routes import admin_router, router
    from wechat_direct.connector_registry import ConnectorRegistry

    monkeypatch.setattr(channel_paths_mod, "sessions_root", lambda: tmp_path)

    calls: dict[str, list] = {"disconnect": [], "start_login": [], "status_for_user": []}

    def _spy_disconnect(self, user_id: int, slot: int = 0) -> bool:
        calls["disconnect"].append((int(user_id), int(slot)))
        return True

    def _spy_start_login(self, user_id: int, slot: int | None = None, user_manager=None):
        calls["start_login"].append((int(user_id), slot))
        return {"slot": int(slot) if slot is not None else 0, "status": "waiting_qr"}

    def _spy_status_for_user(self, user_id: int) -> list[dict]:
        calls["status_for_user"].append(int(user_id))
        return [
            {
                "connected": False,
                "status": "waiting_qr",
                "owner_user_id": int(user_id),
                "slot": 0,
                "bot_id": "",
                "uptime_seconds": 0,
                "messages_today": 0,
                "reconnect_attempts": 0,
            }
        ]

    monkeypatch.setattr(ConnectorRegistry, "disconnect", _spy_disconnect)
    monkeypatch.setattr(ConnectorRegistry, "start_login", _spy_start_login)
    monkeypatch.setattr(ConnectorRegistry, "status_for_user", _spy_status_for_user)

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id=ADMIN_ID, email="admin@t.com", username="admin",
                       hashed_password="x", role="admin"))
            s.add(User(id=USER_A, email="a@t.com", username="ua",
                       hashed_password="x", role="viewer"))
            s.add(User(id=USER_B, email="b@t.com", username="ub",
                       hashed_password="x", role="viewer"))
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    app.include_router(chat_routes_mod.router)
    app.dependency_overrides[get_db] = _get_db

    class _GF:
        async def upsert_binding(self, *a, **k):
            return None

    monkeypatch.setattr(deps, "gf", _GF())

    tokens = {
        "admin": create_access_token({"sub": str(ADMIN_ID), "type": "access"}),
        "a": create_access_token({"sub": str(USER_A), "type": "access"}),
        "b": create_access_token({"sub": str(USER_B), "type": "access"}),
    }
    client = TestClient(app)
    return client, tokens, calls


def _headers(tokens: dict[str, str], who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens[who]}"}


# ═══════════════════════════════════════════════════════
# ① 断开：A 只能断开 A 的通道（客户端自报他人的 user_id 必须被忽略）
# ═══════════════════════════════════════════════════════


def test_disconnect_targets_token_owner_not_client_supplied(env):
    client, tokens, calls = env
    r = client.post(
        "/api/wechat/channel/disconnect",
        json={"user_id": USER_B},  # 恶意/误传的他人归属 —— 必须无效
        headers=_headers(tokens, "a"),
    )
    assert r.status_code == 200, r.text
    assert calls["disconnect"] == [(USER_A, 0)], (
        f"断开目标必须是 token 所有者 A={USER_A}，实得 {calls['disconnect']}"
    )
    assert r.json()["owner_user_id"] == USER_A


# ═══════════════════════════════════════════════════════
# ② 二维码：只读 A 自己的 (user_id, slot)
# ═══════════════════════════════════════════════════════


def test_qrcode_reads_only_own_identity_and_slot(env, monkeypatch):
    client, tokens, _calls = env
    import wechat_direct.wechat_connector as connector_mod

    seen: list[tuple[int, int]] = []

    def _spy_load(user_id: int, slot: int = 0) -> dict:
        seen.append((int(user_id), int(slot)))
        return {"status": "waiting_qr", "qrcode_url": "", "timestamp": 0}

    monkeypatch.setattr(connector_mod, "load_session_qrcode", _spy_load)

    r = client.get(
        "/api/wechat/channel/qrcode?user_id=3&slot=1",  # 客户端自报的归属与槽位均须无效
        headers=_headers(tokens, "a"),
    )
    assert r.status_code == 200, r.text
    assert seen == [(USER_A, 0)], f"二维码读面必须只读 A 的 (id, slot)，实得 {seen}"
    body = r.json()
    assert body["owner_user_id"] == USER_A, "回执不得回显他人归属"


# ═══════════════════════════════════════════════════════
# ③ 重连：让位等待只盯 A 的槽位，重连目标也是 A
# ═══════════════════════════════════════════════════════


def test_reconnect_only_touches_own_slot(env, monkeypatch):
    client, tokens, calls = env
    import proactive.runtime_plane as runtime_plane

    presence: list[tuple[int, int]] = []
    monkeypatch.setattr(
        runtime_plane,
        "presence_live",
        lambda uid, slot=0: (presence.append((int(uid), int(slot))) or False),
    )

    r = client.post(
        "/api/wechat/channel/reconnect",
        json={"user_id": USER_B, "slot": 0},
        headers=_headers(tokens, "a"),
    )
    assert r.status_code == 200, r.text
    assert calls["disconnect"] == [(USER_A, 0)], calls["disconnect"]
    assert presence, "重连必须真实进入让位等待（否则用例没测到那一层）"
    assert all(pair == (USER_A, 0) for pair in presence), (
        f"让位等待只允许盯 A 的槽位，实得 {presence}"
    )
    assert calls["start_login"] == [(USER_A, 0)], calls["start_login"]


# ═══════════════════════════════════════════════════════
# ④ 遗留 admin 兼容面：viewer 一律 403（SEC-P0 回归），admin 保留
# ═══════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "path",
    [
        "/api/channels/wechat/connect",
        "/api/channels/wechat/disconnect",
        "/api/channels/wechat/reconnect",
    ],
)
def test_legacy_channel_endpoints_forbid_viewer(env, path):
    client, tokens, calls = env
    r = client.post(path, headers=_headers(tokens, "a"))
    assert r.status_code == 403, f"{path} 普通用户必须 403，实得 {r.status_code}"
    # 门在依赖层就拒绝 —— 不得先动到任何通道资源
    assert calls["start_login"] == [], f"{path} 越权请求不得触达 start_login"
    assert calls["disconnect"] == [], f"{path} 越权请求不得触达 disconnect"


def test_legacy_channel_endpoints_still_admin_only(env):
    """admin 分支保留：三条路径均 200，且目标恒为**管理员自有** slot=0。"""
    client, tokens, calls = env
    for path in (
        "/api/channels/wechat/connect",
        "/api/channels/wechat/disconnect",
        "/api/channels/wechat/reconnect",
    ):
        r = client.post(path, headers=_headers(tokens, "admin"))
        assert r.status_code == 200, f"{path} admin 实得 {r.status_code}: {r.text}"

    assert calls["start_login"] and all(uid == ADMIN_ID for uid, _s in calls["start_login"])
    # connect → start_login(admin, slot=0) 一次；reconnect 只清凭证+disconnect，不重新发起登录
    assert calls["start_login"] == [(ADMIN_ID, 0)], calls["start_login"]
    # disconnect 端点 + reconnect 各一次，目标恒为管理员自有 slot=0
    assert calls["disconnect"] == [(ADMIN_ID, 0), (ADMIN_ID, 0)], calls["disconnect"]
