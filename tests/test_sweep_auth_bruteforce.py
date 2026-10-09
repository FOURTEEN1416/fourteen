"""auth-bruteforce 修复域红测（EXT-1 / ABUSE-1 / EXT-3）— 红测先行。

攻击面与验收：
- EXT-1/ABUSE-1（同根因）：api/auth._bf_client_ip 旧实现取 X-Forwarded-For
  首跳作为 IP 失败滑窗键，而 nginx 模板 $proxy_add_x_forwarded_for 是追加
  语义（客户端自带伪造头保留在最前）→ 攻击者每请求换一个伪造 IP 即绕过
  「5 失败/60s/IP」。修复后限速键只信 ASGI 直连地址（request.client.host），
  伪造 XFF/X-Real-IP 头不再影响记账。
- EXT-3：/api/auth/register 完全无防爆破入口（对照同文件 login 与
  register-invite 均有）→ 枚举探测与账号农场不受限；409 双 oracle
  （Email already registered / Username already taken 与邀请码端点的
  「该邮箱已注册 / 该用户名已被使用」）可精确区分命中字段 → 账号枚举。
  修复后：注册面新增 IP 尝试桶（60s 窗 15 次）；两族端点 409 统一为单一
  不可区分文案 + 统一机器码（REGISTRATION_CONFLICT）。
- nginx：两个模板（deploy/nginx-ai-girlfriend.conf、deploy/nginx/ai-girlfriend.conf）
  全部 X-Forwarded-For 改覆盖写 $remote_addr（丢弃客户端自带伪造链）。

隔离纪律（与 tests/test_sec_p0_auth.py 同款）：sqlite 引擎逐用例独立
（tmp_path）且 teardown dispose；防爆破账本逐用例清零；注册分发角色卡
目录钉临时目录——零接触真实 data/ 与 config/characters/。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AI_GF_ENV", "dev")

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.auth_jwt import hash_password
from api.database import Base, InviteCode, User, get_db

_PASSWORD = "Str0ngPass!2026"


# ═══════════════════════════════════════════════════════
# 夹具与工具
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_bf_state():
    """防爆破账本（api.auth 模块级全局）逐用例前后清零，防跨用例污染。"""
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

    与 tests/test_sec_p0_auth.py 同纪律：不钉则每轮回归向真实
    config/characters/ 净增克隆卡（gitignored 不可见）。
    """
    from api.routers import character_routes

    sandbox = tmp_path_factory.mktemp("sweep-bf-characters")
    original = character_routes.CHARACTERS_DIR
    character_routes.CHARACTERS_DIR = sandbox
    yield
    character_routes.CHARACTERS_DIR = original


@pytest.fixture
async def db_pair(tmp_path):
    """逐用例独立 sqlite 引擎 + session 工厂；teardown dispose（Windows 文件锁）。"""
    db_path = tmp_path / "sweep_auth_bruteforce.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


