"""T-18: LLM 设置页 /api/config 权限测试

验证：
- 任何已登录用户（含非管理员）可读取配置；
- 仅管理员可保存配置；
- 未登录用户无法访问。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import app_factory, auth
from api.auth_jwt import create_access_token, get_current_user_id, token_claims
from api.database import User, get_db


class FakeResult:
    def __init__(self, user: User | None):
        self._user = user

    def scalar_one_or_none(self) -> User | None:
        return self._user


class FakeSession:
    def __init__(self, user: User | None):
        self._user = user

    async def execute(self, *args: Any, **kwargs: Any):
        return FakeResult(self._user)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any):
        pass

    async def close(self):
        pass


class FakeConfigManager:
    """路由契约桩：实现 save_config 实际消费的 ConfigManager 表面。

    W6 起 save_config 走 save_with_receipt（版本化回执 + live 应用），
    响应组装读 get_config_dict，GET 附带 status/capability_declaration。
    """

    def __init__(self, data: dict | None = None):
        self._data = data or {}
        self.config = type("C", (), {"model_dump": lambda self: self._data})()

    def save(self, updates: dict):
        self._data.update(updates)
        return type("C", (), {"model_dump": lambda self: self._data})()

    def save_with_receipt(self, updates: dict, *, live_components=None):
        self.save(updates)
        return {
            "config": self.config,
            "persisted_version": 1,
            "effective_version": 1,
            "in_sync": True,
            "applied_live": [],
            "restart_required": [],
            "unsupported": [],
            "field_status": {},
        }

    def get_config_dict(self) -> dict:
        return self._data

    def status(self) -> dict:
        return {"persisted_version": 1, "effective_version": 1, "stale": False}

    def capability_declaration(self) -> dict:
        return {}


@pytest.fixture
def app(monkeypatch):
    app = app_factory.create_api_app()
    app.dependency_overrides[auth.verify_api_key_dep] = lambda: True
    # 注入假配置管理器，避免真实文件/DB依赖
    from api.deps import deps
    monkeypatch.setattr(deps, "config", FakeConfigManager({"llm": {"provider": "zhipu"}}))
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _override_user(app, user: User | None):
    """覆盖 get_db，让 require_role/get_current_user 查到指定用户。"""

    async def _fake_get_db() -> AsyncIterator[FakeSession]:
        yield FakeSession(user)

    app.dependency_overrides[get_db] = _fake_get_db


def test_get_config_allowed_for_non_admin(client, app):
    """非管理员可访问 /api/config（P0 读面收口后为收缩面，不再泄漏全局配置块）。"""
    user = User(
        id=42,
        username="user",
        email="user@test.local",
        role="user",
        hashed_password="",
        is_active=True,
        token_version=0,
    )
    app.dependency_overrides[get_current_user_id] = lambda: 42
    _override_user(app, user)

    # P0 修复批 F1 后 verify_api_key_dep 走 W1 主体校验真验签——
    # 旧「Bearer fake-token」形态即漏洞本体（任意可验签 JWT 放行）的测试固化，改真签发。
    token = create_access_token(token_claims(user))
    response = client.get("/api/config", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    # 收缩契约：viewer 面只有 scope 标记，不含全局配置块（admin/机器面全量另见
    # tests/test_sec_p0_read_gates.py 的收缩断言族）
    assert response.json().get("scope") == "user"


def test_get_config_rejects_anonymous(client):
    """未登录用户读取配置返回 401。"""
    response = client.get("/api/config")
    assert response.status_code == 401


def test_post_config_rejects_non_admin(client, app):
    """非管理员保存 LLM 配置返回 403。"""
    app.dependency_overrides[get_current_user_id] = lambda: 42
    _override_user(app, User(id=42, username="user", role="user", hashed_password=""))

    response = client.post(
        "/api/config",
        json={"config": {"llm": {"provider": "zhipu"}}},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 403, response.text


def test_post_config_allows_admin(client, app):
    """管理员可以保存 LLM 配置。"""
    app.dependency_overrides[get_current_user_id] = lambda: 1
    _override_user(app, User(id=1, username="admin", role="admin", hashed_password=""))

    response = client.post(
        "/api/config",
        json={"config": {"llm": {"provider": "zhipu"}}},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["llm"]["provider"] == "zhipu"
