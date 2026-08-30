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
