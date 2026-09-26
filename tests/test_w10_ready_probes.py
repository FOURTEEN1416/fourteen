"""W10 · /api/ready 就绪判据根治 — 共享健康枚举 + 必需/可降级 + 真实探测。

修复的缺陷（对照 W10 工作单 B）：
- 旧 readiness 用 `health_data.get('status') != 'error'` 判健康，而 HealthChecker
  只产 healthy/degraded/unhealthy —— degraded 与 unhealthy 全被放行；
- 旧 readiness 对组件多数只验「非 None」，从未真实探测 users DB / 记忆库 / 模型配置；
- 部分关键路由组（agent_plane/auth/llm_providers/mimo/admin/invites）挂载失败
  不进 route_mounts —— readiness 改为对应用真实路由表做存在性探测，漏挂即 503；
- liveness /api/health 语义保持不变（进程存活，永远轻量 200）。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import deps
from api.health_routes import health_router
from observability.health import HealthChecker

_PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def api_app():
    """完整工厂 app（与 test_api_routes 同构：真实建表 + 认证旁路）。"""
    import asyncio as _asyncio

    from api.database import User, _async_session, init_db

    async def _seed() -> None:
        await init_db()
        from api.auth_jwt import hash_password

        async with _async_session() as session:
            if await session.get(User, 1) is None:
                session.add(User(
                    email="w10-admin@test.local", username="w10-admin",
                    hashed_password=hash_password("W10Admin#2026"),
                    role="admin", is_active=True, is_verified=True,
                ))
                await session.commit()

    _asyncio.run(_seed())

    from api import app_factory, auth

    a = app_factory.create_api_app()
    a.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    from api.auth_jwt import get_current_user, get_current_user_id, require_role
    a.dependency_overrides[get_current_user_id] = lambda: 1
    a.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": 1, "role": "admin"})()
    a.dependency_overrides[require_role("admin")] = lambda: (1, type("U", (), {"id": 1, "role": "admin"})())
    return a


@pytest.fixture()
def client(api_app):
    return TestClient(api_app)


def _fake_health(status: str, checks: dict | None = None):
    fake = HealthChecker()
    payload = {"status": status, "checks": checks or {}}
    fake.check = lambda: payload  # type: ignore[method-assign]

    async def _async() -> dict:
        return payload

    fake.async_check = _async  # type: ignore[method-assign]
    return fake


class _StubLLM:
    def health_check(self) -> dict:
        return {"configured": True}


def _stub_orch():
    from types import SimpleNamespace

    return SimpleNamespace(components={
        "llm": _StubLLM(), "memory": object(), "emotion": object(), "persona": object(),
    })


class TestSharedHealthEnumGate:
    def test_degraded_health_checker_blocks_ready(self, client, monkeypatch):
        """核心红测：degraded 不得再被当通过（旧代码 `!= 'error'` 会放行）。"""
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health("degraded"), raising=False)
        r = client.get("/api/ready")
        assert r.status_code == 503, f"degraded 被放行: {r.text}"

    def test_unhealthy_health_checker_blocks_ready(self, client, monkeypatch):
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health("unhealthy"), raising=False)
        assert client.get("/api/ready").status_code == 503

    def test_healthy_health_checker_passes_ready(self, client, monkeypatch):
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health("healthy"), raising=False)
        r = client.get("/api/ready")
        assert r.status_code == 200, r.text
        assert r.json()["status"] in ("ready", "degraded")  # 记忆库未初始化的安装允许 degraded

    def test_degradable_component_degradation_allowed(self, client, monkeypatch):
        """可降级组件降级 → 200 但明确标 degraded（不装作全绿）。"""
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health(
            "degraded", {"model_config": {"connected": True, "degraded": True}},
        ), raising=False)
        r = client.get("/api/ready")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "degraded"

    def test_required_component_degradation_blocks(self, client, monkeypatch):
        """必需组件（如 users_db）降级/失联 → 503。"""
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health(
            "degraded", {"users_db": {"connected": False, "error": "timeout"}},
        ), raising=False)
        assert client.get("/api/ready").status_code == 503


class TestLivenessUnchanged:
    def test_liveness_stays_lightweight_200(self, client, monkeypatch):
        """/api/health 是 liveness：组件全坏也保持 200（其语义就是进程存活）。"""
        monkeypatch.setattr(deps, "health", _fake_health("unhealthy"), raising=False)
        monkeypatch.setattr(deps, "orch", None, raising=False)
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "timestamp" in body


class TestRoutePresenceProbes:
    def test_missing_required_routes_fail_ready(self):
        """裸 app 只挂 health_router：关键路由组缺失必须 503（漏挂探测）。"""
        from api.deps import deps as _deps

        _deps.orch = None
        bare = FastAPI()
        bare.include_router(health_router)
        c = TestClient(bare)
        r = c.get("/api/ready")
        assert r.status_code == 503
        checks = r.json()["checks"]
        assert checks.get("routes_auth", {}).get("status") == "fail"
        assert checks.get("routes_agent_plane", {}).get("status") == "fail"

    def test_full_app_required_routes_present(self, client):
        r = client.get("/api/ready")
        checks = r.json()["checks"]
        for group in ("routes_auth", "routes_agent_plane", "routes_llm_providers",
                      "routes_mimo_voice", "routes_admin", "routes_invites"):
            assert checks.get(group, {}).get("status") == "ok", f"{group} 未被探测到"


class TestRealProbes:
    def test_users_db_probe_runs_and_passes(self, client, monkeypatch):
        """真实探测 users DB（SELECT 1），通过则 ok。"""
        monkeypatch.setattr(deps, "orch", _stub_orch(), raising=False)
        monkeypatch.setattr(deps, "health", _fake_health("healthy"), raising=False)
        r = client.get("/api/ready")
        assert r.status_code == 200, r.text
        assert r.json()["checks"]["db_users"]["status"] == "ok"

    def test_users_db_probe_failure_blocks_ready(self, api_app, monkeypatch, tmp_path):
        """users DB 探测真实失败（指向目录路径）→ 503。"""
        from sqlalchemy.ext.asyncio import create_async_engine

        import api.database as database_mod

        broken = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'no' / 'such' / 'ghost.db').as_posix()}")
        monkeypatch.setattr(deps, "health", _fake_health("healthy"), raising=False)
        monkeypatch.setattr(deps, "orch", None, raising=False)
        monkeypatch.setattr(database_mod, "_engine", broken)
        r = TestClient(api_app).get("/api/ready")
        assert r.status_code == 503
        assert r.json()["checks"]["db_users"]["status"] == "fail"
        # 探测必须限时，不得挂死请求
        assert r.elapsed.total_seconds() < 10

    def test_probe_timeout_is_bounded(self, api_app, monkeypatch):
        """挂死的健康检查在有限超时内失败，不拖死 readiness。"""
        import api.health_routes as hr

        fake = HealthChecker()

        async def _hang():
            await asyncio.sleep(30)

        fake.async_check = _hang  # type: ignore[method-assign]
        monkeypatch.setattr(deps, "health", fake, raising=False)
        monkeypatch.setattr(deps, "orch", None, raising=False)
        monkeypatch.setattr(hr, "PROBE_TIMEOUT_SECONDS", 0.2)

        from fastapi import Request

        scope = {"type": "http", "app": api_app, "headers": []}
        request = Request(scope)

        async def _call():
            return await hr.readiness_check(request)

        loop = asyncio.new_event_loop()
        try:
            start = time.monotonic()
            resp = loop.run_until_complete(_call())
            elapsed = time.monotonic() - start
        finally:
            loop.close()
        assert resp.status_code == 503
        assert elapsed < 10, "挂死探测未被限时"


class TestHealthEnumShared:
    def test_health_status_values_are_the_shared_three(self):
        """HealthChecker 产出的 status 只能是共享枚举三值。"""
        hc = HealthChecker()
        assert hc.check()["status"] == "healthy"
        hc.register("x", lambda: {"connected": False})
        assert hc.check()["status"] == "degraded"
        hc.register("y", lambda: 1 / 0)
        assert hc.check()["status"] == "unhealthy"
