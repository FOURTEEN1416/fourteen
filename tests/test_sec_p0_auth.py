"""P0 修复批 · 认证收口域（F1-F6）— 红测先行。

任务书六项对应的验收面：
- F1 api/auth.verify_api_key_dep：JWT 验签之外必须追加主体校验
  （存在 + is_active + 撤销版本）；已停用/已删除/已改密账号的旧 token
  落入 API Key 分支或 401，有效且启用用户的 access JWT 继续放行。
- F2 api/auth.py 移除 query `?api_key=` 传 key 通道（header 通道保留）。
- F3 登录 + register-invite 专属严格限速（5 失败/分/IP）+ 账号级失败锁定
  （同 email/username 连续 5 次失败锁 15 分钟，锁定窗口内 401 通用文案）。
- F4 邀请码存在性 oracle 消除：撤销/已用/过期统一「邀请码无效」。
- F5 invite_routes 手工 token dict 改 token_claims(user)（含 tv、不含 role）。
- F6 chat_routes._try_user_id 只验签的 W1 旁路：三个消费端点改走
  get_optional_principal（含 is_active + tv 校验）。

隔离纪律：sqlite 引擎逐用例独立（tmp_path）且 teardown dispose，
防爆破账本逐用例清零，注册分发角色卡目录钉临时目录——
零接触真实 data/ 与 config/characters/。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AI_GF_ENV", "dev")

import pytest
from fastapi import FastAPI, Security
from httpx import ASGITransport, AsyncClient
from jose import jwt as _jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth_jwt import (
    JWT_SECRET,
    create_access_token,
    hash_password,
    token_claims,
)
from api.database import Base, InviteCode, User, get_db

_MACHINE_KEY = "sec-p0-machine-api-key-32chars-minimum-ok!"
_PASSWORD = "Passw0rd!123"
_LOGIN_GENERIC = "Invalid login credentials"


# ═══════════════════════════════════════════════════════
# 夹具与工具
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_bf_state():
    """防爆破账本（api.auth 模块级全局）逐用例前后清零，防跨用例污染。

    修复前这些属性不存在 → getattr 返回 None → 静默跳过（红测期同样安全）。
    """
    from api import auth as auth_mod

    def _clear() -> None:
        for name in ("_bf_ip_hits", "_bf_account_fails", "_bf_account_locked_until"):
            box = getattr(auth_mod, name, None)
            if isinstance(box, dict):
                box.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _sandbox_characters_dir(tmp_path_factory):
    """注册分发钩子（W12）会把初始角色卡写进 CHARACTERS_DIR——钉到临时目录。

    与 tests/test_invite_codes.py 同纪律：不钉则每轮回归向真实
    config/characters/ 净增克隆卡（gitignored 不可见）。
    """
    from api.routers import character_routes

    sandbox = tmp_path_factory.mktemp("secp0-characters")
    original = character_routes.CHARACTERS_DIR
    character_routes.CHARACTERS_DIR = sandbox
    yield
    character_routes.CHARACTERS_DIR = original


@pytest.fixture
def auth_on():
    """显式启用机器认证；用例结束由 conftest 的 reset_auth_state_each 复位。"""
    from api.auth import configure_auth

    configure_auth(True, _MACHINE_KEY)
    return _MACHINE_KEY


@pytest.fixture
async def db_pair(tmp_path):
    """逐用例独立 sqlite 引擎 + session 工厂；teardown dispose（Windows 文件锁）。"""
    db_path = tmp_path / "sec_p0_auth.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


@pytest.fixture
async def seeded_db(db_pair):
    """建表 + 种入 alice(id=1, viewer, active)，返回 session 工厂。"""
    engine, factory = db_pair
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(
            User(
                id=1,
                email="alice@sec.test",
                username="alice",
                hashed_password=hash_password(_PASSWORD),
                display_name="alice",
                role="viewer",
                is_active=True,
                is_verified=True,
                token_version=0,
            )
        )
        await db.commit()
    return factory


async def _create_tables(factory) -> None:
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _seed_invite(factory, code: str, **extra) -> None:
    now = datetime.now(timezone.utc)
    fields: dict = {
        "created_by": 1,
        "created_at": now,
        "expires_at": now + timedelta(days=30),
    }
    fields.update(extra)
    async with factory() as db:
        db.add(InviteCode(code=code, **fields))
        await db.commit()


def _bare_app(factory) -> FastAPI:
    """只挂 /sec-probe 探针的最小 app：直测 verify_api_key_dep 依赖契约。"""
    from api.auth import verify_api_key_dep

    async def _db():
        async with factory() as session:
            yield session

    app = FastAPI()

    @app.get("/sec-probe")
    def probe(_ok: bool = Security(verify_api_key_dep)):
        return {"ok": True}

    app.dependency_overrides[get_db] = _db
    return app


def _full_app(factory):
    """完整 create_api_app + get_db 沙箱覆盖（真实登录/注册/通道路由）。"""

    async def _db():
        async with factory() as session:
            yield session

    from api.app_factory import create_api_app

    app = create_api_app()
    app.dependency_overrides[get_db] = _db
    return app


def _client(app, client_addr: tuple[str, int] | None = None) -> AsyncClient:
    """ASGI 客户端；client_addr 经 ASGITransport 官方参数注入 scope["client"]。

    2026-10-09 手法迁移（auth-bruteforce 修复批，EXT-1）：防爆破限速键修复后
    只信 ASGI 直连地址——旧以 X-Forwarded-For 请求头模拟多攻击者 IP 的手法
    随之失效（该头正是被修复的伪造通道），改由 scope client 注入来源地址，
    被测防爆破语义不变。无参调用与旧行为完全一致（默认 127.0.0.1）。
    """
    transport = ASGITransport(app=app, client=client_addr or ("127.0.0.1", 123))
    return AsyncClient(transport=transport, base_url="http://test", timeout=15.0)


# ═══════════════════════════════════════════════════════
# F1 — verify_api_key_dep JWT 分支主体校验
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f1_valid_enabled_user_jwt_still_passes(seeded_db, auth_on):
    """语义不变量：有效且启用用户的 access JWT 继续放行。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_f1_disabled_account_jwt_rejected(seeded_db, auth_on):
    """已停用账号的旧 token 不得借验签放行 → 401（修复前 200，红）。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))
        user.is_active = False
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"停用账号 token 实得 {r.status_code}，必须 401"


@pytest.mark.asyncio
async def test_f1_disabled_account_legacy_token_without_tv_rejected(seeded_db, auth_on):
    """旧形态 token（无 tv 声明）+ 停用账号 → 401（修复前 200，红）。"""
    factory = seeded_db
    token = create_access_token({"sub": "1"})  # 旧签名时代的典型 token 形态
    async with factory() as db:
        user = await db.get(User, 1)
        user.is_active = False
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"停用账号旧形态 token 实得 {r.status_code}，必须 401"


@pytest.mark.asyncio
async def test_f1_deleted_account_jwt_rejected(seeded_db, auth_on):
    """已删除账号（主体查不到）的 token → 401（修复前 200，红）。"""
    factory = seeded_db
    token = create_access_token({"sub": "1"})
    async with factory() as db:
        user = await db.get(User, 1)
        await db.delete(user)
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"已删除账号 token 实得 {r.status_code}，必须 401"


@pytest.mark.asyncio
async def test_f1_token_version_bumped_jwt_rejected(seeded_db, auth_on):
    """改密/管理员重置后（tv 自增）的旧 token → 401（修复前 200，红）。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))  # tv=0
        user.token_version = 1
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401, f"撤销版本过期 token 实得 {r.status_code}，必须 401"