@pytest.fixture
async def seeded_db(db_pair):
    """建表 + 种入已注册用户 eve（email/username 各占一个枚举靶）+ 有效邀请码。"""
    engine, factory = db_pair
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    now_email = "eve@sweep.test"
    async with factory() as db:
        db.add(
            User(
                id=1,
                email=now_email,
                username="eve",
                hashed_password=hash_password(_PASSWORD),
                display_name="eve",
                role="viewer",
                is_active=True,
                is_verified=True,
                token_version=0,
            )
        )
        db.add(
            InviteCode(
                code="sweepgood1",
                created_by=1,
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        await db.commit()
    return factory, now_email


def _full_app(factory) -> FastAPI:
    """完整 create_api_app + get_db 沙箱覆盖（真实登录/注册路由）。"""

    async def _db():
        async with factory() as session:
            yield session

    from api.app_factory import create_api_app

    app = create_api_app()
    app.dependency_overrides[get_db] = _db
    return app


def _client(app) -> AsyncClient:
    """ASGI 直连客户端：不携带任何 XFF 头，scope client 恒为 ASGITransport 默认。"""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=15.0)


def _spoof_headers() -> dict[str, str]:
    """每次调用返回一个**不同的随机伪造 XFF 首跳**（EXT-1 攻击载荷）。"""
    import random

    ip = f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    return {"X-Forwarded-For": ip}


# ═══════════════════════════════════════════════════════
# EXT-1 / ABUSE-1 — 伪造 XFF 不得绕过 IP 失败滑窗
# ═══════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ext1_xff_spoof_cannot_bypass_login_ip_window(db_pair):
    """登录：每请求换一个伪造 XFF 首跳，5 次失败后第 6 次 → 429。

    修复前：限速键取伪造首跳（每请求全新键）→ 恒 401 永不触顶（红）。
    """
    _, factory = db_pair
    # 建表即可（登录打到 user 不存在分支 → 401 + 记失败账）
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = _full_app(factory)
    async with _client(app) as c:
        for i in range(5):
            r = await c.post(
                "/api/auth/login",
                json={"login": f"ghost{i}@sweep.test", "password": "wrong-pass"},
                headers=_spoof_headers(),
            )
            assert r.status_code == 401, f"前 5 次应为普通失败，第 {i + 1} 次实得 {r.status_code}"
        sixth = await c.post(
            "/api/auth/login",
            json={"login": "ghost5@sweep.test", "password": "wrong-pass"},
            headers=_spoof_headers(),
        )
        assert sixth.status_code == 429, (
            f"伪造 XFF 每请求换 IP 时第 6 次失败应 429（滑窗必须以直连地址记账），"
            f"实得 {sixth.status_code}"
        )


@pytest.mark.asyncio
async def test_ext1_xff_spoof_cannot_bypass_register_invite_ip_window(db_pair):
    """register-invite：随机伪造 XFF 连续 5 次无效码后第 6 次 → 429（修复前恒 400，红）。"""
    _, factory = db_pair
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = _full_app(factory)
    async with _client(app) as c:
        for i in range(5):
            r = await c.post(
                "/api/auth/register-invite",
                json={
                    "invite_code": f"badcode{i}",
                    "email": f"probe{i}@sweep.test",
                    "username": f"probe{i}",
                    "password": _PASSWORD,
                },
                headers=_spoof_headers(),
            )
            assert r.status_code == 400, f"第 {i + 1} 次应为普通 400，实得 {r.status_code}"
        sixth = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "badcode5",
                "email": "probe5@sweep.test",
                "username": "probe5",
                "password": _PASSWORD,
            },
            headers=_spoof_headers(),
        )
        assert sixth.status_code == 429, (
            f"伪造 XFF 每请求换 IP 时第 6 次失败应 429，实得 {sixth.status_code}"
        )


def test_ext1_bf_client_ip_uses_direct_addr_only():
    """_bf_client_ip 必须只信 ASGI 直连地址；XFF/X-Real-IP 头一律忽略（修复前取首跳，红）。"""
    from api.auth import _bf_client_ip

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [
            (b"x-forwarded-for", b"6.6.6.6, 10.0.0.1"),
            (b"x-real-ip", b"7.7.7.7"),
        ],
        "client": ("203.0.113.77", 54321),
        "query_string": b"",
    }
    request = Request(scope)
    assert _bf_client_ip(request) == "203.0.113.77", (
        "限速键必须取直连地址，任何客户端可写头（XFF/X-Real-IP）都不得参与"
    )


# ═══════════════════════════════════════════════════════
# EXT-3 — 注册面限速 + 409 单一 oracle
# ═══════════════════════════════════════════════════════

_WEAK_PASSWORD = "aaaaaaaa"  # 8 位纯字母：过 PasswordStr 长度(8-128)、不过强度(缺数字)


