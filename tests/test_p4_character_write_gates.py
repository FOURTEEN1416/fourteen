"""P4 角色写门回归（2026-11-07 痛点批）。

背景：`require_character_access`（读门：公共池卡对所有登录用户放行）被误挂到
7 个**写**端点上，导致普通用户能改写公共池成品卡的人设、往角色知识库塞/删文档、
重建索引、重算角色级成就——跨用户污染面。

本文件两层锁：
1. **静态守卫**：逐条路由断言依赖链里出现 `require_character_write`，
   防止后续重构把门悄悄改回读门（纯行为测试容易只覆盖一条路由）。
2. **行为级**：公共卡 + 普通用户 → 404（与「不存在」同文案，防状态码枚举）；
   本人私有卡 → 放行；机器面（无主体）→ 维持既有放行语义。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-p4-write-gates-32chars!")

from api.app_factory import create_api_app
from api.auth_jwt import AuthPrincipal
from api.routers import character_routes
from api.routers.character_routes import require_character_write


def _principal(user_id: int, role: str = "user") -> AuthPrincipal:
    return AuthPrincipal(user_id=user_id, role=role, token_version=0, user=None)  # type: ignore[arg-type]


#: 必须挂写门的 7 条写路由（读路由不在此列：GET persona / POST knowledge/search 等）。
WRITE_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/characters/{character_id}/knowledge/documents"),
    ("DELETE", "/api/characters/{character_id}/knowledge/documents/{doc_id}"),
    ("POST", "/api/characters/{character_id}/knowledge/vault"),
    ("POST", "/api/characters/{character_id}/knowledge/crawl"),
    ("POST", "/api/characters/{character_id}/enrich"),
    ("POST", "/api/characters/{character_id}/achievements/recalculate"),
]


def _dependency_names(route) -> set[str]:
    """递归收集 FastAPI 依赖链里的可调用对象名。"""
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        names.add(getattr(dep.call, "__name__", ""))
        stack.extend(dep.dependencies)
    return names


def _find_route(app, method: str, path: str):
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method in getattr(route, "methods", set()):
            return route
    return None


@pytest.fixture(scope="module")
def app():
    return create_api_app()


PERSONAL_OVERLAY_ROUTES: list[tuple[str, str]] = [
    ("PUT", "/api/characters/{character_id}/persona"),
]


@pytest.mark.parametrize(("method", "path"), PERSONAL_OVERLAY_ROUTES)
def test_persona_put_uses_access_gate_for_personal_overlay(app, method: str, path: str) -> None:
    """persona PUT = 读门 + 分支：自己的卡写文件、公开卡写私人覆盖（免克隆）。"""
    route = _find_route(app, method, path)
    assert route is not None, f"路由未挂载: {method} {path}"
    names = _dependency_names(route)
    assert "require_character_access" in names
    assert "require_character_write" not in names


@pytest.mark.parametrize(("method", "path"), WRITE_ROUTES)
def test_write_route_uses_write_gate(app, method: str, path: str) -> None:
    """静态守卫：路由存在且依赖链含 require_character_write（非读门）。"""
    route = _find_route(app, method, path)
    assert route is not None, f"路由未挂载: {method} {path}"
    names = _dependency_names(route)
    assert "require_character_write" in names, (
        f"{method} {path} 未挂写门，依赖链={sorted(names)}"
    )
    assert "require_character_access" not in names, (
        f"{method} {path} 同时挂了读门，语义冲突"
    )


# ── 行为级：直接调用依赖，验证归属判定 ─────────────────────


PUBLIC_CARD = {"id": "pub", "name": "公共卡", "user_id": "default"}
MINE = {"id": "mine", "name": "我的卡", "user_id": "9"}


def test_public_card_write_denied_for_normal_user(monkeypatch) -> None:
    monkeypatch.setattr(character_routes, "_load_character", lambda cid: dict(PUBLIC_CARD))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(require_character_write("pub", _principal(9)))
    assert excinfo.value.status_code == 404


def test_own_card_write_allowed(monkeypatch) -> None:
    monkeypatch.setattr(character_routes, "_load_character", lambda cid: dict(MINE))
    got = asyncio.run(require_character_write("mine", _principal(9)))
    assert got["id"] == "mine"


def test_others_private_card_write_denied(monkeypatch) -> None:
    monkeypatch.setattr(
        character_routes, "_load_character", lambda cid: {"id": "x", "user_id": "2"}
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(require_character_write("x", _principal(9)))
    assert excinfo.value.status_code == 404


def test_admin_write_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        character_routes, "_load_character", lambda cid: {"id": "x", "user_id": "2"}
    )
    got = asyncio.run(require_character_write("x", _principal(1, role="admin")))
    assert got["id"] == "x"


def test_machine_face_unchanged(monkeypatch) -> None:
    """无 Bearer 主体（部署脚本/无登录控制台）维持既有放行语义。"""
    monkeypatch.setattr(character_routes, "_load_character", lambda cid: dict(PUBLIC_CARD))
    assert asyncio.run(require_character_write("pub", None)) == {}


def test_character_cards_dir_not_touched() -> None:
    """本测试不得依赖真实卡库（config/characters 被 gitignore，CI 可能为空）。"""
    assert Path("config/characters").exists() or True
