"""W12 阶段2 · W16 窗：注册分发钩子 — 行为契约测试（红测先行）。

任务书验收面：
1. 注册成功 → 新用户名下**恰 1 张**私有卡（新 id、user_id=新用户）+ `user_active_characters`
   激活绑定存在 + 响应体新增 `initial_character: {"id","name"}`；
2. 种子回落路径：`seed_on_register` 为空 → 取 `visible_ids` 声明序首张；种子卡在盘上缺失 →
   继续回落到策展面；
3. additive-only：既有字段（access_token / refresh_token / user / needs_consent /
   agreement_version）与状态码零改动（W11 e2e 依赖注册后弹协议门）；
4. 失败不阻断注册：克隆写盘失败 / 绑定失败 → 注册仍 200、`initial_character` 为 null、
   日志可见（不谎报）、且**不留孤儿卡**；
5. 总开关关闭（enabled=false）→ 不分发、不写盘（与阶段1 的 fail-closed 同口径）。

沙箱纪律：角色目录与策展清单都重定向到 tmp_path，测试**不写**真实 `config/characters/`
（该目录 gitignored、卡数决定 `test_persona_injection` 收集数，写脏即污染基线）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from types import SimpleNamespace

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-w16-register-seed-32ch-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, UserActiveCharacter, get_db

_SEED = "seedc1"      # 种子卡（seed_on_register 指向它）
_VIS1 = "visibl01"    # 策展面第 1 张
_VIS2 = "visibl02"    # 策展面第 2 张
_HID = "hidden03"     # hidden_ids 排除
_OWNED = "owned04"    # 有主卡，永不入面

_PASSWORD = "Passw0rd!123"


def _card(cid: str, name: str, *, user_id: str = "default") -> dict:
    return {
        "id": cid,
        "name": name,
        "description": f"{name}的简介",
        "schema_version": 1,
        "personality": {"warmth": 0.6},
        "speaking_style": {"formality": 0.5},
        "core_anchors": [name],
        "mes_example": f"USER: 你好\nASSISTANT: 我是{name}。",
        "user_id": user_id,
        "is_active": False,
    }


def _build_app(tmp_path):
    db_path = str(tmp_path / "w16_register.db")
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
            session.add(
                User(
                    id=1,
                    email="admin@w16.test",
                    username="admin",
                    hashed_password=hash_password(_PASSWORD),
                    display_name="admin",
                    role="admin",
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
    return client, engine, session_factory


@pytest.fixture
def w16_app(tmp_path, monkeypatch):
    """注册面 app + 角色目录/策展清单沙箱（不触碰真实 config/characters/）。"""
    from api.routers import character_routes, character_template_routes

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)

    cfg_path = tmp_path / "character_templates.yaml"
    monkeypatch.setattr(character_template_routes, "_CONFIG_PATH", cfg_path)

    for cid, name, owner in (
        (_SEED, "种子角色", "default"),
        (_VIS1, "策展壹", "default"),
        (_VIS2, "策展贰", "default"),
        (_HID, "隐匿", "default"),
        (_OWNED, "私卡", "99"),
    ):
        (chars_dir / f"{cid}.json").write_text(
            json.dumps(_card(cid, name, user_id=owner), ensure_ascii=False),
            encoding="utf-8",
        )

    baseline_cards = set(p.name for p in chars_dir.glob("*.json"))

    def _write_cfg(**kw) -> None:
        data = {
            "enabled": True,
            "visible_ids": [_SEED, _VIS1, _VIS2],
            "hidden_ids": [_HID],
            "seed_on_register": [_SEED],
        }
        data.update(kw)
        cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def _owned_cards(user_id: int) -> dict[str, dict]:
        """盘上归属该注册用户的卡：{文件名: 卡内容}。"""
        out = {}
        for path in chars_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("user_id")) == str(user_id):
                out[path.name] = data
        return out

    _write_cfg()

    client, engine, session_factory = _build_app(tmp_path)
    yield SimpleNamespace(
        client=client,
        write_cfg=_write_cfg,
        cfg_path=cfg_path,
        chars_dir=chars_dir,
        baseline_cards=baseline_cards,
        owned_cards=_owned_cards,
        session_factory=session_factory,
    )
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _register(client: AsyncClient, username: str) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": f"{username}@w16.test",
            "username": username,
            "password": _PASSWORD,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _active_row(session_factory, user_id: int) -> UserActiveCharacter | None:
    async def _read():
        async with session_factory() as session:
            result = await session.execute(
                select(UserActiveCharacter).where(
                    UserActiveCharacter.user_id == user_id
                )
            )
            return result.scalar_one_or_none()

    return await _read()


# ═══════════════════════════════════════════════════════════
# 1. 主路径：注册即分发种子卡 + 激活绑定 + 响应字段
# ═══════════════════════════════════════════════════════════


async def test_register_provisions_seed_card_and_binding(w16_app):
    ns = w16_app
    body = await _register(ns.client, "alice")

    initial = body.get("initial_character")
    assert isinstance(initial, dict), f"注册应分发初始角色，实得 {initial!r}"
    assert initial["name"] == "种子角色"
    new_id = initial["id"]
    assert new_id and new_id != _SEED, "分发必须用克隆新 id，不得认领模板本体"

    user_id = int(body["user"]["id"])

    # 新用户名下**恰 1 张**卡：新 id、归属本人、卡文件未全局激活
    mine = ns.owned_cards(user_id)
    assert len(mine) == 1, f"新用户名下应恰 1 张卡，实得 {len(mine)} 张：{list(mine)}"
    card = next(iter(mine.values()))
    assert card["id"] == new_id
    assert card["is_active"] is False, "不得改写卡文件的全局 is_active（D2 个人选择走表）"
    assert card["mes_example"] == "USER: 你好\nASSISTANT: 我是种子角色。", "克隆必须 persona 正文保真"

    # 激活绑定落表
    row = await _active_row(ns.session_factory, user_id)
    assert row is not None and row.character_id == new_id, "user_active_characters 必须有该用户的激活行"

    # 模板本体零改动（文件仍在、仍无主）
    tpl = json.loads((ns.chars_dir / f"{_SEED}.json").read_text(encoding="utf-8"))
    assert tpl["user_id"] == "default"

    # 个人激活在角色列表接口可见（读侧真源就是这张表）
    r = await ns.client.get(
        "/api/characters",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert r.status_code == 200, r.text
    listed = [c for c in r.json()["characters"] if c["id"] == new_id]
    assert len(listed) == 1 and listed[0]["is_active"] is True


async def test_each_registration_gets_its_own_copy(w16_app):
    """两个新用户各自分发独立副本，互不认领（阶段1「克隆 ≠ 认领」语义）。"""
    ns = w16_app
    a = await _register(ns.client, "alice")
    b = await _register(ns.client, "bob")

    aid, bid = a["initial_character"]["id"], b["initial_character"]["id"]
    assert aid != bid
    assert len(ns.owned_cards(int(a["user"]["id"]))) == 1
    assert len(ns.owned_cards(int(b["user"]["id"]))) == 1

    row_a = await _active_row(ns.session_factory, int(a["user"]["id"]))
    row_b = await _active_row(ns.session_factory, int(b["user"]["id"]))
    assert (row_a.character_id, row_b.character_id) == (aid, bid)


# ═══════════════════════════════════════════════════════════
# 2. 回落路径
# ═══════════════════════════════════════════════════════════


async def test_empty_seed_falls_back_to_first_visible(w16_app):
    """seed 为空 → 取 visible_ids **声明序**首张（不是目录字典序）。"""
    ns = w16_app
    ns.write_cfg(seed_on_register=[], visible_ids=[_VIS2, _VIS1])

    body = await _register(ns.client, "carol")
    assert body["initial_character"]["name"] == "策展贰"


async def test_missing_seed_card_falls_back_to_visible(w16_app):
    """种子卡在盘上缺失 → 回落到策展面首张可用卡。"""
    ns = w16_app
    ns.write_cfg(seed_on_register=["ghost99"], visible_ids=[_VIS1, _VIS2])

    body = await _register(ns.client, "dave")
    assert body["initial_character"]["name"] == "策展壹"


async def test_owned_or_hidden_candidate_is_skipped(w16_app):
    """种子指向有主卡/隐藏卡（不可用）→ 回落；有主卡永不被分发。"""
    ns = w16_app
    ns.write_cfg(
        seed_on_register=[_OWNED, _HID],
        visible_ids=[_OWNED, _HID, _VIS1],
    )

    body = await _register(ns.client, "erin")
    assert body["initial_character"]["name"] == "策展壹"


async def test_no_usable_template_returns_null(w16_app):
    """策展面全不可用 → 注册照旧成功、initial_character 为 null、盘上不新增卡。"""
    ns = w16_app
    ns.write_cfg(seed_on_register=["ghost99"], visible_ids=["ghost88"])

    body = await _register(ns.client, "frank")
    assert body["initial_character"] is None, "无可用模板必须诚实返回 null"
    assert set(p.name for p in ns.chars_dir.glob("*.json")) == ns.baseline_cards
    user_id = int(body["user"]["id"])
    assert ns.owned_cards(user_id) == {}
    assert await _active_row(ns.session_factory, user_id) is None


# ═══════════════════════════════════════════════════════════
# 3. additive-only：既有注册契约零改动
# ═══════════════════════════════════════════════════════════


async def test_register_contract_is_additive_only(w16_app):
    ns = w16_app
    body = await _register(ns.client, "grace")

    for key in ("access_token", "refresh_token", "user", "token_type"):
        assert body.get(key), f"既有字段 {key} 不得缺失/清空"
    assert body["needs_consent"] is True, "新用户必然未同意——W11 e2e 依赖注册后弹协议门"
    assert "agreement_version" in body
    assert set(body) >= {
        "access_token",
        "refresh_token",
        "token_type",
        "user",
        "needs_consent",
        "agreement_version",
        "initial_character",
    }


# ═══════════════════════════════════════════════════════════
# 4. 开关与失败面
# ═══════════════════════════════════════════════════════════


async def test_disabled_plane_does_not_provision(w16_app):
    """enabled=false 时分发整体关闭（与阶段1 fail-closed 同口径），不写盘。"""
    ns = w16_app
    ns.write_cfg(enabled=False)

    body = await _register(ns.client, "henry")
    assert body["initial_character"] is None
    assert set(p.name for p in ns.chars_dir.glob("*.json")) == ns.baseline_cards


async def test_clone_failure_keeps_registration_alive(
    w16_app, monkeypatch, caplog
):
    """卡文件写入失败：注册仍 200、initial_character=null、告警可见（不谎报成功）。"""
    from api.routers import character_template_routes as tpl

    ns = w16_app
    calls = []

    def _failing_save(character_id, data):
        calls.append(character_id)
        return False

    monkeypatch.setattr(tpl, "_save_character", _failing_save)
    with caplog.at_level(logging.WARNING, logger="api.character_template_routes"):
        body = await _register(ns.client, "iris")

    assert body["initial_character"] is None
    assert calls, "分发必须真的尝试过克隆（而非静默跳过）"
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "分发失败必须留告警，不得静默吞掉"
    )
    user_id = int(body["user"]["id"])
    assert await _active_row(ns.session_factory, user_id) is None, "克隆未成功不得留激活绑定"


async def test_binding_failure_leaves_no_orphan_card(w16_app, monkeypatch):
    """绑定失败：注册仍 200、initial_character=null，且补偿掉已克隆的卡（不留孤儿）。"""
    from api.routers import character_template_routes as tpl

    ns = w16_app

    async def _failing_bind(db, user_id, character_id):
        raise RuntimeError("模拟绑定失败")

    monkeypatch.setattr(tpl, "bind_active_character", _failing_bind)
    body = await _register(ns.client, "jack")

    assert body["initial_character"] is None
    user_id = int(body["user"]["id"])
    assert ns.owned_cards(user_id) == {}, "绑定失败后不得在盘上留下无绑定的孤儿卡"
    assert await _active_row(ns.session_factory, user_id) is None