@pytest.mark.asyncio
async def test_ext3_weak_password_payload_cannot_probe_existence(seeded_db):
    """固定弱密码载荷不得二分枚举：强度检查必须先于 409 冲突检查（第 1 轮验收 fail 主据）。

    修复前（auth_routes.py 强度检查位于 409 之后）：weak+已注册 → 409
    REGISTRATION_CONFLICT，weak+未注册 → 422「密码必须包含数字」——攻击者
    固定弱密码载荷即可按状态码二分判定任意 email/username 是否已注册（红）。
    修复后（对齐 invite_routes 先例：强度检查前置）：两分支一律先 422 同文案
    同机器码，固定载荷失去区分能力。
    """
    factory, taken_email = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        taken = await c.post(
            "/api/auth/register",
            json={
                "email": taken_email,
                "username": "weak_probe_taken",
                "password": _WEAK_PASSWORD,
            },
        )
        fresh = await c.post(
            "/api/auth/register",
            json={
                "email": "weak_fresh@sweep.test",
                "username": "weak_probe_fresh",
                "password": _WEAK_PASSWORD,
            },
        )
    assert taken.status_code == fresh.status_code, (
        f"固定弱密码载荷下已注册({taken.status_code})与未注册({fresh.status_code})"
        f"必须同态，否则可按状态码二分枚举账号存在性"
    )
    assert taken.json().get("error_code") == fresh.json().get("error_code"), (
        f"机器码也必须同态：{taken.json().get('error_code')!r} vs "
        f"{fresh.json().get('error_code')!r}"
    )


@pytest.mark.asyncio
async def test_ext3_invite_weak_password_checked_before_existence(seeded_db):
    """防回归钉：register-invite 的强度检查保持在存在性检查之前（既有先例，勿倒转）。

    invite_routes 既有顺序即正确（强度 → 邀请码 → 邮箱/用户名 409），本例钉住：
    弱密码 + 已注册 email 必须 422 先失败，不得漏到 409 泄露冲突态。
    """
    factory, _ = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        r = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "sweepgood1",
                "email": "eve@sweep.test",
                "username": "weak_inv_probe",
                "password": _WEAK_PASSWORD,
            },
        )
        assert r.status_code == 422, (
            f"弱密码必须先于存在性检查失败（422），实得 {r.status_code}——"
            f"若为 409 则强度检查被倒转、冲突态泄露"
        )
        # 语义不变量：合规密码 + 已注册 email 仍精确 409（真实冲突照常报）
        ok = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "sweepgood1",
                "email": "eve@sweep.test",
                "username": "strong_inv_probe",
                "password": _PASSWORD,
            },
        )
        assert ok.status_code == 409, (
            f"合规输入下的真实冲突必须仍为 409，实得 {ok.status_code}"
        )


@pytest.mark.asyncio
async def test_ext3_register_entry_rate_limited(seeded_db):
    """register 连续探测（409 冲突路径）必须被注册面尝试桶限速 → 第 16 次 429。

    修复前：端点无任何限速调用，恒 409 可无限枚举（红）。
    """
    factory, taken_email = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        statuses: list[int] = []
        for i in range(16):
            r = await c.post(
                "/api/auth/register",
                json={
                    "email": taken_email,
                    "username": f"probe_{i}",
                    "password": _PASSWORD,
                },
            )
            statuses.append(r.status_code)
        assert all(s == 409 for s in statuses[:15]), (
            f"前 15 次应为普通 409 冲突，实得 {statuses}"
        )
        assert statuses[15] == 429, (
            f"注册面连续 16 次探测第 16 次必须 429（枚举探测路径必须限速），实得 {statuses}"
        )


@pytest.mark.asyncio
async def test_ext3_register_conflict_single_oracle(seeded_db):
    """register 409 双 oracle 统一：邮箱命中与用户名命中必须同文案同错误码。

    修复前：「Email already registered」/「Username already taken」可精确区分（红）。
    """
    factory, taken_email = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        by_email = await c.post(
            "/api/auth/register",
            json={"email": taken_email, "username": "fresh_name_1", "password": _PASSWORD},
        )
        by_name = await c.post(
            "/api/auth/register",
            json={"email": "fresh@sweep.test", "username": "eve", "password": _PASSWORD},
        )
    assert by_email.status_code == 409 and by_name.status_code == 409
    body_a, body_b = by_email.json(), by_name.json()
    assert body_a["detail"] == body_b["detail"], (
        f"两种命中必须单一不可区分文案：{body_a['detail']!r} vs {body_b['detail']!r}"
    )
    assert body_a.get("error_code") == body_b.get("error_code"), (
        f"机器码也必须统一（header/body 双通道都不得泄露命中字段）："
        f"{body_a.get('error_code')!r} vs {body_b.get('error_code')!r}"
    )
    assert body_a["detail"] not in ("Email already registered", "Username already taken"), (
        "不得沿用可区分旧文案"
    )