@pytest.mark.asyncio
async def test_f1_dead_token_plus_valid_machine_key_still_passes(seeded_db, auth_on):
    """语义不变量：死主体 token 落入 API Key 分支——有效机器 key 仍放行。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))
        user.is_active = False
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get(
            "/sec-probe",
            headers={"Authorization": f"Bearer {token}", "X-API-Key": _MACHINE_KEY},
        )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_f1_dead_token_falls_to_apikey_branch_when_auth_disabled(seeded_db):
    """语义不变量：认证未启用时，死主体 token 落入 API Key 分支 → 分支放行。"""
    from api.auth import configure_auth

    configure_auth(False, "")
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))
        user.is_active = False
        await db.commit()

    async with _client(_bare_app(factory)) as c:
        r = await c.get("/sec-probe", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════
# F2 — query `?api_key=` 通道撤除
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f2_query_api_key_channel_removed(db_pair, auth_on):
    """query 传 key 必须失效 → 401（修复前 200，红）。探针不带 Bearer，无需建表。"""
    _, factory = db_pair
    app = _bare_app(factory)
    async with _client(app) as c:
        r = await c.get(f"/sec-probe?api_key={_MACHINE_KEY}")
    assert r.status_code == 401, f"query 通道仍可用，实得 {r.status_code}"


@pytest.mark.asyncio
async def test_f2_header_api_key_channel_kept(db_pair, auth_on):
    """语义不变量：X-API-Key header 通道不受影响。"""
    _, factory = db_pair
    app = _bare_app(factory)
    async with _client(app) as c:
        r = await c.get("/sec-probe", headers={"X-API-Key": _MACHINE_KEY})
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════
# F3 — 登录 / register-invite 防爆破
# ═══════════════════════════════════════════════════════


async def _login(app, login: str, password: str, ip: str):
    """ip 经 scope client 注入（auth-bruteforce 修复批后 XFF 头不再是限速键）。"""
    async with _client(app, (ip, 123)) as c:
        return await c.post(
            "/api/auth/login",
            json={"login": login, "password": password},
        )


async def _register_invite(
    app,
    *,
    code: str,
    email: str,
    username: str,
    ip: str,
    password: str = "Str0ngPass!2026",
):
    async with _client(app, (ip, 123)) as c:
        return await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": code,
                "email": email,
                "username": username,
                "password": password,
            },
        )


@pytest.mark.asyncio
async def test_f3_login_ip_rate_limit_5_failures_per_min(seeded_db, auth_on):
    """同一 IP 连续 5 次登录失败后，第 6 次（同 IP）→ 429（修复前恒 401，红）。"""
    factory = seeded_db
    app = _full_app(factory)
    bad_ip = "203.0.113.10"
    async with _client(app):
        for i in range(5):
            r = await _login(app, "alice", "definitely-wrong", bad_ip)
            assert r.status_code == 401, f"前 5 次应为普通失败，第 {i + 1} 次实得 {r.status_code}"
        sixth = await _login(app, "alice", "definitely-wrong", bad_ip)
        assert sixth.status_code == 429, f"同 IP 第 6 次失败应 429，实得 {sixth.status_code}"
        # 其他 IP 不受该 IP 窗口影响
        other = await _login(app, "alice", "definitely-wrong", "203.0.113.99")
        assert other.status_code == 401, f"其他 IP 应仍为普通 401，实得 {other.status_code}"


@pytest.mark.asyncio
async def test_f3_login_account_lockout_after_5_consecutive_failures(seeded_db, auth_on):
    """5 个不同 IP 各失败一次后账号锁定：正确密码也 401 通用文案（修复前 200，红）。"""
    factory = seeded_db
    app = _full_app(factory)
    async with _client(app):
        for i in range(5):
            r = await _login(app, "alice", "definitely-wrong", f"198.51.100.{i}")
            assert r.status_code == 401, r.text
        # 换全新 IP + 正确密码：账号锁优先 → 401
        locked = await _login(app, "alice", _PASSWORD, "198.51.100.200")
        assert locked.status_code == 401, f"锁定窗口内正确密码应 401，实得 {locked.status_code}"
        assert locked.json()["detail"] == _LOGIN_GENERIC, (
            "锁定文案必须与普通失败完全一致（不泄露锁定状态）"
        )


@pytest.mark.asyncio
async def test_f3_login_success_resets_failure_counter(seeded_db, auth_on):
    """成功登录清空失败账：4 败 1 成交错出现永不锁定。"""
    factory = seeded_db
    app = _full_app(factory)
    async with _client(app):
        for round_no in range(2):
            for _ in range(4):
                r = await _login(app, "alice", "definitely-wrong", f"198.51.101.{round_no}")
                assert r.status_code == 401, r.text
            ok = await _login(app, "alice", _PASSWORD, f"198.51.101.{round_no}")
            assert ok.status_code == 200, f"第 {round_no + 1} 轮正确登录被误拒: {ok.text}"


@pytest.mark.asyncio
async def test_f3_lockout_expires_after_15_minutes(seeded_db, auth_on, monkeypatch):
    """锁定 15 分钟后自动解除：时钟推进 901s 后正确登录恢复 200。"""
    from api import auth as auth_mod

    factory = seeded_db
    app = _full_app(factory)
    async with _client(app):
        for i in range(5):
            await _login(app, "alice", "definitely-wrong", f"198.51.102.{i}")
        locked = await _login(app, "alice", _PASSWORD, "198.51.102.200")
        assert locked.status_code == 401, "锁定应先生效（前置条件）"

        real_now = auth_mod._bf_now
        monkeypatch.setattr(auth_mod, "_bf_now", lambda: real_now() + 901.0)
        released = await _login(app, "alice", _PASSWORD, "198.51.102.201")
        assert released.status_code == 200, f"锁定到期后应恢复登录，实得 {released.status_code}"


@pytest.mark.asyncio
async def test_f3_register_invite_ip_rate_limit(db_pair, auth_on):
    """register-invite 同 IP 5 次失败（无效码）后第 6 次 → 429（修复前恒 400，红）。"""
    _, factory = db_pair
    await _create_tables(factory)

    app = _full_app(factory)
    bad_ip = "203.0.113.50"
    async with _client(app):
        for i in range(5):
            r = await _register_invite(
                app,
                code=f"badcode{i}",
                email=f"probe{i}@sec.test",
                username=f"probe{i}",
                ip=bad_ip,
            )
            assert r.status_code == 400, f"第 {i + 1} 次应为普通 400，实得 {r.status_code}"
        sixth = await _register_invite(
            app,
            code="badcode5",
            email="probe5@sec.test",
            username="probe5",
            ip=bad_ip,
        )
        assert sixth.status_code == 429, f"同 IP 第 6 次应 429，实得 {sixth.status_code}"


@pytest.mark.asyncio
async def test_f3_register_invite_account_lockout(db_pair, auth_on):
    """同 email 连续 5 次注册失败后，持**有效**邀请码的该邮箱注册也被锁 → 401（红）。"""
    _, factory = db_pair
    await _create_tables(factory)
    await _seed_invite(factory, "goodcode1")

    app = _full_app(factory)
    victim_email = "victim@sec.test"
    async with _client(app):
        for i in range(5):
            r = await _register_invite(
                app,
                code=f"badcode{i}",
                email=victim_email,
                username=f"victim{i}",
                ip=f"198.51.103.{i}",
            )
            assert r.status_code == 400, r.text
        # 有效邀请码 + 被锁邮箱 + 全新 IP → 401（且不消耗邀请码）
        locked = await _register_invite(
            app,
            code="goodcode1",
            email=victim_email,
            username="victim5",
            ip="198.51.103.200",
        )
        assert locked.status_code == 401, (
            f"锁定窗口内有效邀请码也应 401，实得 {locked.status_code}"
        )
        async with factory() as db:
            invite = (
                await db.execute(select(InviteCode).where(InviteCode.code == "goodcode1"))
            ).scalar_one_or_none()
            assert invite is not None and invite.used_by is None


# ═══════════════════════════════════════════════════════
# F4 — 邀请码存在性 oracle 消除
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f4_invite_state_oracle_unified(db_pair, auth_on):
    """不存在/已用/已撤销/已过期四种失效一律同一文案「邀请码无效」（修复前可区分，红）。"""
    _, factory = db_pair
    await _create_tables(factory)
    now = datetime.now(timezone.utc)
    await _seed_invite(factory, "usedone", used_by=999, used_at=now)
    await _seed_invite(factory, "revokedone", is_revoked=True)
    await _seed_invite(factory, "expiredone", expires_at=now - timedelta(days=1))

    app = _full_app(factory)
    async with _client(app):
        details: dict[str, str] = {}
        for code in ("nonexist1", "usedone", "revokedone", "expiredone"):
            r = await _register_invite(
                app,
                code=code,
                email=f"{code}@sec.test",
                username=f"u_{code}",
                ip="198.51.104.7",
            )
            assert r.status_code == 400, f"{code} 应 400，实得 {r.status_code}"
            assert r.json().get("error_code") == "INVITE_INVALID"
            details[code] = r.json()["detail"]

    assert len(set(details.values())) == 1, f"四种失效状态文案必须统一，实得 {details}"
    assert details["nonexist1"] == "邀请码无效"


# ═══════════════════════════════════════════════════════
# F5 — register-invite token 改 token_claims(user)
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f5_invite_registration_token_uses_token_claims(db_pair, auth_on):
    """注册签发的 access token 必须含 tv（撤销版本）且不含 role 声明（修复前红）。"""
    _, factory = db_pair
    await _create_tables(factory)
    await _seed_invite(factory, "claimscode1")

    app = _full_app(factory)
    async with _client(app) as c:
        r = await _register_invite(
            app,
            code="claimscode1",
            email="claims@sec.test",
            username="claimsuser",
            ip="198.51.105.7",
        )
        assert r.status_code == 200, r.text
        body = r.json()

        payload = _jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"])
        assert "tv" in payload, f"token 缺少 tv 撤销版本声明: {list(payload.keys())}"
        assert "role" not in payload, "token 不得携带 role 声明（角色以库内现值为准）"
        assert payload["sub"] == str(body["user"]["id"])

        # refresh 同源：换发链路可用（不变量）
        rr = await c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert rr.status_code == 200, rr.text


# ═══════════════════════════════════════════════════════
# F6 — chat_routes._try_user_id 旁路收口
# ═══════════════════════════════════════════════════════


_STATUS_PATHS = (
    "/api/channels/wechat/connection-status",
    "/api/channels/wechat/status",
    "/api/channels/wechat/status-stream",
)


class _AbortASGIError(Exception):
    """ASGI 探针取到响应头后的受控中止信号。"""


async def _response_status(app, path: str, headers: dict[str, str] | None = None) -> int:
    """最小 ASGI 探针：取 http.response.start 状态码后立即中止 app。

    httpx ASGITransport 会把响应体**跑到完成**才返回——无限 SSE 流
    （/status-stream）直接 client.get/stream 会永久挂死；此探针在
    响应头到达瞬间取码中止，对普通 JSON 端点同样适用。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }
    captured: list[int] = []

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message):
        if message["type"] == "http.response.start":
            captured.append(message["status"])
            raise _AbortASGIError()

    try:
        await app(scope, _receive, _send)
    except _AbortASGIError:
        pass
    except BaseException as exc:  # noqa: BLE001 —— anyio 任务组可能包装异常，取到头即成功
        if not captured:
            raise
        del exc
    assert captured, f"{path} 未发出响应头（可能在响应头前崩溃）"
    return captured[0]


