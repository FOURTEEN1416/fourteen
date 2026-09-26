"""W8 · 心理画像控制面 scope 同源回归（缺陷 E）。

生成侧把画像写在 **请求级 scope**（`{character_id}:{session_id}`，见
`orchestrator/optimized_orchestrator.py` 的 `process_message` 准备段），而控制面
`/api/psych/*` 读的是 `PersonaExtractor.user_id`（默认 `default`，请求链已禁止再
切换它）。两侧永远不是同一个键 ⇒ 页面恒显示"画像尚未生成"，重置也只清一个用不
着的空桶，却对用户承诺"清除所有已学习的心理特征数据"。

全部使用临时 persona 库与临时 users 库，不触 `data/`。
"""

from __future__ import annotations

import asyncio
import copy

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user_id
from api.database import Base, User, get_db
from api.deps import deps
from api.routers import personality_routes
from persona_extractor.fusion import PersonaExtractor
from persona_extractor.models import OceanTraits, UserPersona, UserPersonaSnapshot

ADMIN_ID = 1
CHAR = "十四"
SESSION = "user4:o9cq805ifq"
SCOPE = f"{CHAR}:{SESSION}"
OTHER_SCOPE = f"{CHAR}:user9:o9OTHER"


def _persona(user_id: str, openness: float, updated: str) -> UserPersona:
    return UserPersona(
        user_id=user_id,
        ocean=OceanTraits(openness=openness),
        snapshot_count=3,
        first_seen="2026-09-20T10:00:00",
        last_updated=updated,
    )


@pytest.fixture
def extractor(tmp_path):
    pe = PersonaExtractor(
        llm_gateway=None, db_path=str(tmp_path / "persona.db"), user_id="default"
    )
    yield pe
    conn = pe.bank.get_connection()
    if conn is not None:
        conn.close()


@pytest.fixture
def psych_client(extractor, tmp_path):
    """最小 app：只挂 personality_routes，真实 require_role 链走临时 users 库。"""
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
            session.add(
                User(
                    id=ADMIN_ID,
                    email="w8-admin@test.local",
                    username="w8-admin",
                    hashed_password="x",
                    display_name="Admin",
                    role="admin",
                    is_active=True,
                    is_verified=True,
                )
            )
            await session.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(personality_routes.router)
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_user_id] = lambda: ADMIN_ID

    class _Orch:
        components = {"persona_extractor": extractor}

    saved_orch = deps.orch
    deps.orch = _Orch()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield client
    asyncio.run(client.aclose())
    deps.orch = saved_orch
    asyncio.run(engine.dispose())


