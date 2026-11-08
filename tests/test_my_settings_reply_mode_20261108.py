"""回复模式迁入「我 × 这个角色」契约（2026-11-08 D-C）。

锁定用户裁决（逐字）：「my-settings 快照新增字段 reply_mode，字段组键 reply，
取值仅限 immersive 或 novel，缺省/未设置=跟随全局默认；GET/PUT/DELETE
/api/characters/{id}/my-settings 对该字段完整往返（含恢复默认 DELETE 后回落
全局默认）；运行时解析链改为：my-settings 快照 reply_mode → 全局默认配置 →
immersive 兜底；user_reply_mode 表读层整体移除」。

覆盖：
1. 字段表：`reply` 组注册、默认空串（=跟随全局）、非法值写侧拒绝；
2. 解析链优先级：快照 > 全局 > 沉浸式；缺角色上下文 → 全局；
3. API 往返：PUT 写入 / GET 读回 / DELETE 复位后回落全局；
4. 旧入口全部 404（`/api/user/reply-mode`、`/api/user/humanize`、
   `/api/user/proactive*` 的写面），而 `/api/user/proactive/history` 仍 200。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-my-settings-reply-32ch!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, WechatBinding, get_db

_ALICE = 2
_BOB = 3


# ═══════════════════════════════════════════════════════════
# 1. 字段表 / 写侧校验（不起 app）
# ═══════════════════════════════════════════════════════════


def test_reply_group_is_registered_first_and_has_single_field():
    from utils import character_settings as cs

    assert "reply" in cs.GROUPS
    assert cs.GROUPS[0] == "reply", "回复模式是角色设置的第一组（前端面板排在最前）"
    assert cs.FIELDS_BY_GROUP["reply"] == ("reply_mode",)

    field = next(f for f in cs.SETTING_FIELDS if f.name == "reply_mode")
    assert field.group == "reply"
    # 空串是「未设置 = 跟随全局」的存储形态；可写取值是两种模式 + 空串
    assert field.default == ""
    assert field.choices == ("", "immersive", "novel")


def test_snapshot_defaults_to_follow_global():
    from utils import character_settings as cs

    values = cs.read("9:reply_default@im.wechat", "ch_reply_default")
    assert values["reply_mode"] == "", "缺省必须是「跟随全局」，不是钉死某个模式"


def test_write_roundtrip_and_invalid_rejected():
    from utils import character_settings as cs

    user, ch = "9:reply_rt@im.wechat", "ch_reply_rt"
    assert cs.write(user, ch, {"reply_mode": "novel"})["reply_mode"] == "novel"
    assert cs.read(user, ch)["reply_mode"] == "novel"
    # 显式写回空串 = 恢复「跟随全局」
    assert cs.write(user, ch, {"reply_mode": ""})["reply_mode"] == ""

    with pytest.raises(ValueError):
        cs.write(user, ch, {"reply_mode": "poetry"})


def test_reset_drops_reply_mode_back_to_follow_global():
    from utils import character_settings as cs

    user, ch = "9:reply_reset@im.wechat", "ch_reply_reset"
    cs.write(user, ch, {"reply_mode": "novel"})
    assert cs.reset(user, ch) is True
    assert cs.read(user, ch)["reply_mode"] == ""


# ═══════════════════════════════════════════════════════════
# 2. 运行时解析链：快照 → 全局 → immersive
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def global_mode_file(tmp_path, monkeypatch):
    """把全局默认真源指到临时文件（不碰宿主 data/scheduler_config.json）。"""
    from utils import reply_mode

    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(reply_mode, "_CONFIG_PATH", path)
    return path


def _write_global_mode(path, mode):
    path.write_text(json.dumps({"reply_mode": mode}), encoding="utf-8")


def test_snapshot_beats_global(global_mode_file):
    from utils import character_settings as cs
    from utils.reply_mode import read_reply_mode

    _write_global_mode(global_mode_file, "immersive")
    cs.write("9:chain_a@im.wechat", "ch_chain", {"reply_mode": "novel"})
    assert read_reply_mode("9:chain_a@im.wechat", "ch_chain") == "novel"


def test_unset_snapshot_follows_global(global_mode_file):
    from utils.reply_mode import read_reply_mode

    _write_global_mode(global_mode_file, "novel")
    # 快照未物化/未设置 → 跟随全局
    assert read_reply_mode("9:chain_b@im.wechat", "ch_chain") == "novel"


def test_global_missing_falls_back_to_immersive(global_mode_file):
    from utils.reply_mode import DEFAULT_REPLY_MODE, read_reply_mode

    assert not global_mode_file.exists()
    assert read_reply_mode("9:chain_c@im.wechat", "ch_chain") == DEFAULT_REPLY_MODE


def test_invalid_global_value_falls_back_to_immersive(global_mode_file):
    from utils.reply_mode import read_reply_mode

    _write_global_mode(global_mode_file, "poetry")
    assert read_reply_mode("9:chain_d@im.wechat", "ch_chain") == "immersive"


def test_missing_character_context_reads_global_only(global_mode_file):
    from utils import character_settings as cs
    from utils.reply_mode import read_reply_mode

    _write_global_mode(global_mode_file, "immersive")
    cs.write("9:chain_e@im.wechat", "ch_chain", {"reply_mode": "novel"})
    # 只给 user_key（后台/提醒线旧调用形态）→ 不猜角色，读全局
    assert read_reply_mode("9:chain_e@im.wechat") == "immersive"


def test_instruction_follows_snapshot(global_mode_file):
    from utils import character_settings as cs
    from utils.reply_mode import _INSTRUCTION_NOVEL, reply_mode_instruction

    _write_global_mode(global_mode_file, "immersive")
    cs.write("9:chain_f@im.wechat", "ch_chain", {"reply_mode": "novel"})
    assert reply_mode_instruction(user_key="9:chain_f@im.wechat", character_id="ch_chain") == _INSTRUCTION_NOVEL


# ═══════════════════════════════════════════════════════════
# 3. API 往返 + 旧入口 404
# ═══════════════════════════════════════════════════════════


def _public_card(card_id: str, name: str) -> dict:
    return {
        "id": card_id,
        "name": name,
        "user_id": "default",
        "personality": {},
        "speaking_style": {},
        "core_anchors": [],
        "is_active": False,
    }


def _build_app(tmp_path):
    db_path = str(tmp_path / "reply_settings.db")
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
                (1, "admin@r.test", "admin", "admin"),
                (_ALICE, "alice@r.test", "alice", "viewer"),
                (_BOB, "bob@r.test", "bob", "viewer"),
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
            session.add(WechatBinding(user_id=_ALICE, wxid="alice_wx", character_card_id="default"))
            session.add(WechatBinding(user_id=_BOB, wxid="bob_wx", character_card_id="default"))
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, engine


@pytest.fixture
def r_app(tmp_path, monkeypatch):
    from api.routers import character_routes
    from utils import reply_mode

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    (chars_dir / "pub01.json").write_text(
        json.dumps(_public_card("pub01", "公共卡")), encoding="utf-8"
    )
    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr(reply_mode, "_CONFIG_PATH", tmp_path / "scheduler_config.json")

    client, engine = _build_app(tmp_path)
    yield client, engine
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def _login(client: AsyncClient, name: str) -> str:
    resp = await client.post("/api/auth/login", json={"login": name, "password": "Passw0rd!123"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_api_put_get_delete_roundtrip(r_app, tmp_path):
    """PUT 写入 → GET 读回 → DELETE 复位 → GET 回落「跟随全局」。"""
    client, _ = r_app
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"reply_mode": "novel"}), encoding="utf-8"
    )

    async def _run():
        token = await _login(client, "alice")
        headers = _auth(token)

        put = await client.put(
            "/api/characters/pub01/my-settings",
            json={"config": {"reply_mode": "immersive"}},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        assert put.json()["config"]["reply_mode"] == "immersive"

        got = await client.get("/api/characters/pub01/my-settings", headers=headers)
        assert got.json()["config"]["reply_mode"] == "immersive"

        dele = await client.delete("/api/characters/pub01/my-settings", headers=headers)
        assert dele.status_code == 200 and dele.json()["reset"] is True

        after = await client.get("/api/characters/pub01/my-settings", headers=headers)
        assert after.json()["config"]["reply_mode"] == "", "复位后快照回到「跟随全局」"
        # 解析链也要真的回落：快照空串 → 全局默认（本用例种的 novel）
        from utils.reply_mode import read_reply_mode

        assert read_reply_mode("2:alice_wx", "pub01") == "novel"
        await client.aclose()

    asyncio.run(_run())


def test_api_rejects_invalid_reply_mode(r_app):
    client, _ = r_app

    async def _run():
        token = await _login(client, "alice")
        resp = await client.put(
            "/api/characters/pub01/my-settings",
            json={"config": {"reply_mode": "poetry"}},
            headers=_auth(token),
        )
        assert resp.status_code == 400, resp.text
        await client.aclose()

    asyncio.run(_run())


def test_retired_user_endpoints_are_404(r_app):
    """D-A 退役的四个入口一律 404（不是 401/422/500 —— 路由本身已不存在）。"""
    client, _ = r_app

    async def _run():
        token = await _login(client, "alice")
        headers = _auth(token)
        calls = [
            ("get", "/api/user/reply-mode", None),
            ("post", "/api/user/reply-mode", {"reply_mode": "novel"}),
            ("get", "/api/user/humanize", None),
            ("post", "/api/user/humanize", {"per_char_ms": 60}),
            ("put", "/api/user/humanize", {"per_char_ms": 60}),
            ("delete", "/api/user/humanize", None),
            ("get", "/api/user/proactive-config", None),
            ("post", "/api/user/proactive-config", {"paused": True}),
            ("post", "/api/user/proactive/pause", {"paused": True}),
            ("delete", "/api/user/proactive-config", None),
        ]
        for method, path, payload in calls:
            kwargs = {"headers": headers}
            if payload is not None:
                kwargs["json"] = payload
            resp = await getattr(client, method)(path, **kwargs)
            assert resp.status_code == 404, (
                f"{method.upper()} {path} 仍存在（{resp.status_code}）：{resp.text[:160]}"
            )
        await client.aclose()

    asyncio.run(_run())


def test_user_proactive_history_read_face_kept(r_app):
    """状态页「最近主动消息」卡在用：历史读面必须保留（未绑定/空库也得是 200）。"""
    client, _ = r_app

    async def _run():
        token = await _login(client, "alice")
        resp = await client.get("/api/user/proactive/history", headers=_auth(token))
        assert resp.status_code == 200, resp.text
        await client.aclose()

    asyncio.run(_run())
