"""SEC-P0「读面加门」域 — 行为契约测试（红测先行）。

缺陷链（已确认）：``api/auth.py::verify_api_key_dep`` 把「任意注册用户的 JWT」
当机器凭证放行（仅 ``verify_token`` 验签，不查库、不看角色）→ 只挂该依赖的
读面端点对 ``role=viewer`` 的普通注册用户全量裸奔：

- F1 ``training_routes`` 5 个 GET 控制面端点（最高危 ``/api/proactive/history``
  全表读所有用户消息正文 + wxid）→ 必须 ``require_role("admin")``；
- F2 ``emotion_routes`` GET/PUT ``/api/emotion/params``（PUT 直写全局引擎
  ``_config``，GET 读全局配置）→ 必须 admin；
- F3 ``misc_routes`` ``/api/stats`` / ``/api/stats/dashboard`` / ``/api/config``
  —— 前端普通用户页在消费（StatusCenter 直连 ``/stats`` 用 ``working_count``、
  useDashboard 用 ``current_emotion/affinity/recent_memories``），不能一刀切
  403（``test_w1_identity_authorization`` 也把 viewer 的 200 钉为契约）→
  按**主体口径收缩**：viewer 只拿与其相关/无害字段，admin（及机器 key）
  拿全量；
- F4 ``/api/user/llm-config`` 掩码只盖顶层 ``api_key``，
  ``providers.<name>.api_key`` 明文回显 → 递归掩码；
- F5 ``/api/channels`` ``get_active_sessions()`` 无 user_id 过滤 → 泄漏他人
  会话 id → 本人过滤、admin 不过滤。

红测先行：本文件在修复前必须成片变红；修复后全绿。
数据全部合成（tmp 库 + conftest 沙箱运行时状态文件）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-sec-p0-read-gates-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import hash_password
from api.database import Base, User, get_db
from api.deps import deps
from api.session_manager import SessionManager

_ADMIN_ID = 1
_ALICE_ID = 2
_BOB_ID = 3


# ═══════════════════════════════════════════════════════════
# App 构造（真实登录流，不 override 身份依赖——与 W1 同型）
# ═══════════════════════════════════════════════════════════


def _build_app(tmp_path, *, session_manager: SessionManager | None = None):
    db_path = str(tmp_path / "sec_p0_read_gates.db")
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
                (_ADMIN_ID, "admin@sec.test", "admin", "admin"),
                (_ALICE_ID, "alice@sec.test", "alice", "viewer"),
                (_BOB_ID, "bob@sec.test", "bob", "viewer"),
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

    app = create_api_app(session_manager=session_manager)
    app.dependency_overrides[get_db] = _get_db_override
    # API Key 认证关闭（本域收口的是「已认证普通用户」的越权读面，
    # 与 API_KEY_ENABLED 与否正交）
    configure_auth(False, "")

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine, session_factory


def _token(uid: int) -> str:
    """直接签发 access token（token_claims 同构：sub + tv）。

    不走 POST /api/auth/login：登录链路的 IP 滑窗（api/auth._bf_ip_hits）是
    **进程级全局**且按次计数，本文件 20+ 用例同进程连发登录会撞 429 ——
    本域被测面（读门/收缩/掩码/过滤）不依赖登录端点，签发与库内
    token_version（默认 0）对齐即可。
    """
    from api.auth_jwt import create_access_token

    return create_access_token({"sub": str(uid), "tv": 0})


def _bearer(uid: int) -> dict:
    return {"Authorization": f"Bearer {_token(uid)}"}


@pytest.fixture
def sec_app(tmp_path):
    """标准三账号 app（无编排器/无全局配置——按需在用例内注入假件并复原）。"""
    app, client, engine, session_factory = _build_app(tmp_path)
    yield client, engine, session_factory
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


class _FakeEngines:
    """最小假件：证明「viewer 今天真能读到/写到全局引擎状态」的缺陷本体。

    接口面对齐两处读法：/api/stats 读 ``orch._emotion/_memory``，
    /api/stats/dashboard 优先读 ``orch.components["emotion"/"memory"]`` ——
    同一对象双挂载，两处读法都命中假件。
    """

    def __init__(self):
        self.emotion_engine = SimpleNamespace(
            _config={"baseline_mood": "平和", "volatility": 0.5, "resilience": 0.5},
            _default_config={"energy_drain_per_message": 0.02},
            health_check=lambda: {"status": "ok", "engine": "fake"},
            state=SimpleNamespace(
                primary_emotion=SimpleNamespace(value="平和"), affinity=42, energy=88,
            ),
        )
        self.memory = SimpleNamespace(
            working=SimpleNamespace(count=lambda: 7),
            structured_memory=SimpleNamespace(
                count_chats_today=lambda: 3, count_facts=lambda: 9,
            ),
        )
        self.orch = SimpleNamespace(
            components={"emotion": self.emotion_engine, "memory": self.memory},
            _emotion=self.emotion_engine,
            _memory=self.memory,
        )
        self.config = SimpleNamespace(
            get_config_dict=lambda: {
                "llm": {"provider": "zhipu", "model": "glm-4-flash", "api_key": "sk-global"},
            },
            status=lambda: {"version": 1},
            capability_declaration=lambda: {},
        )


@pytest.fixture
def fake_engines():
    """向全局 deps 注入假编排器/假配置，用例结束复原（deps 是进程级单例）。"""
    fakes = _FakeEngines()
    saved = (deps.orch, deps.config)
    deps.orch = fakes.orch
    deps.config = fakes.config
    yield fakes
    deps.orch, deps.config = saved


# ═══════════════════════════════════════════════════════════
# F1：training_routes 5 个 GET 控制面端点 → admin 门
# ═══════════════════════════════════════════════════════════

_F1_ADMIN_GETS = [
    "/api/proactive/history",
    "/api/proactive/state",
    "/api/proactive/config",
    "/api/knowledge/collect-config",
    "/api/training/progress",
]


@pytest.mark.parametrize("path", _F1_ADMIN_GETS)
async def test_f1_viewer_jwt_rejected_on_control_plane_gets(sec_app, path):
    """viewer 的合法 JWT 打控制面 GET → 403（修复前 200：仅验签即放行）。"""
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.get(path, headers=alice)
    assert r.status_code == 403, (
        f"viewer GET {path} 必须 403，实得 {r.status_code}：{r.text[:200]}"
    )


@pytest.mark.parametrize("path", _F1_ADMIN_GETS)
async def test_f1_admin_passes_role_gate(sec_app, path):
    """admin JWT 必须通过角色门（可达性因引擎初始化而异，但绝不 401/403）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get(path, headers=admin)
    assert r.status_code not in (401, 403), (
        f"admin GET {path} 不应被门挡下，实得 {r.status_code}：{r.text[:200]}"
    )


