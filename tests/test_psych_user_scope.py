"""心理画像归属收窄（普通用户只看自己会话 / admin 全量）。

被测：`/api/psych/*` 五个端点的**双分支**（不新增路由，只改依赖与取数面）：

- **admin**：保留改前的全量语义（显式过滤或「全部」）；
- **普通用户**：只在**本人**会话的 scope 上读写；请求他人的 `session_id`
  → **404**（与 `require_character_access` 同口径，不区分「不存在」与
  「无权限」，防状态码枚举）。

归属真源：`profile_scope()` = `{character_id}:{session_id}`，而 session_id
自 v1.28 起就是完整会话键（`N:wxid@im.wechat` / `N:web:hex`，owner 段即注册
用户 id）。因此归属强制**只发生在路由层**，生成主链与 persona_extractor
零改动（本文件即该边界的回归）。

隔离用例（A 读不到 B / A 删不掉 B）是硬红线；admin 两例守护「开放不等于
削弱管理面」。全程临时 persona 库 + 临时 users 库，不触 `data/`。
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth import verify_api_key_dep
from api.auth_jwt import AuthPrincipal, create_access_token, hash_password
from api.database import Base, User, WechatBinding, get_db
from api.deps import deps
from api.routers import personality_routes
from api.session_manager import SessionManager
from persona_extractor.fusion import PersonaExtractor
from persona_extractor.models import OceanTraits, UserPersona, UserPersonaSnapshot

ADMIN_ID = 1
USER_A = 2
USER_B = 3
CHAR = "十四"

A_WX_SESSION = f"{USER_A}:uA@im.wechat"
B_WX_SESSION = f"{USER_B}:uB@im.wechat"
SCOPE_A = f"{CHAR}:{A_WX_SESSION}"
SCOPE_B = f"{CHAR}:{B_WX_SESSION}"


def _persona(scope: str, openness: float, updated: str) -> UserPersona:
    return UserPersona(
        user_id=scope,
        ocean=OceanTraits(openness=openness),
        snapshot_count=3,
        first_seen="2026-10-20T10:00:00",
        last_updated=updated,
    )


def _snapshot(openness: float, confidence: float) -> UserPersonaSnapshot:
    return UserPersonaSnapshot(
        timestamp="2026-10-29T10:00:00",
        ocean=OceanTraits(openness=openness),
        confidence=confidence,
        source="test",
    )


def _bearer(uid: int) -> dict[str, str]:
    """真实 access token：端点走 get_optional_principal 的完整主体链（验签 + 存在 + 版本）。"""
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def psych(tmp_path):
    """最小 app：只挂 personality_routes；users 库为临时库，会话登记为真实
    SessionManager（web 键由它自己生成，测试不自拼）。"""
    extractor = PersonaExtractor(
        llm_gateway=None, db_path=str(tmp_path / "persona.db"), user_id="default"
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'users.db'}", echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, name, role in (
                (ADMIN_ID, "psych-admin", "admin"),
                (USER_A, "psych-a", "viewer"),
                (USER_B, "psych-b", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=f"{name}@psych.test",
                        username=name,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=name,
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            # D16 一人一 bot：每个注册用户一个绑定 wxid（会话键 = `N:wxid@im.wechat`）
            session.add(WechatBinding(user_id=USER_A, wxid="uA@im.wechat"))
            session.add(WechatBinding(user_id=USER_B, wxid="uB@im.wechat"))
            await session.commit()

    asyncio.run(_init())

    sessions = SessionManager()
    web_a = sessions.create_session(str(USER_A), "web")
    web_b = sessions.create_session(str(USER_B), "web")

    app = FastAPI()
    app.include_router(personality_routes.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    app.dependency_overrides[get_db] = _get_db_override

    class _Orch:
        components = {"persona_extractor": extractor}

    saved_orch, saved_sessions = deps.orch, deps.sessions
    deps.orch = _Orch()
    deps.sessions = sessions

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield SimpleNamespace(
        client=client,
        app=app,
        pe=extractor,
        web_a=web_a,
        web_b=web_b,
        sessions=sessions,
    )
    asyncio.run(client.aclose())
    deps.orch, deps.sessions = saved_orch, saved_sessions
    conn = extractor.bank.get_connection()
    if conn is not None:
        conn.close()
    asyncio.run(engine.dispose())


# ── 普通用户：读面 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_normal_user_profile_reads_only_own_latest_scope(psych) -> None:
    """不带 session_id：只在本人会话的桶里取最近更新的一份（非聚合），他人桶既不进结果也不进计数。"""
    pe = psych.pe
    web_a_scope = f"{CHAR}:{psych.web_a}"
    pe.bank.save_persona(_persona(SCOPE_A, 0.2, "2026-10-28T10:00:00"))
    pe.bank.save_persona(_persona(web_a_scope, 0.6, "2026-10-29T10:00:00"))
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    resp = await psych.client.get(
        "/api/psych/profile", params={"character_id": CHAR}, headers=_bearer(USER_A)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] != SCOPE_B, f"读到他人会话的画像：{body!r}"
    assert body["scope"] == web_a_scope, f"应取本人最近更新的那份：{body!r}"
    assert body["scope_count"] == 2, f"计数必须是本人的桶数（不是全部）：{body!r}"
    assert (body.get("ocean") or {}).get("openness") == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_normal_user_cannot_read_other_session(psych) -> None:
    """显式请求他人 session_id：一律 404（不是 403，不区分不存在与无权限）。"""
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    for other_sid in (B_WX_SESSION, psych.web_b):
        resp = await psych.client.get(
            "/api/psych/profile",
            params={"character_id": CHAR, "session_id": other_sid},
            headers=_bearer(USER_A),
        )
        assert resp.status_code == 404, (
            f"请求他人会话 {other_sid!r} 应 404，实得 {resp.status_code} {resp.text}"
        )
        assert "0.9" not in resp.text, "404 回执不得回显他人画像内容"


@pytest.mark.asyncio
async def test_normal_user_snapshots_exclude_other_users(psych) -> None:
    """快照面同一条白名单：他人桶的快照不得出现在响应里。"""
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_A, 0.5, "2026-10-29T10:00:00"))
    pe.bank.save_persona(_persona(SCOPE_B, 0.8, "2026-10-30T10:00:00"))
    pe.bank.add_snapshot(copy.deepcopy(_snapshot(0.5, 0.4)), SCOPE_A)
    pe.bank.add_snapshot(copy.deepcopy(_snapshot(0.8, 0.7)), SCOPE_B)

    resp = await psych.client.get(
        "/api/psych/snapshots",
        params={"character_id": CHAR},
        headers=_bearer(USER_A),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == SCOPE_A, f"应读本人桶：{body!r}"
    assert len(body["snapshots"]) == 1, f"混入他人快照：{body!r}"
    assert body["snapshots"][0]["confidence"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_normal_user_mental_health_and_liwc_follow_own_scope(psych) -> None:
    """筛查与 LIWC 同样只能读本人桶（他人桶即便更新也不得压过本人）。"""
    pe = psych.pe
    mine = _persona(SCOPE_A, 0.3, "2026-10-28T10:00:00")
    mine.mental_health = {"depression": {"total_score": 3}}
    mine.liwc = {"emotion_pos": 0.2}
    pe.bank.save_persona(mine)
    others = _persona(SCOPE_B, 0.7, "2026-10-30T10:00:00")
    others.mental_health = {"depression": {"total_score": 99}}
    others.liwc = {"emotion_pos": 0.9}
    pe.bank.save_persona(others)

    mh = await psych.client.get(
        "/api/psych/mental-health",
        params={"character_id": CHAR},
        headers=_bearer(USER_A),
    )
    liwc = await psych.client.get(
        "/api/psych/liwc", params={"character_id": CHAR}, headers=_bearer(USER_A)
    )

    assert mh.json()["scope"] == SCOPE_A, mh.text
    assert mh.json()["mental_health"] == {"depression": {"total_score": 3}}, mh.text
    assert liwc.json()["scope"] == SCOPE_A, liwc.text
    assert liwc.json()["data"] == {"emotion_pos": 0.2}, liwc.text


# ── 普通用户：删面（最高危）───────────────────────────────


@pytest.mark.asyncio
async def test_normal_user_delete_leaves_other_buckets(psych) -> None:
    """自清除只清本人：他人画像必须原样存活，且回执不得带上他人 scope。"""
    pe = psych.pe
    web_a_scope = f"{CHAR}:{psych.web_a}"
    pe.bank.save_persona(_persona(SCOPE_A, 0.2, "2026-10-28T10:00:00"))
    pe.bank.save_persona(_persona(web_a_scope, 0.4, "2026-10-29T10:00:00"))
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    resp = await psych.client.delete(
        "/api/psych/profile", params={"character_id": CHAR}, headers=_bearer(USER_A)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cleared"] == 2, f"应只清本人两份：{body!r}"
    assert SCOPE_B not in body["scopes"], f"回执泄露他人 scope：{body!r}"
    assert pe.bank.get_persona(SCOPE_A) is None
    assert pe.bank.get_persona(web_a_scope) is None
    assert pe.bank.get_persona(SCOPE_B) is not None, "把他人的画像一起清了"


@pytest.mark.asyncio
async def test_normal_user_delete_with_other_session_is_404(psych) -> None:
    """带他人 session_id 的自清除：404 且零落库（不是静默成功）。"""
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    resp = await psych.client.delete(
        "/api/psych/profile",
        params={"character_id": CHAR, "session_id": B_WX_SESSION},
        headers=_bearer(USER_A),
    )

    assert resp.status_code == 404, resp.text
    assert pe.bank.get_persona(SCOPE_B) is not None, "越权清除生效了"


# ── 客户端可控参数不得成为读键（验收打回项）──────────────
#
# 缺陷形态：非 admin 分支在「无本人桶」时把 `character_id`（或
# `profile_scope(cid, '')`）原样喂给 `bank.get_persona` / `get_recent_snapshots`
# ⇒ `?character_id=<他人完整 scope 键>` 读到他人画像与快照。下面四例把该面钉死。


@pytest.mark.asyncio
async def test_no_own_bucket_never_falls_back_to_a_client_key(psych) -> None:
    """A 无本人桶、B 有桶：即使只传普通角色 id / 什么都不传，也拿不到任何 scope。

    旧实现把 `profile_scope(cid, '')`（= cid 原串）当读键回吐；盘上若恰有同名桶
    即越权。现实现只回 `scope=""` 且**不触 bank**（读面与快照面同一条短路）。
    """
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))
    pe.bank.add_snapshot(copy.deepcopy(_snapshot(0.9, 0.9)), SCOPE_B)

    for params in ({"character_id": CHAR}, {}):
        resp = await psych.client.get(
            "/api/psych/profile", params=params, headers=_bearer(USER_A)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "insufficient_data", f"{params} → {body!r}"
        assert body["scope"] == "" and body["scope_count"] == 0, f"{body!r}"
        assert "ocean" not in body, f"泄露他人画像：{body!r}"

    snaps = await psych.client.get(
        "/api/psych/snapshots", params={"character_id": CHAR}, headers=_bearer(USER_A)
    )
    assert snaps.status_code == 200, snaps.text
    assert snaps.json() == {"scope": "", "snapshots": []}, snaps.text


@pytest.mark.asyncio
async def test_scope_key_in_character_id_is_not_a_read_key(psych) -> None:
    """把他人完整 scope 键塞进 character_id：404，且响应里没有任何他人数据。"""
    pe = psych.pe
    victim = _persona(SCOPE_B, 0.9, "2026-10-30T10:00:00")
    victim.mental_health = {"depression": {"total_score": 99}}
    victim.liwc = {"emotion_pos": 0.9}
    pe.bank.save_persona(victim)

    resp = await psych.client.get(
        "/api/psych/profile", params={"character_id": SCOPE_B}, headers=_bearer(USER_A)
    )

    assert resp.status_code == 404, f"应 404，实得 {resp.status_code} {resp.text}"
    assert "0.9" not in resp.text, f"泄露受害者画像：{resp.text}"
    assert SCOPE_B not in resp.text, f"回显了他人 scope：{resp.text}"


@pytest.mark.asyncio
async def test_scope_key_in_character_id_cannot_reach_other_endpoints(psych) -> None:
    """快照 / 筛查 / LIWC 三面同一防线（快照行还带 trigger_message 列）。"""
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))
    pe.bank.add_snapshot(copy.deepcopy(_snapshot(0.9, 0.9)), SCOPE_B)

    for path in ("/api/psych/snapshots", "/api/psych/mental-health", "/api/psych/liwc"):
        resp = await psych.client.get(
            path, params={"character_id": SCOPE_B}, headers=_bearer(USER_A)
        )
        assert resp.status_code == 404, f"{path} 应 404，实得 {resp.status_code} {resp.text}"
        assert "0.9" not in resp.text, f"{path} 泄露受害者数据：{resp.text}"


@pytest.mark.asyncio
async def test_legacy_default_bucket_is_not_readable_by_normal_user(psych) -> None:
    """`?character_id=default` 不得成为历史全局 default 桶的读键（只回空画像）。"""
    legacy = _persona("default", 0.95, "2026-10-30T10:00:00")
    psych.pe.bank.save_persona(legacy)

    resp = await psych.client.get(
        "/api/psych/profile", params={"character_id": "default"}, headers=_bearer(USER_A)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "insufficient_data", f"读到了 legacy 桶：{body!r}"
    assert "ocean" not in body, f"泄露 default 桶画像：{body!r}"
    assert body["scope"] == "", f"不得回显客户端可控键：{body!r}"


@pytest.mark.asyncio
async def test_scope_key_in_character_id_cannot_delete(psych) -> None:
    """删面同一条防线：把他人 scope 键当 character_id → 404 且零落库。"""
    pe = psych.pe
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    resp = await psych.client.delete(
        "/api/psych/profile", params={"character_id": SCOPE_B}, headers=_bearer(USER_A)
    )

    assert resp.status_code == 404, resp.text
    assert pe.bank.get_persona(SCOPE_B) is not None, "越权清除生效了"


@pytest.mark.asyncio
async def test_own_declared_session_without_profile_reports_insufficient(psych) -> None:
    """本人已声明会话尚无画像 → 200 insufficient_data（不是 404，也不回显请求键）。"""
    resp = await psych.client.get(
        "/api/psych/profile",
        params={"character_id": CHAR, "session_id": psych.web_a},
        headers=_bearer(USER_A),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "insufficient_data", f"{body!r}"
    assert body["scope_count"] == 0
    assert body["scope"] == "", f"不得回显客户端可控键：{body!r}"


# ── admin：全量语义保留 ───────────────────────────────────


@pytest.mark.asyncio
async def test_admin_still_reads_every_scope(psych) -> None:
    """admin 分支保持改前语义：不按 owner 过滤，取全局最近更新的那份。"""
    pe = psych.pe
    web_a_scope = f"{CHAR}:{psych.web_a}"
    pe.bank.save_persona(_persona(SCOPE_A, 0.2, "2026-10-28T10:00:00"))
    pe.bank.save_persona(_persona(web_a_scope, 0.4, "2026-10-29T10:00:00"))
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))

    resp = await psych.client.get(
        "/api/psych/profile", params={"character_id": CHAR}, headers=_bearer(ADMIN_ID)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scope"] == SCOPE_B, f"admin 应取全局最新：{body!r}"
    assert body["scope_count"] == 3, "admin 的计数是全部匹配桶"
    assert (body.get("ocean") or {}).get("openness") == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_admin_still_clears_every_scope(psych) -> None:
    """admin 无参 DELETE 仍是「清除全部已学 scope」（页面文案口径不变）。"""
    pe = psych.pe
    web_b_scope = f"{CHAR}:{psych.web_b}"
    pe.bank.save_persona(_persona(SCOPE_A, 0.2, "2026-10-28T10:00:00"))
    pe.bank.save_persona(_persona(SCOPE_B, 0.9, "2026-10-30T10:00:00"))
    pe.bank.save_persona(_persona(web_b_scope, 0.5, "2026-10-29T10:00:00"))

    resp = await psych.client.delete(
        "/api/psych/profile", headers=_bearer(ADMIN_ID)
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] == 3, resp.text
    for scope in (SCOPE_A, SCOPE_B, web_b_scope):
        assert pe.bank.get_persona(scope) is None, f"{scope} 未被清掉"


# ── 认证依赖收口（R5-1 / R5-2）────────────────────────────


@pytest.mark.asyncio
async def test_actor_has_no_current_user_fallback(psych) -> None:
    """_actor 只依赖可选主体：无主体直接 401，五端点不再挂 get_current_user。

    旧实现 `_actor(principal, user)` 的 `principal is None` 回落分支生产不可达
    （无 Bearer 时 get_current_user 必 401），却让认证链多查一次 users。
    """
    with pytest.raises(HTTPException) as missing:
        await personality_routes._actor(None)
    assert missing.value.status_code == 401

    admin_actor = await personality_routes._actor(
        AuthPrincipal(
            user_id=ADMIN_ID,
            role="admin",
            token_version=0,
            user=SimpleNamespace(id=ADMIN_ID, role="admin"),
        )
    )
    assert admin_actor == (ADMIN_ID, True)
    user_actor = await personality_routes._actor(
        AuthPrincipal(
            user_id=USER_A,
            role="viewer",
            token_version=0,
            user=SimpleNamespace(id=USER_A, role="viewer"),
        )
    )
    assert user_actor == (USER_A, False)

    psych_routes = [
        route
        for route in personality_routes.router.routes
        if str(getattr(route, "path", "")).startswith("/api/psych/")
    ]
    assert len(psych_routes) == 5, [r.path for r in psych_routes]

    def _collect_dep_names(dependant) -> set[str]:
        """递归收集依赖树里的全部 callable 名。

        R5-1 清扫批：旧断言只看 ``route.dependant.dependencies`` 顶层集合，
        嵌在 ``_actor`` 内部的 ``get_current_user`` 不会出现在其中——构造
        ``_actor_bad(Depends(get_optional_principal), Depends(get_current_user))``
        的假路由时旧断言仍通过（顶层 names 只有 ``_actor_bad``）。递归后
        同类嵌套回归才会被抓住。
        """
        names: set[str] = set()
        for dep in dependant.dependencies:
            names.add(getattr(dep.call, "__name__", ""))
            names |= _collect_dep_names(dep)
        return names

    for route in psych_routes:
        dep_names = _collect_dep_names(route.dependant)
        assert "get_current_user" not in dep_names, route.path
        assert "get_current_user_id" not in dep_names, route.path
        assert "get_optional_principal" in dep_names, route.path
        assert "_actor" in dep_names, route.path


@pytest.mark.asyncio
async def test_each_request_loads_user_once(psych, monkeypatch) -> None:
    """R5-2：本路由自身依赖链只查一次 users。

    夹具已覆写 `verify_api_key_dep`（它内部另有全局认证面的一次主体解析）；
    旧链 `get_optional_principal` + `get_current_user`（含 get_current_user_id
    的两次 _load_enabled_user）在同一请求内共查 3 次，本用例会变红。
    """
    import api.auth_jwt as auth_jwt

    calls: list[int] = []
    original = auth_jwt._load_enabled_user

    async def _counting_load(db, user_id):
        calls.append(int(user_id))
        return await original(db, user_id)

    monkeypatch.setattr(auth_jwt, "_load_enabled_user", _counting_load)

    for uid in (USER_A, ADMIN_ID):
        calls.clear()
        resp = await psych.client.get(
            "/api/psych/profile", params={"character_id": CHAR}, headers=_bearer(uid)
        )
        assert resp.status_code == 200, resp.text
        assert calls == [uid], f"uid={uid} 每请求应恰好查一次 users，实得 {calls!r}"


def test_narrower_than_spec_note_is_documented() -> None:
    """R5-3：读面「比规格更窄」的口径注记必须留在模块与函数注释里。"""
    source = Path(personality_routes.__file__).read_text(encoding="utf-8")
    assert "实现比规格更窄" in source
    assert "exact 匹配" in source
    assert "最近的一份" in source
    assert "不在各 scope 之间做聚合" in source
    assert "实现比规格更窄" in (personality_routes._owned_scope.__doc__ or "")


@pytest.mark.asyncio
async def test_full_auth_wiring_does_not_stack_user_lookups(psych, monkeypatch) -> None:
    """生产接线（不覆写 verify_api_key_dep）：认证总查 users 不超过 2 次。

    全局认证门 `verify_api_key_dep` 自身解析主体 1 次（所有路由共有），
    `_actor` 再 1 次；旧链还会叠 get_current_user_id + get_current_user 两次
    （共 4 次），本用例守住「每层最多一次」。
    """
    import api.auth_jwt as auth_jwt

    calls: list[int] = []
    original = auth_jwt._load_enabled_user

    async def _counting_load(db, user_id):
        calls.append(int(user_id))
        return await original(db, user_id)

    monkeypatch.setattr(auth_jwt, "_load_enabled_user", _counting_load)
    psych.app.dependency_overrides.pop(verify_api_key_dep, None)
    try:
        resp = await psych.client.get(
            "/api/psych/profile", params={"character_id": CHAR}, headers=_bearer(USER_A)
        )
    finally:
        psych.app.dependency_overrides[verify_api_key_dep] = lambda: True

    assert resp.status_code == 200, resp.text
    assert set(calls) == {USER_A}, f"应只查本人的 users 行：{calls!r}"
    assert len(calls) <= 2, f"认证链查 users 次数超限（旧链为 4）：{calls!r}"


# ── 无主体：保持改前的 fail-closed ────────────────────────


@pytest.mark.asyncio
async def test_missing_token_is_rejected(psych) -> None:
    """心理画像是本人数据、无机器面：无 Bearer 一律 401（不得因「可选主体」放宽）。"""
    for method, path in (
        ("get", "/api/psych/profile"),
        ("get", "/api/psych/snapshots"),
        ("get", "/api/psych/mental-health"),
        ("get", "/api/psych/liwc"),
        ("delete", "/api/psych/profile"),
    ):
        resp = await getattr(psych.client, method)(path)
        assert resp.status_code == 401, f"{path} 无凭证应 401，实得 {resp.status_code}"


@pytest.mark.asyncio
async def test_invalid_token_is_rejected(psych) -> None:
    """伪造 Bearer 不得借「可选主体」通道落地（验签失败即 401）。"""
    resp = await psych.client.get(
        "/api/psych/profile", headers={"Authorization": "Bearer not-a-token"}
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_missing_token_is_rejected_even_without_extractor(psych) -> None:
    """提取器未初始化时也不得让无凭证请求落进 200 分支（认证依赖先于 handler）。"""
    saved = deps.orch
    deps.orch = None
    try:
        resp = await psych.client.get("/api/psych/profile")
    finally:
        deps.orch = saved
    assert resp.status_code == 401, f"无凭证 + 无提取器应仍 401，实得 {resp.status_code}"
