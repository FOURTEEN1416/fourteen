"""
8 sub-router mount verification — 2026-06-01 refactor.

Each test verifies that endpoints from one of the 8 new sub-routers
(_misc_routes, _chat_routes, _personality_routes, _users_routes,
 _training_routes, _tools_routes, _safety_routes, _clone_routes)
are correctly mounted in the FastAPI app with the right method+path.

Static mount check is the primary proof of refactor correctness:
no DB, no LLM, no auth required, runs in <1s.

One live HTTP smoke test (test_health_endpoint_reachable) proves
create_api_app() produces a working app, not just a static route list.
"""

from __future__ import annotations

import pytest

from api import (
    _chat_routes,
    _clone_routes,
    _misc_routes,
    _personality_routes,
    _safety_routes,
    _tools_routes,
    _training_routes,
    _users_routes,
    app_factory,
    auth,
)


@pytest.fixture(scope="module")
def app():
    """Build the app once per module; bypass auth to keep tests fast."""
    a = app_factory.create_api_app()
    # Bypass X-API-Key check so smoke tests don't need a real key
    a.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    return a


def _app_routes(app) -> set[tuple[str, str]]:
    """Return set of (METHOD, path) for all /api/* routes in the app."""
    routes: set[tuple[str, str]] = set()
    for r in app.routes:
        if hasattr(r, "path") and hasattr(r, "methods") and r.path.startswith("/api/"):
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


# --- 8 sub-router mount checks (parametrized) ---

@pytest.mark.parametrize(
    "module,expected_count,label",
    [
        (_misc_routes, 10, "health/stats/memory/logs/config/channels/routes"),
        (_chat_routes, 10, "chat/session + wechat channels"),
        (_personality_routes, 9, "emotion/persona/psych"),
        (_users_routes, 7, "users/*"),
        (_training_routes, 11, "training/* + proactive/*"),
        (_tools_routes, 5, "tools/* + plugins/*"),
        (_safety_routes, 12, "safety/rag/voice/files/cache"),
        (_clone_routes, 7, "clone/*"),
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


def test_total_contribution_is_71(app):
    """The 8 new sub-routers together contribute exactly 71 endpoints."""
    modules = [
        _misc_routes, _chat_routes, _personality_routes, _users_routes,
        _training_routes, _tools_routes, _safety_routes, _clone_routes,
    ]
    total = sum(len(_sub_router_routes(m)) for m in modules)
    assert total == 71, f"8 sub-routers contribute {total} routes, expected 71"


def test_no_duplicate_endpoints_across_sub_routers():
    """No (METHOD, path) appears in more than one of the 8 new sub-routers."""
    modules = [
        _misc_routes, _chat_routes, _personality_routes, _users_routes,
        _training_routes, _tools_routes, _safety_routes, _clone_routes,
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
    """GET /api/routes should return 200 (proves _misc_routes mounted)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/api/routes")
    assert r.status_code == 200, f"/api/routes returned {r.status_code}: {r.text}"