@pytest.mark.asyncio
async def test_ext3_invite_register_conflict_single_oracle(seeded_db):
    """register-invite 409 双 oracle 同样统一（修复前「该邮箱已注册/该用户名已被使用」可区分，红）。"""
    factory, _ = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        by_email = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "sweepgood1",
                "email": "eve@sweep.test",
                "username": "fresh_name_2",
                "password": _PASSWORD,
            },
        )
        by_name = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "sweepgood1",
                "email": "fresh2@sweep.test",
                "username": "eve",
                "password": _PASSWORD,
            },
        )
    assert by_email.status_code == 409 and by_name.status_code == 409, (
        f"两个分支都应 409：{by_email.status_code} / {by_name.status_code}"
    )
    body_a, body_b = by_email.json(), by_name.json()
    assert body_a["detail"] == body_b["detail"], (
        f"两种命中必须单一不可区分文案：{body_a['detail']!r} vs {body_b['detail']!r}"
    )
    assert body_a.get("error_code") == body_b.get("error_code")
    assert body_a["detail"] not in ("该邮箱已注册", "该用户名已被使用"), "不得沿用可区分旧文案"


@pytest.mark.asyncio
async def test_ext3_register_single_success_not_throttled(seeded_db):
    """语义不变量：正常单次注册不受尝试桶影响（201 成功，不误伤真实用户）。"""
    factory, _ = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        r = await c.post(
            "/api/auth/register",
            json={
                "email": "normal@sweep.test",
                "username": "normaluser",
                "password": _PASSWORD,
            },
        )
        assert r.status_code == 200, f"正常注册应成功，实得 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_ext3_register_invite_entry_also_has_attempt_bucket(seeded_db):
    """register-invite 入口同样挂注册面尝试桶（成功注册也占桶，压制账号农场）。"""
    factory, _ = seeded_db
    app = _full_app(factory)
    async with _client(app) as c:
        # 409 冲突路径 15 次打满尝试桶（不消费邀请码、不建用户）
        for i in range(15):
            r = await c.post(
                "/api/auth/register-invite",
                json={
                    "invite_code": "sweepgood1",
                    "email": "eve@sweep.test",
                    "username": f"inv_probe_{i}",
                    "password": _PASSWORD,
                },
            )
            assert r.status_code == 409, f"第 {i + 1} 次应 409，实得 {r.status_code}"
        sixth = await c.post(
            "/api/auth/register-invite",
            json={
                "invite_code": "sweepgood1",
                "email": "bucket@sweep.test",
                "username": "bucketuser",
                "password": _PASSWORD,
            },
        )
        assert sixth.status_code == 429, (
            f"尝试桶打满后（即便携带有效邀请码）应 429，实得 {sixth.status_code}"
        )


# ═══════════════════════════════════════════════════════
# nginx 模板 — XFF 覆盖写静态断言
# ═══════════════════════════════════════════════════════

_NGINX_TEMPLATES = (
    "deploy/nginx-ai-girlfriend.conf",
    os.path.join("deploy", "nginx", "ai-girlfriend.conf"),
)


def test_nginx_templates_xff_override_static():
    """两个 nginx 模板必须无 $proxy_add_x_forwarded_for 残留，XFF 一律覆盖写 $remote_addr。

    追加语义会把客户端自带的伪造 XFF 链保留在最前（EXT-1 根因）；
    覆盖写后上游（uvicorn forwarded_allow_ips=127.0.0.1）无论新旧解析算法
    拿到的都是真实直连 IP。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in _NGINX_TEMPLATES:
        path = os.path.join(repo_root, rel)
        assert os.path.exists(path), f"模板缺失：{rel}"
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert "$proxy_add_x_forwarded_for" not in text, (
            f"{rel} 仍残留追加语义 $proxy_add_x_forwarded_for（客户端伪造 XFF 链会被保留）"
        )
        overrides = text.count("X-Forwarded-For $remote_addr")
        assert overrides >= 5, (
            f"{rel} 覆盖写 X-Forwarded-For $remote_addr 仅 {overrides} 处（预期 ≥5，"
            f"/api/ 与 fastrun 各反代段各一处）"
        )
