"""话题状态机管理端 API 契约测试（2026-10-27 实施计划 Task 5 / §10.1-E）。

覆盖：
- 三个端点的 401/403/200/400/422 语义；
- ``GET /api/topic-state/config`` 纯热读、无调度器依赖；
- ``POST`` partial merge 只改 ``topic_state`` 块，保留其他配置块；
- ``GET /api/topic-state/{session_key}`` 的 active/parked/closed/next_plan；
- 纯数字 user_id 只经微信绑定确定性还原，无绑定 400（不猜键）。

隔离：``utils.topic_state._DB_PATH/_CONFIG_PATH`` 重定向到 ``tmp_path``，
绝不触碰开发机真实 ``data/sqlite.db`` / ``data/scheduler_config.json``。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-topic-api-32chars-ok!!!!")

from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, WechatBinding, get_db
from api.routers.topic_routes import router as topic_router

_ADMIN_ID = 1
_VIEWER_ID = 2
_UNBOUND_ID = 3
_NOW = datetime(2026, 10, 27, 10, 0, 0)


def _build_app(tmp_path):
    db_path = str(tmp_path / "topic_api.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db_override():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, email, role in (
                (_ADMIN_ID, "admin@topic.test", "admin"),
                (_VIEWER_ID, "viewer@topic.test", "viewer"),
                (_UNBOUND_ID, "unbound@topic.test", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=email.split("@")[0],
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            session.add(
                WechatBinding(
                    user_id=_VIEWER_ID,
                    wxid="wxid@im.wechat",
                    nickname="测试微信",
                )
            )
            await session.commit()

    asyncio.run(_init())

    # 只挂话题状态 router：避免 create_api_app() 触发 setup_shisi(run_migrate=True)
    # 写宿主真实 data/sqlite.db（2026-10-28 复核 finding）。真实 app 的挂载完整性
    # 由 tests/test_api_routes.py 的 sub-router 清单覆盖。
    app = FastAPI()
    app.include_router(topic_router)
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine


def _bearer(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    from utils import topic_state

    config_path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(topic_state, "_CONFIG_PATH", config_path, raising=False)
    monkeypatch.setattr(topic_state, "_DB_PATH", tmp_path / "topic_api_state.db", raising=False)
    # 固定墙钟：快照端点不带 now，若用真实时钟，_NOW 创建的 next_plan
    # 会在 24h TTL 后过期 → 测试变成时间炸弹（2026-10-28 复核 finding）。
    monkeypatch.setattr(topic_state, "now_local", lambda: _NOW, raising=False)

    app, client, engine = _build_app(tmp_path)
    yield client, engine, app, config_path
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


def _seed_config(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_config(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════
# 鉴权 + 配置读
# ═══════════════════════════════════════════════════════════


async def test_get_config_401_without_token(api):
    client, _engine, _app, _path = api
    r = await client.get("/api/topic-state/config")
    assert r.status_code == 401, r.text


async def test_get_config_403_viewer(api):
    client, _engine, _app, _path = api
    r = await client.get("/api/topic-state/config", headers=_bearer(_VIEWER_ID))
    assert r.status_code == 403, r.text


async def test_get_config_defaults_without_orchestrator(api):
    from api.deps import deps

    client, _engine, _app, _path = api
    saved = deps.orch
    deps.orch = None
    try:
        r = await client.get("/api/topic-state/config", headers=_bearer(_ADMIN_ID))
    finally:
        deps.orch = saved
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["config"]) == {
        "enabled",
        "llm_judge_enabled",
        "judge_every_turns",
        "daily_llm_budget",
        "active_cap",
        "retention_days",
        "recall_after_hours",
        "recall_max",
        "park_idle_hours",
        "switch_theta1",
        "switch_theta2",
    }
    assert body["config"]["enabled"] is True
    assert body["config"]["llm_judge_enabled"] is True
    assert body["config"]["judge_every_turns"] == 4


# ═══════════════════════════════════════════════════════════
# 配置写：partial merge / 越界 / 跨字段
# ═══════════════════════════════════════════════════════════


async def test_post_partial_merge_preserves_other_blocks(api):
    client, _engine, _app, path = api
    _seed_config(path, {
        "humanize": {"per_char_ms": 60},
        "reply_mode": "novel",
        "topic_state": {"enabled": True, "active_cap": 2},
    })

    r = await client.post(
        "/api/topic-state/config",
        headers=_bearer(_ADMIN_ID),
        json={"active_cap": 3, "recall_after_hours": 12},
    )

    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["active_cap"] == 3
    assert config["recall_after_hours"] == 12
    assert config["enabled"] is True  # 未传键保持原值
    on_disk = _read_config(path)
    assert on_disk["humanize"] == {"per_char_ms": 60}
    assert on_disk["reply_mode"] == "novel"
    assert set(on_disk["topic_state"]) == set(config)


@pytest.mark.parametrize(
    "payload",
    [
        {"active_cap": 4},
        {"judge_every_turns": 2},
        {"daily_llm_budget": 1},
        {"retention_days": 8},
        {"recall_after_hours": 5},
        {"recall_max": 3},
        {"park_idle_hours": 1},
    ],
)
async def test_post_out_of_bounds_422(api, payload):
    client, _engine, _app, _path = api
    r = await client.post(
        "/api/topic-state/config", headers=_bearer(_ADMIN_ID), json=payload,
    )
    assert r.status_code == 422, f"{payload} -> {r.status_code}: {r.text[:300]}"


async def test_post_theta_inversion_400(api):
    client, _engine, _app, path = api
    r = await client.post(
        "/api/topic-state/config",
        headers=_bearer(_ADMIN_ID),
        json={"switch_theta1": 0.8, "switch_theta2": 0.2},
    )
    assert r.status_code == 400, r.text
    assert "switch_theta2" in r.text
    assert not path.exists() or "topic_state" not in _read_config(path)


# ═══════════════════════════════════════════════════════════
# 快照
# ═══════════════════════════════════════════════════════════


async def test_get_snapshot_disabled_empty(api):
    client, _engine, _app, _path = api
    _seed_config(_path, {"topic_state": {"enabled": False}})
    r = await client.get(
        "/api/topic-state/2:wxid@im.wechat", headers=_bearer(_ADMIN_ID),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["enabled"] is False
    assert body["active"] == []
    assert body["parked"] == []
    assert body["closed"] == []
    assert body["next_plan"] is None


async def test_get_snapshot_active_parked_closed_and_plan(api):
    from utils import topic_state

    client, _engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)
    active_key = "2:active@im.wechat"
    parked_key = "2:parked@im.wechat"
    closed_key = "2:closed@im.wechat"
    plan_key = "2:plan@im.wechat"

    topic_state.update_after_turn(
        active_key, "default", "我明天去面试", now=_NOW, turn_id="a1",
    )
    topic_state.update_after_turn(
        parked_key, "default", "周末去爬山", now=_NOW, turn_id="p1",
    )
    for index in range(3):
        topic_state.update_after_turn(
            parked_key, "default", "嗯", now=_NOW, turn_id=f"p{index + 2}",
        )
    topic_state.update_after_turn(
        closed_key, "default", "我明天去面试", now=_NOW, turn_id="c1",
    )
    topic_state.update_after_turn(
        closed_key, "default", "面试弄完了", now=_NOW, turn_id="c2",
    )
    topic_state.save_next_plan(
        plan_key, "default", "面试", "问问结果", planned_at=_NOW,
    )

    async def _snap(key: str) -> dict:
        response = await client.get(f"/api/topic-state/{key}", headers=_bearer(_ADMIN_ID))
        assert response.status_code == 200, response.text
        return response.json()

    active = await _snap(active_key)
    assert [t["title"] for t in active["active"]] == ["我明天去面试"]
    assert active["parked"] == []
    assert active["closed"] == []

    parked = await _snap(parked_key)
    assert parked["active"] == []
    assert [t["title"] for t in parked["parked"]] == ["周末去爬山"]
    # park/timeout 必须把本轮衰减后的 salience 落库（2026-10-28 复核 finding）
    assert parked["parked"][0]["salience"] == pytest.approx(0.5 * 0.9 ** 3)

    closed = await _snap(closed_key)
    assert [t["title"] for t in closed["closed"]] == ["我明天去面试"]

    planned = await _snap(plan_key)
    assert planned["next_plan"]["topic"] == "面试"
    assert planned["next_plan"]["intent"] == "问问结果"
    assert "text" not in planned


async def test_numeric_user_id_resolved_to_full_key(api):
    client, _engine, _app, _path = api
    r = await client.get("/api/topic-state/2", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    assert r.json()["session_key"] == "2:wxid@im.wechat"


async def test_numeric_user_id_without_binding_400(api):
    client, _engine, _app, _path = api
    r = await client.get("/api/topic-state/3", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 400, r.text
    assert "未绑定微信" in r.text


async def test_web_full_key_requires_full_key(api):
    client, _engine, _app, _path = api
    r = await client.get(
        "/api/topic-state/2:web:8hexdemo", headers=_bearer(_ADMIN_ID),
    )
    assert r.status_code == 200, r.text
    assert r.json()["session_key"] == "2:web:8hexdemo"


# ═══════════════════════════════════════════════════════════
# 读面归属：admin 任意键 / 普通用户仅本人会话
# （服务端从 token 派生身份键，客户端自报归属不得成为依据）
# ═══════════════════════════════════════════════════════════


async def test_snapshot_401_without_token(api):
    client, _engine, _app, _path = api
    r = await client.get("/api/topic-state/2:wxid@im.wechat")
    assert r.status_code == 401, r.text


async def test_viewer_reads_own_bound_key_without_text(api):
    """isolation ①：viewer 传本人绑定键 → 200，且载荷不含 prompt 文本。"""
    from utils import topic_state

    client, _engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)
    topic_state.update_after_turn(
        "2:wxid@im.wechat", "default", "我明天去面试", now=_NOW, turn_id="own-1",
    )

    r = await client.get(
        "/api/topic-state/2:wxid@im.wechat", headers=_bearer(_VIEWER_ID),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_key"] == "2:wxid@im.wechat"
    assert [t["title"] for t in body["active"]] == ["我明天去面试"]
    assert "text" not in body


async def test_viewer_other_user_key_404(api):
    """isolation ②：viewer 传他人完整键 → 404（不区分无权限/不存在）。"""
    client, _engine, _app, _path = api
    r = await client.get(
        "/api/topic-state/9:wxid@im.wechat", headers=_bearer(_VIEWER_ID),
    )
    assert r.status_code == 404, r.text


async def test_viewer_numeric_id_404_admin_numeric_id_200(api):
    """isolation ③：普通用户分支不解析数字 id；admin 保留 R7 还原语义。"""
    client, _engine, _app, _path = api

    denied = await client.get("/api/topic-state/2", headers=_bearer(_VIEWER_ID))
    assert denied.status_code == 404, denied.text

    allowed = await client.get("/api/topic-state/2", headers=_bearer(_ADMIN_ID))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["session_key"] == "2:wxid@im.wechat"


async def test_viewer_active_web_session_whitelisted_others_404(api):
    """白名单第二支：本人活跃 web 会话可读，他人活跃会话 404、admin 可读。"""
    from api.deps import deps
    from api.session_manager import SessionManager

    client, _engine, _app, _path = api
    sessions = SessionManager()
    own = sessions.create_session(str(_VIEWER_ID), "web")
    other = sessions.create_session(str(_ADMIN_ID), "web")
    saved = deps.sessions
    deps.sessions = sessions
    try:
        allowed = await client.get(
            f"/api/topic-state/{own}", headers=_bearer(_VIEWER_ID),
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["session_key"] == own

        denied = await client.get(
            f"/api/topic-state/{other}", headers=_bearer(_VIEWER_ID),
        )
        assert denied.status_code == 404, denied.text

        admin_read = await client.get(
            f"/api/topic-state/{other}", headers=_bearer(_ADMIN_ID),
        )
        assert admin_read.status_code == 200, admin_read.text
    finally:
        deps.sessions = saved


async def test_viewer_reads_real_web_session_key_without_session_manager(api):
    """R4-1：真实 web 键 ``N:web:hex`` 由 ``resolve_owned_session`` 现场铸造，
    从不登记 SessionManager / 绑定表 → 必须按 token 派生的 owner 前缀可读。

    旧实现只认 SessionManager 登记键，本用例（sessions=None）恒 404。
    """
    from api.deps import deps
    from utils import topic_state

    client, _engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)
    own_key = "2:web:1a2b3c4d"
    other_key = "1:web:deadbeef"
    topic_state.update_after_turn(
        own_key, "default", "我明天去面试", now=_NOW, turn_id="web-own-1",
    )
    topic_state.update_after_turn(
        other_key, "default", "换个话题吧", now=_NOW, turn_id="web-other-1",
    )

    saved = deps.sessions
    deps.sessions = None  # 证明确实不依赖 SessionManager 登记
    try:
        allowed = await client.get(
            f"/api/topic-state/{own_key}", headers=_bearer(_VIEWER_ID),
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["session_key"] == own_key
        assert [t["title"] for t in allowed.json()["active"]] == ["我明天去面试"]

        denied = await client.get(
            f"/api/topic-state/{other_key}", headers=_bearer(_VIEWER_ID),
        )
        assert denied.status_code == 404, denied.text

        admin_read = await client.get(
            f"/api/topic-state/{other_key}", headers=_bearer(_ADMIN_ID),
        )
        assert admin_read.status_code == 200, admin_read.text
    finally:
        deps.sessions = saved


async def test_viewer_reads_all_wechat_bindings_not_only_latest(api):
    """R4-2：多条微信绑定的会话键全部可读（旧实现只取 bound_at 最新一条）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.database import WechatBinding
    from utils import topic_state

    client, engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            WechatBinding(
                user_id=_VIEWER_ID,
                wxid="wxid-second@im.wechat",
                nickname="第二绑定",
                bound_at=datetime(2026, 10, 1, 9, 0, 0),
            )
        )
        await session.commit()

    primary = "2:wxid@im.wechat"
    secondary = "2:wxid-second@im.wechat"
    topic_state.update_after_turn(
        primary, "default", "主绑定会话", now=_NOW, turn_id="bind-1",
    )
    topic_state.update_after_turn(
        secondary, "default", "副绑定会话", now=_NOW, turn_id="bind-2",
    )

    for key, title in ((primary, "主绑定会话"), (secondary, "副绑定会话")):
        response = await client.get(
            f"/api/topic-state/{key}", headers=_bearer(_VIEWER_ID),
        )
        assert response.status_code == 200, f"{key} -> {response.status_code}: {response.text}"
        assert [t["title"] for t in response.json()["active"]] == [title]