# ── 缺陷 E：读侧 scope ─────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_endpoint_reads_the_scope_generation_writes(psych_client) -> None:
    """指定 character+session 时，控制面必须读到生成侧写入的那份画像。"""
    psych_client  # noqa: B018 — fixture 副作用即 app
    extractor = personality_routes._get_pe()
    assert extractor is not None
    extractor.bank.save_persona(_persona(SCOPE, 0.8, "2026-09-26T10:00:00"))

    resp = await psych_client.get(
        "/api/psych/profile", params={"character_id": CHAR, "session_id": SESSION}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("scope") == SCOPE, f"未回显实际读取的 scope：{body!r}"
    assert body.get("status") != "insufficient_data", (
        f"生成侧已有画像，控制面仍报缺数据（读的是 {body.get('user_id')!r}）：{body!r}"
    )
    assert (body.get("ocean") or {}).get("openness") == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_profile_without_session_resolves_latest_scope_for_character(psych_client) -> None:
    """页面只有 activeCharacter：按角色取最近更新的那份，并回显 scope 数量。"""
    extractor = personality_routes._get_pe()
    extractor.bank.save_persona(_persona(SCOPE, 0.2, "2026-09-24T10:00:00"))
    extractor.bank.save_persona(_persona(OTHER_SCOPE, 0.9, "2026-09-26T10:00:00"))

    resp = await psych_client.get("/api/psych/profile", params={"character_id": CHAR})

    body = resp.json()
    assert body.get("scope") == OTHER_SCOPE, f"应取最近更新的那份：{body!r}"
    assert (body.get("ocean") or {}).get("openness") == pytest.approx(0.9)
    assert body.get("scope_count") == 2, "必须让调用方知道该角色下有几份画像"


@pytest.mark.asyncio
async def test_snapshots_follow_the_same_scope(psych_client) -> None:
    """/api/psych/snapshots 与 profile 同一 scope 判据，否则趋势与画像对不上。"""
    extractor = personality_routes._get_pe()
    extractor.bank.save_persona(_persona(SCOPE, 0.7, "2026-09-26T10:00:00"))
    snap = UserPersonaSnapshot(
        timestamp="2026-09-26T10:00:00",
        ocean=OceanTraits(openness=0.7),
        confidence=0.6,
        source="test",
    )
    extractor.bank.add_snapshot(copy.deepcopy(snap), SCOPE)
    extractor.bank.add_snapshot(copy.deepcopy(snap), "default")

    resp = await psych_client.get(
        "/api/psych/snapshots", params={"character_id": CHAR, "session_id": SESSION}
    )

    body = resp.json()
    snaps = body.get("snapshots", [])
    assert len(snaps) == 1, f"应只返回该 scope 的快照（拿到 {len(snaps)} 条）"
    assert body.get("scope") == SCOPE


@pytest.mark.asyncio
async def test_mental_health_and_liwc_follow_the_same_scope(psych_client) -> None:
    """筛查与 LIWC 端点同源：读的是 scope 内画像，不再固定读 `default`。"""
    extractor = personality_routes._get_pe()
    persona = _persona(SCOPE, 0.7, "2026-09-26T10:00:00")
    persona.mental_health = {"depression": {"total_score": 5}}
    persona.liwc = {"emotion_pos": 0.4}
    extractor.bank.save_persona(persona)

    params = {"character_id": CHAR, "session_id": SESSION}
    mh = (await psych_client.get("/api/psych/mental-health", params=params)).json()
    liwc = (await psych_client.get("/api/psych/liwc", params=params)).json()

    assert mh.get("scope") == SCOPE, f"mental-health 未回显 scope：{mh!r}"
    assert mh.get("mental_health") == {"depression": {"total_score": 5}}, mh
    assert liwc.get("scope") == SCOPE, f"liwc 未回显 scope：{liwc!r}"
    assert liwc.get("data") == {"emotion_pos": 0.4}, liwc


@pytest.mark.asyncio
async def test_empty_bank_still_reports_honest_insufficient_data(psych_client) -> None:
    """反证守护：没有任何画像时不得凭空造数据，scope_count 如实为 0。"""
    resp = await psych_client.get("/api/psych/profile", params={"character_id": CHAR})

    body = resp.json()
    assert body.get("status") == "insufficient_data"
    assert body.get("scope_count") == 0


# ── 缺陷 E：写侧（重置）scope 与失败可见 ────────────────────


@pytest.mark.asyncio
async def test_reset_clears_every_scope_the_page_promises(psych_client) -> None:
    """重置文案是"清除所有已学习的心理特征数据" —— 必须真清掉全部 scope。"""
    extractor = personality_routes._get_pe()
    extractor.bank.save_persona(_persona(SCOPE, 0.5, "2026-09-26T10:00:00"))
    extractor.bank.save_persona(_persona(OTHER_SCOPE, 0.6, "2026-09-25T10:00:00"))

    resp = await psych_client.delete("/api/psych/profile")

    assert resp.status_code == 200, resp.text
    assert extractor.bank.get_persona(SCOPE) is None, "生成侧写的画像没被清掉"
    assert extractor.bank.get_persona(OTHER_SCOPE) is None
    assert resp.json().get("cleared") == 2, "响应必须如实报告清了几份"


@pytest.mark.asyncio
async def test_reset_scoped_to_character_leaves_other_characters(psych_client) -> None:
    """带 character_id 时只清该角色的画像（不得顺手扩大清除面）。"""
    extractor = personality_routes._get_pe()
    other_char = f"米彩:{SESSION}"
    extractor.bank.save_persona(_persona(SCOPE, 0.5, "2026-09-26T10:00:00"))
    extractor.bank.save_persona(_persona(other_char, 0.4, "2026-09-26T09:00:00"))

    resp = await psych_client.delete("/api/psych/profile", params={"character_id": CHAR})

    assert resp.status_code == 200, resp.text
    assert extractor.bank.get_persona(SCOPE) is None
    assert extractor.bank.get_persona(other_char) is not None, "越界清除其他角色画像"


@pytest.mark.asyncio
async def test_reset_failure_is_not_reported_as_success(
    psych_client, monkeypatch
) -> None:
    """清除失败不得返回 2xx：旧实现返回 200 + {"status": "failed"}，前端显示成功。"""
    extractor = personality_routes._get_pe()
    extractor.bank.save_persona(_persona(SCOPE, 0.5, "2026-09-26T10:00:00"))

    monkeypatch.setattr(extractor.bank, "clear_users", lambda *a, **k: 0, raising=False)

    resp = await psych_client.delete("/api/psych/profile")

    assert resp.status_code >= 500, (
        f"清除失败必须可见（实得 {resp.status_code} {resp.text}）"
    )
