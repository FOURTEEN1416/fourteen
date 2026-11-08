"""微信绑定写侧根修回归（2026-11-08 · 先红后绿）。

本轮**不改任何被测代码**：除第 4 条（「不猜键」底线的绿守卫）外，用例在
当前 HEAD 上应当**红**，修复后全绿。

根因（已复核的现状坐标）：
- `my-settings` 等 15 处调用点共用
  `api/routers/training_routes.py::_user_session_key_for`（572 行），
  它**只查** `wechat_bindings`（表定义 `api/database.py::WechatBinding` 257 行起）；
- 该表只有 `/api/characters/{id}/activate`（`api/routers/character_routes.py`
  938-1005 行；实测 953/964 行经 `wechat_direct/channel_paths.py::scanner_ids_for_user`
  即 75 行）与遗留 `POST /api/wechat/bind` 会写入；
- **扫码登录路径**（`wechat_direct/wechat_connector.py::login` 的
  `status == "confirmed"` 分支，1480 行起）与注册分发都不写——该文件全文只有
  1 处 `bind` 字面量且是注释（1846 行）；
- 于是「先网页激活角色 → 后扫码登录」的用户永远没有绑定行，
  `GET /api/characters/{id}/my-settings` 返回 409「尚未绑定微信」
  （`api/routers/character_settings_routes.py` 68-74 / 106 / 146 行），
  与 /wechat 页「通道已连接」的状态直接冲突；
- 身份组单一真源：`shisi/memory/legacy/structured_memory.py::identity_keys`
  （574 行；`user_identity_map` 建表在 191 行）。

本文件钉死四条契约：
1. 扫码登录成功必须为 owner 补写 `wechat_bindings` 行（wxid = 登录返回的
   `ilink_user_id`、`character_card_id` = 该用户 `user_active_characters` 现值），
   且重复扫码登录幂等（同一 wxid 不得出现第二行）。
2. 仅有扫码登录产生的绑定行（从未走过 activate 链）时，
   `GET /api/characters/{id}/my-settings` 必须 200 而非 409。
3. 读侧兜底：`wechat_bindings` 直查为空时，`_user_session_key_for` 必须经身份组
   单一真源 `StructuredMemory.identity_keys()` 解析——该用户在
   `user_identity_map` 里的键（`{uid}:{wxid}` 形态）所属组内若有成员键带绑定行，
   则返回**那个组内会话键**（找到组、再查绑定；不拼假键）。
4. 全无绑定且身份组内也无绑定行时仍返回空串（D15 不猜键底线不变）。

测试库隔离（`users_db` 夹具）：users 主库整体重定向到 per-test 临时库，并同时
改写三条入口，避免实现方走任一路径落进真实 `data/users.db`：
- `APP_DATABASE_URL` —— `UserManager._resolve_users_db_path()` 的同步 sqlite 口径；
- `api.database.DATABASE_URL` / `_engine` / `_async_session` —— 异步会话口径。
写入后用同步 sqlite3 读回事实（同一物理文件），不依赖事件循环状态；
引擎用 `NullPool`，避免跨 `asyncio.run` 复用连接时的
「attached to a different loop」假红。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault(
    "JWT_SECRET", "test-secret-for-wechat-binding-rootfix-32ch"
)

# 主用例用户（先激活后扫码）：扫码登录必须给他补写绑定行
USER_SCAN = 90801
# 身份组兜底：他本人在组里的键没有绑定行，组内另一成员有
USER_GROUP = 90802
USER_GROUP_BOUND = 90803
# 「不猜键」守卫：全无绑定、也不在身份组
USER_NOBODY = 90804

SCAN_WXID = "wx_scan_90801@im.wechat"
ACTIVE_CARD = "card_scan_active_001"
PASSWORD = "Passw0rd!123"


# ── 夹具与工具 ────────────────────────────────────────────


@pytest.fixture()
def users_db(tmp_path, monkeypatch):
    """per-test users 主库（异步会话 + 同步 sqlite 双口径一致）。"""
    import api.database as dbmod

    db_path = tmp_path / "users_binding.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("APP_DATABASE_URL", url)  # 同步路径真源（调用时读环境）
    monkeypatch.setattr(dbmod, "DATABASE_URL", url, raising=False)
    monkeypatch.setattr(dbmod, "_engine", engine, raising=False)
    monkeypatch.setattr(dbmod, "_async_session", session_factory, raising=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(dbmod.Base.metadata.create_all)

    asyncio.run(_init())
    yield db_path, engine, session_factory
    asyncio.run(engine.dispose())


async def _seed(
    session_factory,
    *,
    uid: int,
    username: str,
    active_card: str | None = None,
    binding_wxid: str | None = None,
) -> None:
    """种一个注册用户（可选个人激活卡 / 既有绑定行）。"""
    from api.auth_jwt import hash_password
    from api.database import User, UserActiveCharacter, WechatBinding

    async with session_factory() as session:
        session.add(
            User(
                id=uid,
                email=f"{username}@binding.test",
                username=username,
                hashed_password=hash_password(PASSWORD),
                display_name=username,
                role="viewer",
                is_active=True,
                is_verified=True,
            )
        )
        if active_card:
            session.add(UserActiveCharacter(user_id=uid, character_id=active_card))
        if binding_wxid:
            session.add(
                WechatBinding(
                    user_id=uid, wxid=binding_wxid, character_card_id="default"
                )
            )
        await session.commit()


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """同步 sqlite3 读回事实（与异步写共用同一物理文件）。"""
    with closing(sqlite3.connect(str(db_path), timeout=10.0)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _binding_rows(db_path: Path, uid: int) -> list[dict]:
    return _rows(
        db_path,
        "SELECT wxid, user_id, character_card_id FROM wechat_bindings "
        "WHERE user_id = ? ORDER BY id",
        (uid,),
    )


def _login_with_scan(
    owner_user_id: int,
    ilink_user_id: str,
    *,
    drop_credentials: bool = False,
):
    """跑一遍真实扫码登录（只打桩微信 HTTP，不碰 DB 写入逻辑）。

    `drop_credentials=True`：先把上一轮落盘的凭证删掉，模拟「重新扫码」，
    确保第二次调用仍走 `status == "confirmed"` 分支（而不是凭证快速重连，
    那条路径今天不承担绑定补写）。
    """
    from wechat_direct import channel_paths
    from wechat_direct import wechat_connector as wc

    # 用**通道真源路径**（conftest 已把 sessions_root 沙箱化）：这样无论
    # 实现方用 `self.scanner_wxid` 还是回头 `scanner_ids_for_user(owner)`
    # 读凭证，看到的都是同一份刚落盘的 credentials.json。
    session_dir = channel_paths.ensure_session_dir(owner_user_id, 0)
    if drop_credentials:
        creds = channel_paths.credentials_path(owner_user_id, 0)
        if creds.exists():
            creds.unlink()

    conn = wc.WeChatConnector(
        MagicMock(), owner_user_id=owner_user_id, slot=0, session_dir=session_dir
    )
    confirmed = {
        "status": "confirmed",
        "bot_token": "bot_token_for_test",
        "ilink_bot_id": "bot_id_for_test",
        "baseurl": wc.DEFAULT_BASE_URL,
        "ilink_user_id": ilink_user_id,
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc,
            "_fetch_qr_code",
            lambda *a, **k: {"qrcode": "q", "qrcode_img_content": "img"},
        )
        mp.setattr(wc, "_poll_qr_status", lambda *a, **k: confirmed)
        mp.setattr(wc, "_get_updates", lambda *a, **k: {"ret": 0, "msgs": []})
        ok = conn.login()
    assert ok is True, "前置：扫码登录本身必须成功"
    assert conn.scanner_wxid == ilink_user_id, "前置：登录确认必须装载扫码人 id"
    # 凭证确实落盘（重启可恢复），与运营面「已连接」口径一致
    saved = json.loads((session_dir / "credentials.json").read_text(encoding="utf-8"))
    assert saved["user_id"] == ilink_user_id
    return conn


def _build_client(session_factory):
    """按 per-test users 库建 app；不触发 lifespan。"""
    from api.app_factory import create_api_app
    from api.auth import configure_auth
    from api.database import get_db

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login_token(client: AsyncClient, username: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"login": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


# ── 契约 1：扫码登录补写绑定行 ────────────────────────────


def test_scan_login_writes_wechat_binding(users_db):
    """扫码登录成功 → 为 owner 补写 wechat_bindings（挂当前激活角色）。

    今天：表里一行都没有（只有 activate 链写表）→ 红。
    """
    db_path, _engine, session_factory = users_db
    asyncio.run(
        _seed(
            session_factory,
            uid=USER_SCAN,
            username="binding_scan",
            active_card=ACTIVE_CARD,
        )
    )
    assert _binding_rows(db_path, USER_SCAN) == [], "前置：种用户不带绑定行"

    _login_with_scan(USER_SCAN, SCAN_WXID)

    rows = _binding_rows(db_path, USER_SCAN)
    assert len(rows) == 1, f"扫码登录必须补写且只写一行绑定，实得 {rows}"
    assert rows[0]["wxid"] == SCAN_WXID, "wxid 必须取登录确认返回的扫码人 id"
    assert rows[0]["user_id"] == USER_SCAN, "绑定行必须挂登录 owner（不猜别人）"
    assert rows[0]["character_card_id"] == ACTIVE_CARD, (
        "绑定必须挂该用户当前激活角色（user_active_characters 现值），"
        "不得回落全局 default"
    )


def test_scan_login_relogin_is_idempotent(users_db):
    """重复扫码登录不得产生第二行（wxid 唯一），也不得改写激活角色。"""
    db_path, _engine, session_factory = users_db
    asyncio.run(
        _seed(
            session_factory,
            uid=USER_SCAN,
            username="binding_scan",
            active_card=ACTIVE_CARD,
        )
    )

    _login_with_scan(USER_SCAN, SCAN_WXID)
    _login_with_scan(USER_SCAN, SCAN_WXID, drop_credentials=True)

    rows = _binding_rows(db_path, USER_SCAN)
    assert len(rows) == 1, f"重复登录必须幂等（同 wxid 一行），实得 {rows}"
    assert rows[0]["wxid"] == SCAN_WXID
    assert rows[0]["character_card_id"] == ACTIVE_CARD


# ── 契约 2：只有扫码绑定行 → my-settings 200 ──────────────


def test_my_settings_ok_with_scan_binding_only(users_db):
    """未走过 activate、仅扫码登录 → my-settings 必须 200（今天 409）。

    与 /wechat 页「通道已连接」状态对齐：通道连上就是有会话键。
    """
    db_path, _engine, session_factory = users_db
    asyncio.run(_seed(session_factory, uid=USER_SCAN, username="binding_scan"))

    _login_with_scan(USER_SCAN, SCAN_WXID)
    rows = _binding_rows(db_path, USER_SCAN)
    assert len(rows) == 1, (
        "前置：绑定行必须由扫码登录产生（本轮未调用 activate），"
        f"实得 {rows}"
    )

    client = _build_client(session_factory)

    async def _run() -> None:
        try:
            token = await _login_token(client, "binding_scan")
            resp = await client.get(
                "/api/characters/default/my-settings",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, (
                "仅扫码绑定（未经 activate）也必须能定出会话键；"
                f"实得 {resp.status_code} {resp.text}"
            )
            body = resp.json()
            assert body["status"] == "ok"
            assert body["character_id"] == "default"
            assert body["scope"] == "user_character"
            assert "config" in body
        finally:
            await client.aclose()

    asyncio.run(_run())


# ── 契约 3：读侧身份组兜底 ────────────────────────────────


def test_user_session_key_falls_back_to_identity_group(users_db):
    """wechat_bindings 直查为空 → 经 identity_keys() 组内成员绑定解析。

    场景：USER_GROUP 本人在 `user_identity_map` 里的键
    `{uid}:wx_group_self@im.wechat` **没有**绑定行；该键与
    `{peer}:wx_peer@im.wechat` 同组，而后者**有**绑定行。
    期望解析到那个组内会话键（找到组、再查绑定；绝不拼假键）。

    今天：只查 wechat_bindings → 返回 "" → 红。
    """
    from shisi.memory.legacy.structured_memory import StructuredMemory

    db_path, _engine, session_factory = users_db
    asyncio.run(_seed(session_factory, uid=USER_GROUP, username="binding_group"))
    asyncio.run(
        _seed(
            session_factory,
            uid=USER_GROUP_BOUND,
            username="binding_group_peer",
            binding_wxid="wx_peer@im.wechat",
        )
    )
    assert _binding_rows(db_path, USER_GROUP) == [], "前置：本用户直查无绑定行"

    group_self_key = f"{USER_GROUP}:wx_group_self@im.wechat"
    peer_key = f"{USER_GROUP_BOUND}:wx_peer@im.wechat"

    # 身份组真源 = StructuredMemory 模块默认库（conftest 已沙箱化）；
    # 实现侧读身份组必须走同一真源（identity_keys 单一真源）。
    memory = StructuredMemory()
    try:
        members = memory.link_identities(group_self_key, peer_key)
        assert sorted(members) == sorted([group_self_key, peer_key])

        async def _run() -> str:
            from api.routers.training_routes import _user_session_key_for

            async with session_factory() as session:
                return await _user_session_key_for(USER_GROUP, session)

        got = asyncio.run(_run())
    finally:
        memory.close()

    assert got == peer_key, (
        "直查无绑定时应回落到身份组内**有绑定行**的会话键；"
        f"实得 {got!r}（组键 {group_self_key!r} / {peer_key!r}）"
    )


# ── 契约 4：不猜键底线（绿守卫） ──────────────────────────


def test_no_binding_no_identity_group_returns_empty(users_db):
    """全无绑定、身份组内也无绑定行 → 仍返回空串（D15 不猜键）。"""
    from shisi.memory.legacy.structured_memory import StructuredMemory

    db_path, _engine, session_factory = users_db
    asyncio.run(_seed(session_factory, uid=USER_NOBODY, username="binding_nobody"))
    asyncio.run(
        _seed(session_factory, uid=USER_GROUP, username="binding_group_nobind")
    )
    assert _binding_rows(db_path, USER_NOBODY) == []

    memory = StructuredMemory()
    try:
        # 组存在但两个成员都没有绑定行 → 找到组也不能编键
        memory.link_identities(
            f"{USER_NOBODY}:wx_nobody_a@im.wechat",
            f"{USER_GROUP}:wx_nobody_b@im.wechat",
        )

        async def _run() -> str:
            from api.routers.training_routes import _user_session_key_for

            async with session_factory() as session:
                return await _user_session_key_for(USER_NOBODY, session)

        got = asyncio.run(_run())
    finally:
        memory.close()

    assert got == "", f"没有任何绑定行时不得猜键，实得 {got!r}"


# 说明：契约 1 的登录补写若实现为异步会话，可能读不到本进程 `_engine` 的补丁
# （例如在模块顶层 `from api.database import _async_session` 冻结了引用）。
# 那时请改为函数内 late import（本仓既有风格，如 `character_routes` 内
# `from wechat_direct.channel_paths import ...`），或走
# `UserManager._resolve_users_db_path()` 的同步 sqlite 口径 —— 两条入口本夹具
# 都已重定向。测试不替你选实现，只钉住可观测事实。
