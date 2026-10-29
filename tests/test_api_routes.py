"""
8 sub-router mount verification — migrated to api.routers/

Each test verifies that endpoints from one of the 8 sub-routers
(api.routers.misc_routes, chat_routes, personality_routes, users_routes,
 training_routes, tools_routes, safety_routes, clone_routes)
are correctly mounted in the FastAPI app with the right method+path.

Static mount check is the primary proof of refactor correctness:
no DB, no LLM, no auth required, runs in <1s.

One live HTTP smoke test (test_health_endpoint_reachable) proves
create_api_app() produces a working app, not just a static route list.
"""

from __future__ import annotations

import pytest

from api import (
    app_factory,
    auth,
)
from api.routers import (
    chat_routes,
    clone_routes,
    misc_routes,
    personality_routes,
    safety_routes,
    tools_routes,
    topic_routes,
    training_routes,
    users_routes,
)


@pytest.fixture(scope="module")
def app():
    """Build the app once per module; bypass auth to keep tests fast."""
    # require_role("admin") 是工厂调用（每次返回新函数），dependency_overrides 无法按键覆盖
    # （FastAPI 工厂陷阱）。CI 干净环境因此必炸 no such table: users。
    # 根治：真实建表 + 种子 id=1 admin，让真实依赖链走通（本地/CI 行为一致）。
    import asyncio
    from pathlib import Path as _Path

    _Path("data").mkdir(parents=True, exist_ok=True)

    from api.database import User, _async_session, init_db

    async def _seed() -> None:
        await init_db()
        from api.auth_jwt import hash_password

        async with _async_session() as session:
            exists = await session.get(User, 1)
            if exists is None:
                session.add(
                    User(
                        email="ci-admin@test.local",
                        username="ci-admin",
                        hashed_password=hash_password("CiAdmin#2026"),
                        role="admin",
                        is_active=True,
                        is_verified=True,
                    )
                )
                await session.commit()

    asyncio.run(_seed())

    a = app_factory.create_api_app()
    # Bypass X-API-Key check so smoke tests don't need a real key
    a.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    # Bypass JWT-based role checks for admin-only endpoints (e.g. /api/routes)
    from api.auth_jwt import get_current_user, get_current_user_id, require_role
    a.dependency_overrides[get_current_user_id] = lambda: 1
    a.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": 1, "role": "admin"})()
    a.dependency_overrides[require_role("admin")] = lambda: (1, type("U", (), {"id": 1, "role": "admin"})())
    return a


def _flatten_app_routes(app) -> list:
    """Flatten app.routes — handles FastAPI 0.139+ _IncludedRouter wrappers."""
    flat = []
    for r in app.routes:
        if hasattr(r, "path") and hasattr(r, "methods"):
            flat.append(r)
        elif hasattr(r, "original_router"):
            # FastAPI 0.139+ wraps included routers in _IncludedRouter
            for sub in r.original_router.routes:
                if hasattr(sub, "path") and hasattr(sub, "methods"):
                    flat.append(sub)
    return flat


def _app_routes(app) -> set[tuple[str, str]]:
    """Return set of (METHOD, path) for all /api/* routes in the app."""
    routes: set[tuple[str, str]] = set()
    for r in _flatten_app_routes(app):
        if r.path.startswith("/api/"):
            for m in r.methods:
                if m != "HEAD":  # FastAPI auto-adds HEAD
                    routes.add((m, r.path))
    return routes


def _sub_router_routes(module) -> set[tuple[str, str]]:
    """Return set of (METHOD, path) for a sub-router's /api/* routes."""
    routes: set[tuple[str, str]] = set()
    for r in module.router.routes:
        if hasattr(r, "path") and hasattr(r, "methods") and r.path.startswith("/api/"):
            for m in r.methods:
                if m != "HEAD":
                    routes.add((m, r.path))
    return routes


def test_app_creates_and_has_at_least_71_api_routes(app):
    """Sanity: create_api_app() returns a FastAPI app with >=71 /api/* routes."""
    routes = _app_routes(app)
    assert routes, "app has no /api/* routes"
    assert len(routes) >= 71, f"app has only {len(routes)} /api/* routes, expected >=71"


def test_control_plane_critical_routes_are_mounted(app):
    """Regression: the control plane must not silently lose optional route groups."""
    actual = _app_routes(app)
    required = {
        ("GET", "/api/characters"),
        ("POST", "/api/characters"),
        ("GET", "/api/characters/{character_id}/favorites"),
        ("GET", "/api/characters/{character_id}/storyline"),
        ("GET", "/api/characters/{character_id}/voice"),
        ("GET", "/api/emotion/params"),
        ("GET", "/api/wechat/bindings"),
        ("POST", "/api/wechat/bind"),
    }
    assert not required - actual, f"missing critical routes: {sorted(required - actual)}"