async def test_f1_proactive_history_admin_sees_own_plane(sec_app):
    """最高危面修复后 admin 仍可用：全表出站事实真源（沙箱控制面，空表零态）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/proactive/history", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["history"] == [] and body["total"] == 0


# ═══════════════════════════════════════════════════════════
# F2：emotion/params → admin 门（PUT 直写全局引擎 _config）
# ═══════════════════════════════════════════════════════════


async def test_f2_viewer_get_emotion_params_403(sec_app, fake_engines):
    """viewer 读全局情绪参数 → 403（修复前 200：全局引擎配置裸读）。"""
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.get("/api/emotion/params", headers=alice)
    assert r.status_code == 403, f"viewer GET /api/emotion/params 必须 403，实得 {r.status_code}"


async def test_f2_viewer_put_emotion_params_403_and_no_write(sec_app, fake_engines):
    """viewer PUT 全局情绪参数 → 403 且引擎 _config 不被改写。

    修复前实锤：viewer 一发 PUT 即改写全局引擎 _config（越权写）。
    """
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.put(
        "/api/emotion/params",
        json={"baseline_mood": "狂躁", "volatility": 1.0, "resilience": 0.0},
        headers=alice,
    )
    assert r.status_code == 403, f"viewer PUT /api/emotion/params 必须 403，实得 {r.status_code}"
    eng = fake_engines.emotion_engine
    assert eng._config["baseline_mood"] == "平和", "viewer 的 PUT 不得触达全局引擎配置"


async def test_f2_admin_emotion_params_gate_passes(sec_app, fake_engines):
    """admin GET/PUT 情绪参数照常可用（门只挡 viewer，不挡 admin）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/emotion/params", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["baseline_mood"] == "平和"
    r2 = await client.put(
        "/api/emotion/params",
        json={"baseline_mood": "开朗", "volatility": 0.4, "resilience": 0.6},
        headers=admin,
    )
    assert r2.status_code == 200, r2.text
    assert fake_engines.emotion_engine._config["baseline_mood"] == "开朗"


