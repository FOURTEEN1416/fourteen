"""W12 阶段1 角色模板面 — 行为契约测试（红测先行）。

任务包：docs/board/TASK_PACKAGE_W12_角色模板面.md（B 阶段1 · 纯后端）。
覆盖验收面：
1. 无 Bearer → GET 401（新端点自持契约，不扩大机器面）；
2. 列表含 visible_ids 白名单、不含 hidden_ids，有主卡永不入面，返回摘要不含 persona 正文；
3. 克隆 201 且新 id 归属调用者（A 看得到、B 看不到，is_active=False，persona 正文保真）；
4. 克隆前后模板文件 sha256 完全一致（模板零字节改动实证）；
5. 模板卡本身 A 仍无法 PUT/DELETE（require_character_access 404）→ 克隆是唯一获得途径；
6. enabled=false → GET 空表、clone 一律 404；未知/有主/隐藏模板统一 404（防枚举）；
7. 机器面回归：无 Bearer 的既有端点（/api/stats）行为不变。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from types import SimpleNamespace

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-w12-templates-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db

_ALICE_ID = 2
_BOB_ID = 3

_TPL_ALPHA = "tmplalp1"
_TPL_BETA = "tmplbet2"
_TPL_HIDDEN = "tmplhid3"
_TPL_OWNED = "tmplown4"

_ALPHA_MES_EXAMPLE = "USER: 今晚吃什么\nASSISTANT: 想吃你做的面。"


def _card(cid: str, name: str, *, user_id: str = "default") -> dict:
    return {
        "id": cid,
        "name": name,
        "description": f"{name}的简介",
        "schema_version": 1,
        "personality": {"warmth": 0.6},
        "speaking_style": {"formality": 0.5},
        "core_anchors": [name],
        "mes_example": _ALPHA_MES_EXAMPLE,
        "user_id": user_id,
        "is_active": False,
    }


def _build_app(tmp_path):
    db_path = str(tmp_path / "w12_templates.db")
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
            for uid, email, username, role in (
                (1, "admin@w12.test", "admin", "admin"),
                (_ALICE_ID, "alice@w12.test", "alice", "viewer"),
                (_BOB_ID, "bob@w12.test", "bob", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=username,
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=username,
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine


async def _login(client: AsyncClient, login: str) -> dict:
    resp = await client.post(
        "/api/auth/login", json={"login": login, "password": "Passw0rd!123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def w12_app(tmp_path, monkeypatch):
    """三账号 app + 角色目录/策展清单沙箱（不触碰真实 config/characters/）。"""
    from api.routers import character_routes, character_template_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    cfg_path = tmp_path / "character_templates.yaml"
    monkeypatch.setattr(character_template_routes, "_CONFIG_PATH", cfg_path)

    for cid, name, owner in (
        (_TPL_ALPHA, "阿尔法", "default"),
        (_TPL_BETA, "贝塔", "default"),
        (_TPL_HIDDEN, "隐匿", "default"),
        (_TPL_OWNED, "私卡", str(_ALICE_ID)),
    ):
        (chars_dir / f"{cid}.json").write_text(
            json.dumps(_card(cid, name, user_id=owner), ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_cfg(**kw) -> None:
        data = {"enabled": True, "visible_ids": [], "hidden_ids": [], "seed_on_register": []}
        data.update(kw)
        cfg_path.write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )

    _write_cfg()

    client, engine = _build_app(tmp_path)
    yield SimpleNamespace(
        client=client, write_cfg=_write_cfg, cfg_path=cfg_path, chars_dir=chars_dir
    )
    asyncio.run(engine.dispose())
    configure_auth(False, "")


def _bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _template_ids(payload: dict) -> list[str]:
    return [t["id"] for t in payload["templates"]]


# ═══════════════════════════════════════════════════════════
# 1. 鉴权：无 Bearer → 401（新端点自持契约）
# ═══════════════════════════════════════════════════════════


async def test_templates_require_bearer(w12_app):
    client = w12_app.client
    r = await client.get("/api/character-templates")
    assert r.status_code == 401, f"无主体 GET 应 401，实得 {r.status_code}"
    r2 = await client.post(f"/api/character-templates/{_TPL_ALPHA}/clone")
    assert r2.status_code == 401, f"无主体 clone 应 401，实得 {r2.status_code}"


# ═══════════════════════════════════════════════════════════
# 2. 列表：策展白/黑名单 + 有主卡不入面 + 摘要不含 persona 正文
# ═══════════════════════════════════════════════════════════


async def test_list_templates_curated_surface(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))

    ns.write_cfg(hidden_ids=[_TPL_HIDDEN])
    r = await ns.client.get("/api/character-templates", headers=alice)
    assert r.status_code == 200, r.text
    payload = r.json()
    ids = _template_ids(payload)
    # 开发期放行全部无主卡（visible_ids 为空）
    assert _TPL_ALPHA in ids and _TPL_BETA in ids
    # hidden_ids 排除
    assert _TPL_HIDDEN not in ids
    # 有主卡永不入面（哪怕写进 visible_ids）
    ns.write_cfg(visible_ids=[_TPL_ALPHA, _TPL_OWNED])
    r2 = await ns.client.get("/api/character-templates", headers=alice)
    ids2 = _template_ids(r2.json())
    assert ids2 == [_TPL_ALPHA], f"白名单应只含无主卡，实得 {ids2}"

    # 摘要形状：不含 persona 正文
    item = next(t for t in r2.json()["templates"] if t["id"] == _TPL_ALPHA)
    assert set(item.keys()) == {"id", "name", "description", "tags"}
    assert "mes_example" not in item and "personality" not in item
    assert payload["total"] == len(ids)


# ═══════════════════════════════════════════════════════════
# 3. 克隆：新 id 归属调用者，A 看得到 B 看不到，persona 正文保真
# ═══════════════════════════════════════════════════════════


async def test_clone_creates_private_isolated_copy(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))
    bob = _bearer(await _login(ns.client, "bob"))

    r = await ns.client.post(
        f"/api/character-templates/{_TPL_ALPHA}/clone", headers=alice
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] != _TPL_ALPHA, "克隆必须产出新 id，不得认领模板"
    assert body["name"] == "阿尔法"
    assert body["status"] == "created"

    # A 的角色列表含新卡且归属本人、未激活
    r2 = await ns.client.get("/api/characters", headers=alice)
    mine = [c for c in r2.json()["characters"] if c["id"] == body["id"]]
    assert len(mine) == 1, "克隆后 A 应恰好多出这张私有卡"
    assert mine[0]["user_id"] == str(_ALICE_ID)
    assert mine[0]["is_active"] is False

    # persona 正文保真（公开契约读回）
    r3 = await ns.client.get(f"/api/characters/{body['id']}", headers=alice)
    assert r3.status_code == 200, r3.text
    assert r3.json()["mes_example"] == _ALPHA_MES_EXAMPLE

    # B 看不到 A 的克隆
    r4 = await ns.client.get("/api/characters", headers=bob)
    assert all(c["id"] != body["id"] for c in r4.json()["characters"])


# ═══════════════════════════════════════════════════════════
# 4. 模板零字节改动：克隆前后 sha256 一致
# ═══════════════════════════════════════════════════════════


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_clone_preserves_template_bytes(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))
    tpl_path = ns.chars_dir / f"{_TPL_ALPHA}.json"
    before = _sha(tpl_path)

    r = await ns.client.post(
        f"/api/character-templates/{_TPL_ALPHA}/clone", headers=alice
    )
    assert r.status_code == 201, r.text
    assert _sha(tpl_path) == before, "克隆不得改动模板文件一个字节"


# ═══════════════════════════════════════════════════════════
# 5. 模板卡本身对普通用户不可写（克隆是唯一获得途径）
# ═══════════════════════════════════════════════════════════


async def test_template_card_not_mutable_by_user(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))

    r = await ns.client.put(
        f"/api/characters/{_TPL_ALPHA}", headers=alice, json={"name": "改名"}
    )
    assert r.status_code == 404, f"模板卡 PUT 应 404，实得 {r.status_code}"
    r2 = await ns.client.delete(f"/api/characters/{_TPL_ALPHA}", headers=alice)
    assert r2.status_code == 404, f"模板卡 DELETE 应 404，实得 {r2.status_code}"


# ═══════════════════════════════════════════════════════════
# 6. 开关与防枚举：enabled=false 空表 + 未知/有主/隐藏统一 404
# ═══════════════════════════════════════════════════════════


async def test_disabled_config_gates_surface(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))

    ns.write_cfg(enabled=False)
    r = await ns.client.get("/api/character-templates", headers=alice)
    assert r.status_code == 200
    assert r.json() == {"templates": [], "total": 0}
    r2 = await ns.client.post(
        f"/api/character-templates/{_TPL_ALPHA}/clone", headers=alice
    )
    assert r2.status_code == 404, f"总开关关闭时 clone 应 404，实得 {r2.status_code}"


async def test_unknown_owned_hidden_templates_unified_404(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))

    ns.write_cfg(hidden_ids=[_TPL_HIDDEN])
    for tid in ("nonexist9", _TPL_OWNED, _TPL_HIDDEN):
        r = await ns.client.post(f"/api/character-templates/{tid}/clone", headers=alice)
        assert r.status_code == 404, f"模板 {tid} 应统一 404，实得 {r.status_code}"


async def test_missing_config_fails_closed(w12_app):
    ns = w12_app
    alice = _bearer(await _login(ns.client, "alice"))
    ns.cfg_path.unlink()

    r = await ns.client.get("/api/character-templates", headers=alice)
    assert r.status_code == 200
    assert r.json() == {"templates": [], "total": 0}
    r2 = await ns.client.post(
        f"/api/character-templates/{_TPL_ALPHA}/clone", headers=alice
    )
    assert r2.status_code == 404


# ═══════════════════════════════════════════════════════════
# 7. 机器面回归：无 Bearer 既有端点行为不变
# ═══════════════════════════════════════════════════════════


async def test_machine_face_unchanged(w12_app):
    client = w12_app.client
    r = await client.get("/api/stats")
    assert r.status_code == 200, f"机器面 /api/stats 应维持 200，实得 {r.status_code}"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/character-templates"),
        ("POST", "/api/character-templates/nonexist9/clone"),
    ],
)
async def test_new_plane_never_joins_machine_face(w12_app, method, path):
    """机器 API Key（X-API-Key）也不能打开模板面——新端点只认 Bearer 主体。"""
    client = w12_app.client
    # configure_auth(False, "") 下机器面本就开放，这里直接验证无主体 401 即已覆盖；
    # 再验一次带伪造 X-API-Key 头同样 401（防实现误挂 verify_api_key_dep）。
    r = await client.request(method, path, headers={"X-API-Key": "any-key"})
    assert r.status_code == 401, f"{method} {path} 带 X-API-Key 无主体应 401，实得 {r.status_code}"
