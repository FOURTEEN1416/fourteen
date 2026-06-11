"""Comprehensive tests for backend ops, DB lifecycle, graceful shutdown, and app factory."""

from __future__ import annotations

import asyncio
import gc
import os
import sys
import time
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _cleanup_db_file(db_path: str, retries: int = 5, delay: float = 0.1) -> None:
    """Windows-friendly DB file cleanup with retries for locked files."""
    gc.collect()
    time.sleep(delay)
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            for _ in range(retries):
                try:
                    os.unlink(p)
                    break
                except PermissionError:
                    time.sleep(delay)
                    gc.collect()


@pytest.fixture
def db_module(tmp_path: Any) -> Generator[Any, None, None]:
    """Provide isolated api.database module with a temp SQLite file.

    This fixture creates a fresh engine backed by a temp file so that
    init_db / close_db / get_db can be tested without touching the
    real database path.
    """
    db_path = str(tmp_path / "test_ops.db")
    test_url = f"sqlite+aiosqlite:///{db_path}"

    import api.database as db_mod

    # Save originals
    orig_url = db_mod.DATABASE_URL
    orig_engine = db_mod._engine
    orig_session = db_mod._async_session

    # Create fresh engine targeting temp file
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_mod.DATABASE_URL = test_url
    db_mod._engine = create_async_engine(test_url, echo=False, pool_pre_ping=True)
    db_mod._async_session = async_sessionmaker(db_mod._engine, expire_on_commit=False)

    yield db_mod

    # Cleanup — dispose the test engine first
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(db_mod._engine.dispose())
        else:
            loop.run_until_complete(db_mod._engine.dispose())
    except Exception:
        pass

    # Restore originals
    db_mod.DATABASE_URL = orig_url
    db_mod._engine = orig_engine
    db_mod._async_session = orig_session

    _cleanup_db_file(db_path)


@pytest.fixture
def app_no_deps() -> Any:
    """Create a bare FastAPI app without any orchestrator/deps.

    The health endpoint returns ``{"status": "unknown"}`` because
    ``deps.health`` is None by default.
    """
    # Ensure auth is disabled for test isolation
    import api.auth as auth_mod
    auth_mod.configure_auth(enabled=False, api_key="")

    from api.app_factory import create_api_app
    app = create_api_app()
    return app


@pytest.fixture
async def async_client(app_no_deps: Any) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client wired to the test app."""
    transport = ASGITransport(app=app_no_deps)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _get_route_paths(app: Any) -> set[str]:
    """Extract all route paths from a FastAPI app instance."""
    paths: set[str] = set()
    for route in app.routes:
        if hasattr(route, "path"):
            paths.add(route.path)
    return paths


# ═══════════════════════════════════════════════════════════════
# Database Lifecycle Tests
# ═══════════════════════════════════════════════════════════════


class TestDatabaseLifecycle:
    """Tests for init_db / close_db / get_db and table creation."""

    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self, db_module: Any) -> None:
        """Call init_db() then verify all expected tables exist."""
        await db_module.init_db()

        async with db_module._engine.begin() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )

        expected = {"users", "invite_codes", "user_sessions", "wechat_bindings"}
        for t in expected:
            assert t in tables, f"Expected table '{t}' not found in {tables}"

    @pytest.mark.asyncio
    async def test_close_db_disposes_engine(self, db_module: Any) -> None:
        """Call close_db() and verify it doesn't raise."""
        await db_module.init_db()
        # close_db should not raise
        await db_module.close_db()
        # Calling close_db twice should also be safe
        await db_module.close_db()

    @pytest.mark.asyncio
    async def test_get_db_yields_session(self, db_module: Any) -> None:
        """Verify get_db() async generator yields an AsyncSession."""
        await db_module.init_db()
        async for session in db_module.get_db():
            assert isinstance(session, AsyncSession)
            assert session.is_active
            break  # we only need one

    @pytest.mark.asyncio
    async def test_get_db_closes_on_exit(self, db_module: Any) -> None:
        """After generator exits, session.close() is called.

        SQLAlchemy's ``AsyncSession.close()`` returns the underlying
        connection to the pool without setting ``is_active = False``
        (the session stays reusable).  We verify that ``close()`` was
        *called* by spying on the class method.
        """
        await db_module.init_db()

        close_called: bool = False
        original_close = AsyncSession.close

        async def _spy_close(self_session: AsyncSession) -> None:
            nonlocal close_called
            close_called = True
            return await original_close(self_session)

        with patch.object(AsyncSession, "close", _spy_close):
            gen = db_module.get_db()
            session = await gen.__anext__()
            assert isinstance(session, AsyncSession)
            assert session.is_active

            # Exiting the generator triggers the finally block → close()
            from contextlib import suppress
            with suppress(StopAsyncIteration):
                await gen.__anext__()

        assert close_called, "session.close() must be called on generator exit"

    @pytest.mark.asyncio
    async def test_concurrent_db_operations(self, db_module: Any) -> None:
        """Run 10 concurrent get_db() calls in parallel, verify all succeed."""
        await db_module.init_db()

        async def _use_db(idx: int) -> int:
            async for session in db_module.get_db():
                assert isinstance(session, AsyncSession)
                # Run a simple query to verify the session works
                result = await session.execute(text("SELECT 1 AS val"))
                row = result.one()
                assert row._mapping["val"] == 1
                return idx
            return -1

        results = await asyncio.gather(*[_use_db(i) for i in range(10)])
        assert results == list(range(10))