# --- 9 sub-router mount checks (parametrized) ---

@pytest.mark.parametrize(
    "module,expected_count,label",
    [
        (misc_routes, 17, "stats/memory/diary/dates/meta/logs/config/channels/routes/user-llm-config（10-29 +user/llm-config/test BYOK 连通性测试）"),
        (chat_routes, 11, "chat/session + wechat channels"),
        (personality_routes, 10, "emotion/persona/psych（08-31? +emotion/distribution SP-1）"),
        (users_routes, 7, "users/*"),
        (training_routes, 23, "training/* + proactive/*（…10-27 +humanize/config DELETE；10-29 +user/humanize GET/POST/PUT/DELETE 用户自助拟人节奏 NEW-34）"),
        (tools_routes, 6, "tools/* + plugins/* + health"),
        (safety_routes, 12, "safety/rag/voice/files/cache"),
        (clone_routes, 8, "clone/* (2026-08-27 剥离 /api/clone/preview 死路径)"),
        (topic_routes, 3, "topic-state/config GET/POST + 会话快照"),
    ],
)
def test_sub_router_mounts_all_endpoints(app, module, expected_count, label):
    """Each sub-router must contribute exactly N endpoints to the app."""
    expected = _sub_router_routes(module)
    actual = _app_routes(app)
    missing = expected - actual
    assert not missing, (
        f"{module.__name__} ({label}) has {len(missing)} endpoints NOT mounted:\n"
        + "\n".join(f"  {m:6s} {p}" for m, p in sorted(missing))
    )
    assert len(expected) == expected_count, (
        f"{module.__name__} has {len(expected)} routes, expected {expected_count}"
    )


def test_total_contribution_is_92(app):
    """The 9 new sub-routers together contribute exactly 92 endpoints.

    2026-08-27: 微信克隆 Option B 剥离 /api/clone/preview，clone_routes 由 9 端点变 8 端点
    2026-08-28: training/extract 移除（9→8）；proactive +config(GET)/send/pause 手动控制（8→11）；misc +diary/important-dates×2/meta（11→15）
    2026-09-17: +knowledge/collect-config GET/POST（Vault 定期采集 web 开关，11→13；总 81→83）
    2026-10-16: +humanize/config GET/POST/PUT（拟人度配置读写，13→16；总 83→86）
    2026-10-21: +user/reply-mode GET/POST（用户自助回复模式 R7，16→18；总 86→88）
    2026-10-27: +humanize/config DELETE（按用户覆盖清除，18→19；总 88→89）
    2026-10-27: +topic-state/config GET/POST + 会话快照（新增 topic_routes 3 端点；总 89→92）
    2026-10-29: +user/llm-config/test（BYOK 连通性测试，misc 16→17；总 92→93）+user/humanize GET/POST/DELETE（用户自助拟人节奏，training 19→22；总 93→97）
    """
    modules = [
        misc_routes, chat_routes, personality_routes, users_routes,
        training_routes, tools_routes, safety_routes, clone_routes,
        topic_routes,
    ]
    total = sum(len(_sub_router_routes(m)) for m in modules)
    assert total == 97, f"9 sub-routers contribute {total} routes, expected 97"


def test_no_duplicate_endpoints_across_sub_routers():
    """No (METHOD, path) appears in more than one of the 9 new sub-routers."""
    modules = [
        misc_routes, chat_routes, personality_routes, users_routes,
        training_routes, tools_routes, safety_routes, clone_routes,
        topic_routes,
    ]
    seen: dict[tuple[str, str], str] = {}
    for mod in modules:
        for m, p in _sub_router_routes(mod):
            if (m, p) in seen:
                pytest.fail(
                    f"duplicate {m} {p} in {mod.__name__} and {seen[(m, p)]}"
                )
            seen[(m, p)] = mod.__name__


def test_main_routes_residual_is_zero():
    """main_routes.py is shrunk to ~95 lines: 6 models + 4 constants + helper + empty router."""
    import re
    from pathlib import Path

    src = Path("api/main_routes.py").read_text(encoding="utf-8")
    endpoints = re.findall(r"@router\.(get|post|put|delete|patch)\(", src)
    assert len(endpoints) == 0, (
        f"main_routes.py still has {len(endpoints)} endpoints, expected 0 (all moved)"
    )


# --- Live HTTP smoke test (proves app actually starts, not just a route list) ---

def test_health_endpoint_reachable(app):
    """GET /api/health should return 200 (proves the app actually runs)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200, f"/api/health returned {r.status_code}: {r.text}"


def test_routes_endpoint_reachable(app):
    """GET /api/routes should return 200 (proves misc_routes mounted)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/api/routes")
    assert r.status_code == 200, f"/api/routes returned {r.status_code}: {r.text}"