# ═══════════════════════════════════════════════════════════
# F3：/api/stats、/api/stats/dashboard、/api/config 按主体收缩
# ═══════════════════════════════════════════════════════════


async def test_f3_stats_viewer_shrunk_to_harmless(sec_app, fake_engines):
    """viewer /api/stats 收缩：只留无害全局计数（前端只消费 working_count），
    引擎健康详情（emotion）与全局会话数不得外泄。"""
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.get("/api/stats", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) <= {"status", "working_count"}, (
        f"viewer /api/stats 应收缩为无害计数面，实得字段 {sorted(body.keys())}"
    )
    assert "emotion" not in body, "引擎健康详情不得暴露给 viewer"


async def test_f3_stats_admin_full(sec_app, fake_engines):
    """admin /api/stats 保持全量（既有契约，不因收缩而收窄 admin 视角）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/stats", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "emotion" in body and body.get("working_count") == 7
    assert body.get("has_orchestrator") is True


async def test_f3_dashboard_viewer_shrunk(sec_app, fake_engines):
    """viewer /api/stats/dashboard 收缩：保留陪伴态计数（当前情绪/好感度/记忆条数），
    剔除基础设施面（wechat bot_id/重连计数、训练管线、system_status/uptime）。"""
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.get("/api/stats/dashboard", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    forbidden = {"wechat", "wechat_connected", "training", "system_status", "uptime_seconds"}
    leaked = forbidden & set(body.keys())
    assert not leaked, f"viewer dashboard 不得携带管理面字段，泄漏 {sorted(leaked)}"
    # 陪伴态字段对 viewer 保留（StatusCenter 消费 current_emotion/affinity/recent_memories）
    assert body.get("affinity") == 42
    assert body.get("current_emotion") == "平和"


async def test_f3_dashboard_admin_full(sec_app, fake_engines):
    """admin /api/stats/dashboard 保持全量（wechat/training 管理面字段在位）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/stats/dashboard", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "wechat" in body and "training" in body and "system_status" in body