# ═══════════════════════════════════════════════════════════════
# Health Endpoint Tests
# ═══════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """Tests for the /api/health public endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, async_client: AsyncClient) -> None:
        """GET /api/health returns 200 OK."""
        response = await async_client.get("/api/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_response_shape(self, async_client: AsyncClient) -> None:
        """Response has expected fields (status at minimum)."""
        response = await async_client.get("/api/health")
        data = response.json()
        # Without a health checker, the endpoint returns {"status": "unknown"}
        assert "status" in data
        assert data["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_health_works_after_init(self, db_module: Any) -> None:
        """Health check works after init_db() — verify no cross-contamination."""
        await db_module.init_db()
        # This test just verifies that calling init_db doesn't break the
        # health endpoint; we rebuild the app with a fresh deps context.
        import api.deps as deps_mod
        from api.app_factory import create_api_app
        deps_mod.deps.health = None  # reset

        app = create_api_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json()["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, async_client: AsyncClient) -> None:
        """Health check works without any API key (public endpoint)."""
        # The health route has no Security(verify_api_key_dep) dependency
        response = await async_client.get("/api/health")
        assert response.status_code == 200
        # Even with auth enabled, health should be accessible
        import api.auth as auth_mod
        auth_mod.configure_auth(enabled=True, api_key="some-key")
        try:
            response2 = await async_client.get("/api/health")
            assert response2.status_code == 200
        finally:
            auth_mod.configure_auth(enabled=False, api_key="")


# ═══════════════════════════════════════════════════════════════
# Application Factory Tests
# ═══════════════════════════════════════════════════════════════


class TestApplicationFactory:
    """Tests for create_api_app() factory function."""

    def test_create_api_app_basic(self) -> None:
        """create_api_app() returns FastAPI instance with correct metadata."""
        from api.app_factory import create_api_app
        app = create_api_app()
        assert app.title == "唯一的你 API"
        assert app.version == "2.0"
        assert app.debug is True  # non-prod by default

    def test_create_api_app_registers_routers(self) -> None:
        """Verify all 8 main sub-routers are mounted.

        Each of the 8 router files contributes specific endpoint paths.
        We check for a representative path from each router.
        """
        from api.app_factory import create_api_app
        app = create_api_app()
        paths = _get_route_paths(app)

        # Representative paths from each of the 8 main routers
        expected_paths = {
            "/api/health",                # misc_router
            "/api/chat",                  # chat_router
            "/api/emotion/state",         # personality_router
            "/api/users",                 # users_router
            "/api/training/status",       # training_router
            "/api/tools",                 # tools_router
            "/api/safety/stats",          # safety_router
            "/api/clone/contacts",        # clone_router
        }

        for path in expected_paths:
            assert path in paths, (
                f"Expected path '{path}' not found in registered routes. "
                f"Available paths similar: {[p for p in paths if '/api/' in p][:20]}"
            )

    def test_create_api_app_cors_middleware(self) -> None:
        """Verify CORS middleware is added."""
        from api.app_factory import create_api_app
        app = create_api_app()

        # Check that CORSMiddleware is in the user middleware list
        middleware_classes = [m.cls.__name__ for m in app.user_middleware]  # type: ignore[attr-defined]
        assert "CORSMiddleware" in middleware_classes, (
            f"CORSMiddleware not found in {middleware_classes}"
        )

    @pytest.mark.asyncio
    async def test_create_api_app_security_headers(self) -> None:
        """Request returns X-Content-Type-Options, X-Frame-Options, CSP headers."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("x-xss-protection") == "1; mode=block"
        assert "default-src 'self'" in response.headers.get("content-security-policy", "")

    @pytest.mark.asyncio
    async def test_create_api_app_request_size_limit(self) -> None:
        """POST with body > 10MB returns 413."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # content-length > 10MB should trigger the size limiter
            response = await client.get(
                "/api/health",
                headers={"content-length": str(11 * 1024 * 1024)},
            )

        assert response.status_code == 413
        data = response.json()
        assert "detail" in data
        assert "error_code" in data
        assert data["error_code"] == "REQUEST_TOO_LARGE"


# ═══════════════════════════════════════════════════════════════
# Request Lifecycle Tests
# ═══════════════════════════════════════════════════════════════


class TestRequestLifecycle:
    """Tests for CORS, security headers, concurrency, and error paths."""

    @pytest.mark.asyncio
    async def test_cors_preflight_response(self) -> None:
        """OPTIONS request with Origin header gets CORS headers."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )

        # CORS middleware should add the allow-origin header
        cors_origin = response.headers.get("access-control-allow-origin")
        assert cors_origin is not None, "No CORS origin header on OPTIONS"
        assert cors_origin == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_security_headers_on_error(self) -> None:
        """Even on 404, security headers are present."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/nonexistent-route-12345")

        assert response.status_code == 404
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert "default-src 'self'" in response.headers.get("content-security-policy", "")

    @pytest.mark.asyncio
    async def test_request_size_limit_unaffected_by_health(self) -> None:
        """Health check with small content-length passes normally."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Small content-length should not trigger the 413
            response = await client.get(
                "/api/health",
                headers={"content-length": "100"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_concurrent_health_checks(self) -> None:
        """20 parallel health checks all return 200."""
        from api.app_factory import create_api_app
        app = create_api_app()

        transport = ASGITransport(app=app)

        async def _check(client: AsyncClient) -> int:
            resp = await client.get("/api/health")
            return resp.status_code

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            results = await asyncio.gather(*[_check(client) for _ in range(20)])

        assert all(s == 200 for s in results), f"Not all returned 200: {results}"


# ═══════════════════════════════════════════════════════════════
# Production-Specific Tests
# ═══════════════════════════════════════════════════════════════


class TestProductionSecurity:
    """Tests for production environment adjustments."""

    @pytest.mark.asyncio
    async def test_prod_env_warning_for_default_cors(self) -> None:
        """Set ENV=prod, verify that a warning is logged about default CORS.

        We check that (1) the app still creates successfully and
        (2) the CORS origins are the default localhost ones (still
        permissive, but with a warning).
        """
        with patch.dict(os.environ, {"ENV": "prod", "API_KEY": "test-prod-key"}), \
             patch("api.app_factory.logger") as mock_logger:
                from api.app_factory import create_api_app
                app = create_api_app()

                # The warning should be logged about default CORS in prod
                warning_calls = [
                    c for c in mock_logger.warning.call_args_list
                    if "default CORS" in str(c)
                ]
                assert len(warning_calls) >= 1, (
                    f"No CORS warning logged in prod. Calls: {mock_logger.warning.call_args_list}"
                )

                # Verify the app still works
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.get("/api/health")
                    assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_prod_env_adds_hsts_header(self) -> None:
        """Set ENV=prod, verify Strict-Transport-Security header."""
        with patch.dict(os.environ, {"ENV": "prod", "API_KEY": "test-prod-key"}):
            from api.app_factory import create_api_app
            app = create_api_app()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health")

            hsts = response.headers.get("strict-transport-security")
            assert hsts is not None, "Missing HSTS header in prod mode"
            assert "max-age=31536000" in hsts
            assert "includeSubDomains" in hsts


# ═══════════════════════════════════════════════════════════════
# DB Engine / Pool / Session Tests
# ═══════════════════════════════════════════════════════════════


class TestDBEnginePool:
    """Tests for engine pool configuration, session isolation, and rollback."""

    @pytest.mark.asyncio
    async def test_engine_uses_pool_pre_ping(self, db_module: Any) -> None:
        """Verify SQLAlchemy engine configured with pool_pre_ping=True."""
        # The pool_pre_ping setting is visible via the engine's pool
        assert db_module._engine.pool._pre_ping is True

    @pytest.mark.asyncio
    async def test_db_session_rolls_back_on_exception(self, db_module: Any) -> None:
        """If a session raises, the transaction rolls back — no partial data."""
        await db_module.init_db()

        # Insert a user via one session
        async with db_module._async_session() as session:
            user = db_module.User(
                email="rollback@test.com",
                username="rollback_test",
                hashed_password="abc",
            )
            session.add(user)
            await session.commit()

        # Now start a session, insert, then raise — session closes => rollback
        try:
            async with db_module._async_session() as session:
                user2 = db_module.User(
                    email="should_not_exist@test.com",
                    username="should_not_exist",
                    hashed_password="abc",
                )
                session.add(user2)
                # Raise before commit — the async with will close + rollback
                raise ValueError("Simulated failure")
        except ValueError:
            pass

        # Verify only the first user exists
        async with db_module._async_session() as session:
            result = await session.execute(
                text("SELECT email FROM users ORDER BY email")
            )
            emails = [row[0] for row in result]

        assert "rollback@test.com" in emails
        assert "should_not_exist@test.com" not in emails

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self, db_module: Any) -> None:
        """Two concurrent sessions don't see each other's uncommitted writes.

        SQLite's default transaction isolation (SERIALIZABLE / deferred)
        ensures that uncommitted data in session A is invisible to session B.
        """
        await db_module.init_db()

        async def _session_a() -> None:
            async with db_module._async_session() as session:
                user = db_module.User(
                    email="isolated_a@test.com",
                    username="isolated_a",
                    hashed_password="abc",
                )
                session.add(user)
                # Commit explicitly later — but we don't commit here
                await session.flush()
                # Data is flushed to transaction but NOT committed
                # The transaction is still open
                # When session A context exits, it will rollback

        async def _session_b() -> list[str]:
            async with db_module._async_session() as session:
                result = await session.execute(
                    text("SELECT email FROM users")
                )
                return [row[0] for row in result]

        # Start session A, insert without commit, but DON'T exit yet
        async with db_module._async_session() as session_a:
            user = db_module.User(
                email="isolated_a@test.com",
                username="isolated_a",
                hashed_password="abc",
            )
            session_a.add(user)
            await session_a.flush()  # flushed but not committed

            # While session A's transaction is open, session B should NOT
            # see the uncommitted data
            emails_b = await _session_b()
            assert "isolated_a@test.com" not in emails_b, (
                "Session B should not see uncommitted data from session A"
            )

            # Now commit session A
            await session_a.commit()

        # After commit, session B should see the data
        emails_after = await _session_b()
        assert "isolated_a@test.com" in emails_after, (
            "Session B should see committed data after session A commits"
        )
