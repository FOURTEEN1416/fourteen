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
