"""BYOK 强制策略三态测试（W1，2026-08-28）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.byok import ensure_user_has_key


def _cfg(required: bool):
    return MagicMock(byok_required=required)


def _user(role: str = "user", key: str | None = None):
    u = MagicMock()
    u.role = role
    u.llm_config = {"api_key": key} if key is not None else None
    return u


def test_byok_off_allows_everyone():
    ensure_user_has_key(_user("user", None), _cfg(False))  # 不抛


def test_byok_on_blocks_user_without_key():
    with pytest.raises(Exception) as ei:
        ensure_user_has_key(_user("user", None), _cfg(True))
    assert "BYOK_REQUIRED" in str(ei.value.headers.get("X-Error-Code", "")) or ei.value.status_code == 403


def test_byok_on_allows_user_with_key():
    ensure_user_has_key(_user("user", "sk-own"), _cfg(True))  # 不抛


def test_byok_on_allows_admin():
    ensure_user_has_key(_user("admin", None), _cfg(True))  # 不抛


@pytest.mark.asyncio
async def test_background_owner_configuration_failure_never_uses_global(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from api import byok, database

    class BrokenDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *a):
            raise ConnectionError("unavailable")

    monkeypatch.setattr(database, "_async_session", BrokenDB)
    platform = AsyncMock()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await byok.session_llm("7:peer", SimpleNamespace(components={"llm": platform}))
    assert exc.value.status_code == 503
    platform.assert_not_called()


def test_user_auto_chain_never_inherits_platform_credentials(monkeypatch):
    from types import SimpleNamespace

    from llm_provider import get_user_llm
    from llm_provider import multi_provider_gateway as mg

    captured = []
    monkeypatch.setenv("ZHIPU_API_KEY", "platform-test-only")
    monkeypatch.setattr(mg, "_load_providers_config", lambda: (_ for _ in ()).throw(AssertionError("平台配置不得读取")))
    monkeypatch.setattr(mg, "OpenAICompatibleProvider", lambda **kw: captured.append(kw) or SimpleNamespace())
    get_user_llm(900001, {"provider": "auto", "providers": {"zhipu": {
        "api_key": "user-test-only", "api_base": "https://example.invalid", "model": "test-model",
    }}})
    assert captured[0]["api_key"] == "user-test-only"
    assert captured[0]["model"] == "test-model"


@pytest.mark.parametrize("config", [{"provider": "auto"}, {"provider": "zhipu"}, {"api_key": "unit-test"}])
def test_bad_user_configuration_cannot_fall_back_to_platform(config):
    from llm_provider import select_request_llm

    with pytest.raises(ValueError):
        select_request_llm(object(), 900002, config)