async def test_f3_config_viewer_shrunk(sec_app, fake_engines):
    """viewer GET /api/config 收缩：全局配置结构（llm 块等）不得外泄。

    前端普通用户页走 /api/user/llm-config（SettingsLLM.tsx），全局配置是 admin 面；
    但 W1 契约钉了 viewer 200（停用检测依赖它），故收缩而不 403。
    """
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    r = await client.get("/api/config", headers=alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "llm" not in body, f"viewer 不得读到全局配置块，实得键 {sorted(body.keys())}"


async def test_f3_config_admin_full(sec_app, fake_engines):
    """admin GET /api/config 保持全量（llm 块在位、api_key 已被既有掩码盖住）。"""
    client, _engine, _sf = sec_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/config", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "llm" in body
    assert body["llm"]["api_key"] == "****", "既有 _sanitize_config 掩码不得回退"


# ═══════════════════════════════════════════════════════════
# F4：/api/user/llm-config 递归掩码（providers.<name>.api_key）
# ═══════════════════════════════════════════════════════════

_NESTED_LLM = {
    "provider": "zhipu",
    "model": "glm-4-flash",
    "api_key": "sk-top-SECRET",
    "providers": {
        "zhipu": {"api_key": "sk-nested-SECRET", "model": "glm-4-flash"},
        "moonshot": {"api_key": "sk-nested2-SECRET", "model": "kimi"},
    },
}


async def test_f4_user_llm_config_masks_nested_api_keys(sec_app):
    """GET/POST /api/user/llm-config 均不得明文回显嵌套 providers.*.api_key。"""
    client, _engine, _sf = sec_app
    alice = _bearer(_ALICE_ID)
    save = await client.post(
        "/api/user/llm-config", json={"config": {"llm": _NESTED_LLM}}, headers=alice
    )
    assert save.status_code == 200, save.text
    saved_dump = json.dumps(save.json(), ensure_ascii=False)
    assert "sk-nested-SECRET" not in saved_dump, "POST 回执泄漏嵌套 api_key"
    assert "sk-top-SECRET" not in saved_dump

    load = await client.get("/api/user/llm-config", headers=alice)
    assert load.status_code == 200, load.text
    body = load.json()
    assert body["source"] == "user"
    llm = body["llm"]
    assert llm["api_key"] == "****"
    assert llm["providers"]["zhipu"]["api_key"] == "****", "嵌套 providers.zhipu.api_key 必须掩码"
    assert llm["providers"]["moonshot"]["api_key"] == "****", "嵌套 providers.moonshot.api_key 必须掩码"
    dump = json.dumps(body, ensure_ascii=False)
    assert "sk-nested2-SECRET" not in dump and "sk-nested-SECRET" not in dump
    # 非敏感字段不得被掩码波及
    assert llm["providers"]["zhipu"]["model"] == "glm-4-flash"


# ═══════════════════════════════════════════════════════════
# F5：/api/channels 活跃会话按本人过滤（admin 不过滤）
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def channels_app(tmp_path):
    """带跨用户活跃会话的 app。

    实况注记：``/api/channels`` 的静态 web/api/wechat 三条目会**占位吸收**同
    名通道类型的会话，且列表按 ``ch_type`` 去重、每类只收**第一个**活跃会话——
    泄漏形态即「他人会话排在前时其 id 前缀被展示」。故 user9 的会话先建
    （先到先得），红测才能对准真实泄漏路径。
    """
    sm = SessionManager()
    sm.create_session(str(_ALICE_ID), "web")
    sm.create_session("9", "miniprogram")
    sm.create_session(str(_BOB_ID), "miniprogram")
    app, client, engine, session_factory = _build_app(tmp_path, session_manager=sm)
    yield client, engine, session_factory, sm
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


async def test_f5_channels_viewer_sees_only_own_sessions(channels_app):
    """bob 的通道列表不得出现 user9 的会话（修复前全量广播会话 id）。"""
    client, _engine, _sf, sm = channels_app
    bob = _bearer(_BOB_ID)
    r = await client.get("/api/channels", headers=bob)
    assert r.status_code == 200, r.text
    dump = json.dumps(r.json(), ensure_ascii=False)
    assert "9:minip" not in dump, "viewer 通道列表不得泄漏他人会话 id"
    assert "3:minip" in dump, "本人的活跃会话（非静态通道）仍应出现在通道列表"


async def test_f5_channels_admin_unfiltered(channels_app):
    """admin 通道列表保持全量视角（不过滤）：他人（user9）会话仍在列表。

    实况注记：既有 ch_type 去重使每类通道只展示第一个会话（此处为 user9 的），
    这是既有展示行为、不属本次修复面；admin 断言只钉「未过滤」。
    """
    client, _engine, _sf, sm = channels_app
    admin = _bearer(_ADMIN_ID)
    r = await client.get("/api/channels", headers=admin)
    assert r.status_code == 200, r.text
    dump = json.dumps(r.json(), ensure_ascii=False)
    assert "9:minip" in dump, "admin 全量视角应包含他人会话（未过滤）"