class _ExplodingSessionManager:
    """``get_active_sessions`` 必抛错：模拟会话管理器故障。"""

    def get_active_sessions(self, user_id: str = "") -> list[str]:
        raise RuntimeError("session manager unavailable")


async def test_owned_session_keys_fail_closed_on_sessions_error(api):
    """R4-3：读活跃会话抛错 → 异常被吞、绑定键仍可读、他人键仍 404。

    fail-closed = 只丢 SessionManager 支（收窄），不向客户端回 500。
    """
    from api.deps import deps
    from utils import topic_state

    client, _engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)
    topic_state.update_after_turn(
        "2:wxid@im.wechat", "default", "绑定会话", now=_NOW, turn_id="fc-1",
    )
    topic_state.update_after_turn(
        "2:web:feedface", "default", "网页会话", now=_NOW, turn_id="fc-2",
    )

    saved = deps.sessions
    deps.sessions = _ExplodingSessionManager()
    try:
        bound = await client.get(
            "/api/topic-state/2:wxid@im.wechat", headers=_bearer(_VIEWER_ID),
        )
        assert bound.status_code == 200, bound.text
        assert [t["title"] for t in bound.json()["active"]] == ["绑定会话"]

        web = await client.get(
            "/api/topic-state/2:web:feedface", headers=_bearer(_VIEWER_ID),
        )
        assert web.status_code == 200, web.text

        other = await client.get(
            "/api/topic-state/1:web:feedface", headers=_bearer(_VIEWER_ID),
        )
        assert other.status_code == 404, other.text
    finally:
        deps.sessions = saved


async def test_unbound_viewer_whitelist_empty_404(api):
    """无微信绑定且无活跃会话 → 白名单为空，任意键 404（不猜键）。"""
    client, _engine, _app, _path = api
    r = await client.get(
        "/api/topic-state/3:wxid@im.wechat", headers=_bearer(_UNBOUND_ID),
    )
    assert r.status_code == 404, r.text


async def test_admin_reads_any_key_without_text(api):
    """admin 保留：任意（含他人）完整键可读，载荷同样不含 text。"""
    from utils import topic_state

    client, _engine, _app, _path = api
    topic_state.write_topic_config(enabled=True)
    topic_state.update_after_turn(
        "1:other@im.wechat", "default", "换个话题吧", now=_NOW, turn_id="adm-1",
    )
    r = await client.get(
        "/api/topic-state/1:other@im.wechat", headers=_bearer(_ADMIN_ID),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["title"] for t in body["active"]] == ["换个话题吧"]
    assert "text" not in body