@pytest.mark.asyncio
async def test_f6_status_endpoints_reject_disabled_account_token(seeded_db):
    """三个消费端点对停用账号旧 token 必须 401（修复前 _try_user_id 只验签 → 200，红）。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))
        user.is_active = False
        await db.commit()

    app = _full_app(factory)
    for path in _STATUS_PATHS:
        status = await _response_status(app, path, {"Authorization": f"Bearer {token}"})
        assert status == 401, f"{path} 停用账号 token 实得 {status}，必须 401"


@pytest.mark.asyncio
async def test_f6_status_endpoints_reject_tv_bumped_token(seeded_db):
    """改密后（tv 自增）旧 token 对三个端点必须 401（修复前 200，红）。"""
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))
        user.token_version = 1
        await db.commit()

    app = _full_app(factory)
    for path in _STATUS_PATHS:
        status = await _response_status(app, path, {"Authorization": f"Bearer {token}"})
        assert status == 401, f"{path} 撤销版本过期 token 实得 {status}，必须 401"


@pytest.mark.asyncio
async def test_f6_status_endpoints_valid_token_still_work(seeded_db):
    """语义不变量：有效主体 token 前两个端点照常 200 且 owner 归属本人。

    status-stream 为无限 SSE，只探状态码（owner 断言由上面两个 JSON 端点覆盖）。
    """
    factory = seeded_db
    async with factory() as db:
        user = await db.get(User, 1)
        token = create_access_token(token_claims(user))

    app = _full_app(factory)
    for path in _STATUS_PATHS:
        status = await _response_status(app, path, {"Authorization": f"Bearer {token}"})
        assert status == 200, f"{path} 有效 token 实得 {status}，必须 200"

    async with _client(app) as c:
        for path in _STATUS_PATHS[:2]:
            r = await c.get(path, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, f"{path} 有效 token 应 200，实得 {r.status_code}"
            assert r.json().get("owner_user_id") == 1


@pytest.mark.asyncio
async def test_f6_status_endpoints_no_token_rejected(seeded_db):
    """语义不变量：无 Bearer 依旧 401（不泄露全局状态）。"""
    factory = seeded_db
    app = _full_app(factory)
    for path in _STATUS_PATHS:
        status = await _response_status(app, path)
        assert status == 401, f"{path} 匿名实得 {status}，必须 401"
