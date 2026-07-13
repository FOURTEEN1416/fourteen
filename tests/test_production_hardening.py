"""Regression tests for production configuration and API hardening."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


def test_ai_gf_env_enables_production_mode() -> None:
    from api.runtime_config import is_production

    with patch.dict(
        "os.environ",
        {"AI_GF_ENV": "prod", "ENV": "", "APP_ENV": ""},
        clear=False,
    ):
        assert is_production() is True


def test_database_url_accepts_documented_environment_name() -> None:
    from api.runtime_config import get_database_url

    with patch.dict(
        "os.environ",
        {
            "DATABASE_URL": "postgresql://user:pass@localhost:5432/app",
            "APP_DATABASE_URL": "",
        },
        clear=False,
    ):
        assert get_database_url() == "postgresql+asyncpg://user:pass@localhost:5432/app"


@pytest.mark.asyncio
async def test_global_rate_limit_is_enforced_when_slowapi_is_installed() -> None:
    from api.app_factory import create_api_app

    with patch.dict(
        "os.environ",
        {
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_PER_MINUTE": "2",
            "API_KEY_ENABLED": "false",
        },
        clear=False,
    ):
        app = create_api_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://rate-limit-test",
        ) as client:
            statuses = [(await client.get("/api/health")).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/mimo/status"),
        ("GET", "/api/demo/memory/recall"),
        ("GET", "/api/demo/memory/visualization"),
    ],
)
async def test_sensitive_routes_require_api_key(method: str, path: str) -> None:
    from api.app_factory import create_api_app

    with patch.dict(
        "os.environ",
        {
            "API_KEY_ENABLED": "true",
            "API_KEY": "test-api-key",
            "RATE_LIMIT_ENABLED": "false",
        },
        clear=False,
    ):
        app = create_api_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://auth-test",
        ) as client:
            response = await client.request(method, path)

    assert response.status_code == 401
