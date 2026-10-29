"""拟人度配置 API（``/api/humanize/config``）— 行为契约测试。

被测面（设计契约 ``api_spec`` / ``humanize_schema``）：

- ``GET  /api/humanize/config`` —— admin 门 + **纯文件热读**（不依赖
  ``orch._ase``，故无 503）；响应 ``{'status':'ok','config':{…全部字段…,
  'max_segments':2,'delay_mode_label':'逐字(按字数)'}}``。
- ``POST /api/humanize/config``（前端 W11-D5 消费的写法；``PUT`` 同一实现）
  —— partial merge：只改传入键；非法枚举/越界 422，``max_segments`` ≠2 400。
- 写入走 ``utils.json_state.update_json``（跨进程 flock + ``os.replace``）
  且**只触碰** ``humanize`` 块 —— 与 ``/api/proactive/config`` 并发保存同一
  份 ``data/scheduler_config.json`` 不得互相覆盖。
- 读侧无进程缓存：别的 worker 写完后本 worker 下一次读立刻可见。

数据全部合成：``utils.humanize_config._CONFIG_PATH`` 经 fixture 重定向到
per-test 临时文件（**绝不触碰**开发机真实 ``data/scheduler_config.json``，
该文件不入版本控制，污染不会被 git 发现 —— 与 conftest 记录的
2026-09-22 块E 自伤事故同型防线）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-humanize-api-32chars-ok!!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db

_ADMIN_ID = 1
_VIEWER_ID = 2

#: 契约钉死的响应字段全集（GET/POST/PUT 三个方向的 config 形状一致）。
#: 2026-10-27 G-1 新增 ``user_override_enabled``；P0-B 新增阅读延迟/typing 七字段。
_EXPECTED_KEYS = {
    "delay_mode",
    "per_char_ms",
    "fixed_ms",
    "random_min_ms",
    "random_max_ms",
    "delay_floor_ms",
    "delay_ceil_ms",
    "bracket_delay_ms",
    "proactive_split",
    "read_delay_enabled",
    "read_delay_min_ms",
    "read_delay_max_ms",
    "read_delay_chars_per_second",
    "read_delay_max_total_ms",
    "read_delay_channel_cap_ms",
    "typing_enabled",
    "typo_rate",
    "user_override_enabled",
    "max_segments",
    "delay_mode_label",
}

_DEFAULTS = {
    "delay_mode": "dynamic",
    "per_char_ms": 45,
    "fixed_ms": 600,
    "random_min_ms": 400,
    "random_max_ms": 1600,
    "delay_floor_ms": 400,
    "delay_ceil_ms": 1600,
    "bracket_delay_ms": 0,
    "proactive_split": False,
    "read_delay_enabled": True,
    "read_delay_min_ms": 1000,
    "read_delay_max_ms": 6000,
    "read_delay_chars_per_second": 0,
    "read_delay_max_total_ms": 30000,
    "read_delay_channel_cap_ms": 5000,
    "typing_enabled": True,
    "typo_rate": 0,
    "user_override_enabled": True,
    "max_segments": 3,
}


# ═══════════════════════════════════════════════════════════
# App 构造（真实角色门，不 override 身份依赖）
# ═══════════════════════════════════════════════════════════


def _build_app(tmp_path):
    db_path = str(tmp_path / "humanize_api.db")
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
            for uid, email, role in (
                (_ADMIN_ID, "admin@humanize.test", "admin"),
                (_VIEWER_ID, "viewer@humanize.test", "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=email,
                        username=email.split("@")[0],
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=email.split("@")[0],
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")  # 本域收口的是角色门，与 API_KEY_ENABLED 正交

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine


def _bearer(uid: int) -> dict:
    """直接签发 access token（库内 token_version 默认 0 对齐）。

    不走 POST /api/auth/login：登录链路的 IP 滑窗是**进程级全局**计数，
    同进程多用例连发会撞 429（与 test_sec_p0_read_gates 同款规避）。
    """
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def api(tmp_path):
    app, client, engine = _build_app(tmp_path)
    yield client, engine, app
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


@pytest.fixture
def hm_file(tmp_path, monkeypatch):
    """把 humanize 真源重定向到 per-test 临时文件（禁止写开发机真实 data/）。"""
    from utils import humanize_config

    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(humanize_config, "_CONFIG_PATH", path)
    return path


def _seed(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _humanize_block(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["humanize"]


# ═══════════════════════════════════════════════════════════
# 鉴权（与 /api/proactive/config 同门：api key + admin）
# ═══════════════════════════════════════════════════════════


async def test_get_without_token_is_401(api, hm_file):
    client, _engine, _app = api
    r = await client.get("/api/humanize/config")
    assert r.status_code == 401, r.text


@pytest.mark.parametrize("method", ["get", "post", "put"])
async def test_viewer_jwt_is_403(api, hm_file, method):
    client, _engine, _app = api
    kwargs = {"headers": _bearer(_VIEWER_ID)}
    if method != "get":
        kwargs["json"] = {}
    r = await getattr(client, method)("/api/humanize/config", **kwargs)
    assert r.status_code == 403, f"viewer {method.upper()} 必须 403，实得 {r.status_code}：{r.text[:200]}"


# ═══════════════════════════════════════════════════════════
# GET：默认值 / 形状 / 热读 / 容错
# ═══════════════════════════════════════════════════════════


async def test_get_defaults_match_zero_change_formula(api, hm_file):
    """文件不存在时返回契约默认值（= 现行公式的等价参数，零行为变更）。"""
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    config = body["config"]
    assert set(config) == _EXPECTED_KEYS, config
    for key, expected in _DEFAULTS.items():
        assert config[key] == expected, f"{key}: {config[key]!r} != {expected!r}"
    assert config["delay_mode_label"] == "逐字(按字数)"
    assert not hm_file.exists(), "GET 是只读端点，不得创建真源文件"


async def test_get_does_not_need_orchestrator(api, hm_file):
    """无 orch._ase 的 worker 也必须能答（契约：GET 无 503）。"""
    from api.deps import deps

    client, _engine, _app = api
    saved = deps.orch
    deps.orch = None
    try:
        r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    finally:
        deps.orch = saved
    assert r.status_code == 200, r.text


async def test_dirty_file_falls_back_per_field(api, hm_file):
    """非法枚举/越界/类型不符 → 逐字段回落默认，不 500（照 read_reply_mode 容忍）。

    随机区间倒置（两侧单看都在界内）按**读侧愈合**处理：一并回落默认，
    否则 ``randint(min, max)`` 会在投递时刻抛 ``ValueError``。
    """
    _seed(hm_file, {"humanize": {
        "delay_mode": "bogus",
        "per_char_ms": 9999,
        "fixed_ms": "600",
        "random_min_ms": 8000,
        "random_max_ms": 100,
        "proactive_split": "yes",
    }})
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["delay_mode"] == "dynamic"
    assert config["per_char_ms"] == 45
    assert config["fixed_ms"] == 600
    assert config["random_min_ms"] == 400
    assert config["random_max_ms"] == 1600
    assert config["proactive_split"] is False


async def test_inverted_clamp_pair_is_healed_on_read(api, hm_file):
    _seed(hm_file, {"humanize": {"delay_floor_ms": 2500, "delay_ceil_ms": 300}})
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["delay_floor_ms"] == 400
    assert config["delay_ceil_ms"] == 1600


async def test_non_dict_block_falls_back(api, hm_file):
    _seed(hm_file, {"humanize": [1, 2, 3], "reply_mode": "novel"})
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    assert r.json()["config"]["per_char_ms"] == 45


async def test_get_is_hot_read_no_process_cache(api, hm_file):
    """别的 worker 写入后本 worker 立刻可见（读侧无进程缓存）。"""
    from utils import json_state

    _seed(hm_file, {"humanize": {"per_char_ms": 45}})
    client, _engine, _app = api
    first = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert first.json()["config"]["per_char_ms"] == 45

    # 旁路写入（模拟另一 worker）：只经 json_state，不经过本进程的写函数
    def _mutate(data: dict) -> None:
        data["humanize"] = {"per_char_ms": 120}

    json_state.update_json(hm_file, _mutate)
    second = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID))
    assert second.json()["config"]["per_char_ms"] == 120, "读到了陈旧快照（进程内缓存）"


# ═══════════════════════════════════════════════════════════
# POST / PUT：partial merge 与真源落盘
# ═══════════════════════════════════════════════════════════


async def test_post_partial_merge_and_sibling_keys_preserved(api, hm_file):
    """partial merge 只改传入键；同文件的其他 owner 键逐字保留。"""
    _seed(hm_file, {
        "reply_mode": "novel",
        "quiet_hours": {"start": 22, "end": 7},
        "llm_proactive": {"enabled": True},
    })
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"per_char_ms": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["config"]["per_char_ms"] == 100
    assert body["config"]["delay_mode"] == "dynamic"  # 未传 → 保持原值
    assert body["config"]["fixed_ms"] == 600
    assert set(body["config"]) == _EXPECTED_KEYS

    on_disk = json.loads(hm_file.read_text(encoding="utf-8"))
    assert on_disk["humanize"] == {"delay_mode": "dynamic", "per_char_ms": 100,
                                   "fixed_ms": 600, "random_min_ms": 400,
                                   "random_max_ms": 1600, "delay_floor_ms": 400,
                                   "delay_ceil_ms": 1600, "bracket_delay_ms": 0,
                                   "proactive_split": False,
                                   # 2026-10-27 G-1/P0-B：新增字段随完整配置落盘
                                   "read_delay_enabled": True,
                                   "read_delay_min_ms": 1000,
                                   "read_delay_max_ms": 6000,
                                   "read_delay_chars_per_second": 0,
                                   "read_delay_max_total_ms": 30000,
                                   "read_delay_channel_cap_ms": 5000,
                                   "typing_enabled": True,
                                   "typo_rate": 0,
                                   "user_override_enabled": True,
                                   "max_segments": 3}
    assert on_disk["reply_mode"] == "novel"
    assert on_disk["quiet_hours"] == {"start": 22, "end": 7}
    assert on_disk["llm_proactive"] == {"enabled": True}


async def test_put_same_write_path_as_post(api, hm_file):
    client, _engine, _app = api
    r = await client.put("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                         json={"delay_mode": "fixed", "fixed_ms": 800})
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["delay_mode"] == "fixed"
    assert config["fixed_ms"] == 800
    assert config["delay_mode_label"] == "固定延时"
    assert _humanize_block(hm_file)["fixed_ms"] == 800


async def test_post_empty_body_is_noop_but_ok(api, hm_file):
    """空 body = 不改任何键（前端保存无改动时必须仍拿到 status ok 回执）。"""
    _seed(hm_file, {"humanize": {"per_char_ms": 77}})
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID), json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["config"]["per_char_ms"] == 77


async def test_post_write_goes_through_json_state_update_json(api, hm_file, monkeypatch):
    """跨 4 worker 一致性的前提：写入必须经 json_state.update_json（flock+replace）。"""
    from utils import humanize_config, json_state

    calls: list[str] = []
    real = json_state.update_json

    def _spy(path, mutate, default=None):
        calls.append(str(path))
        return real(path, mutate, default)

    monkeypatch.setattr(humanize_config.json_state, "update_json", _spy)
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"per_char_ms": 90})
    assert r.status_code == 200, r.text
    assert calls == [str(hm_file)], f"写入未走 update_json（或走了别的路径）：{calls}"


async def test_concurrent_partial_writes_do_not_lose_updates(api, hm_file):
    """并发 partial merge 不得丢更新（锁内读改写；Windows 下由进程内锁保证）。"""
    from utils import humanize_config

    _seed(hm_file, {"humanize": {}})
    fields = [
        ("per_char_ms", 60),
        ("fixed_ms", 700),
        ("random_min_ms", 300),
        ("random_max_ms", 2200),
        ("delay_floor_ms", 250),
        ("delay_ceil_ms", 3300),
        ("bracket_delay_ms", 600),
        ("delay_mode", "random"),
        ("proactive_split", True),
    ]
    with ThreadPoolExecutor(max_workers=len(fields)) as pool:
        futures = [pool.submit(humanize_config.write_humanize, **{k: v}) for k, v in fields]
        readback = [f.result() for f in futures]

    on_disk = _humanize_block(hm_file)
    for key, value in fields:
        assert on_disk[key] == value, f"丢更新：{key} = {on_disk[key]!r}，期望 {value!r}"
    # 最后完成的那次调用返回的也是完整合并结果（不是它自己的局部视图）
    assert all(cfg.max_segments == 3 for cfg in readback)


# ═══════════════════════════════════════════════════════════
# 校验：422（Pydantic 先行）/ 400（业务语义）
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("payload", [
    {"delay_mode": "typing"},
    {"per_char_ms": 4},
    {"per_char_ms": 201},
    {"fixed_ms": 5001},
    {"bracket_delay_ms": 5001},
    {"per_char_ms": "abc"},
    {"random_min_ms": 2000, "random_max_ms": 100},
    {"delay_floor_ms": 3000, "delay_ceil_ms": 2000},
    {"max_segments": 1},
    {"max_segments": 5},
    {"typo_rate": 101},
])
async def test_invalid_payload_is_422(api, hm_file, payload):
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID), json=payload)
    assert r.status_code == 422, f"{payload} 必须 422，实得 {r.status_code}：{r.text[:200]}"
    assert not hm_file.exists(), "校验失败的请求不得落盘"


async def test_numeric_string_is_coerced_by_pydantic(api, hm_file):
    """Pydantic 宽松模式把 "45" 归一为 45 —— 行为钉死，避免日后被当成"静默补全"。"""
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"per_char_ms": "45"})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["per_char_ms"] == 45
    assert _humanize_block(hm_file)["per_char_ms"] == 45


async def test_max_segments_three_is_accepted_and_persisted(api, hm_file):
    """2026-10-27 Q6：拆条上限可放宽到 3/4（默认仍 2），并写入真源。"""
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"max_segments": 3})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["max_segments"] == 3
    assert _humanize_block(hm_file)["max_segments"] == 3


async def test_max_segments_two_is_accepted_and_persisted(api, hm_file):
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"max_segments": 2, "per_char_ms": 50})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["max_segments"] == 2
    assert _humanize_block(hm_file)["max_segments"] == 2


async def test_cross_field_conflict_after_merge_is_400_and_file_untouched(api, hm_file):
    """单看入参合法、合并后越界（floor 2000 > 既有 ceil 1600）→ 400 且不落盘。"""
    _seed(hm_file, {"humanize": {"delay_ceil_ms": 1600}})
    before = hm_file.read_bytes()
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"delay_floor_ms": 2000})
    assert r.status_code == 400, r.text
    assert "delay_floor_ms" in r.json()["detail"]
    assert hm_file.read_bytes() == before, "校验失败不得改写真源（禁半份配置）"


async def test_off_mode_round_trip(api, hm_file):
    """off 模式（不延时）可写可读，标签随之变化。"""
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"delay_mode": "off", "proactive_split": True})
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["delay_mode"] == "off"
    assert config["delay_mode_label"] == "不延时"
    assert config["proactive_split"] is True
    assert _humanize_block(hm_file)["proactive_split"] is True


# ═══════════════════════════════════════════════════════════
# NEW-12（2026-10-29）：user_key 数字 user_id 还原（与 reply-mode 同一还原器）
# ═══════════════════════════════════════════════════════════


async def test_user_key_full_session_key_passthrough(api, hm_file):
    """含 ':' 的 user_key 视为完整会话键原样使用（脚本/API 直呼契约不变）。"""
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                         params={"user_key": "2:peer@im.wechat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user"
    assert body["user_key"] == "2:peer@im.wechat"


async def test_user_key_numeric_unbound_is_400(api, hm_file):
    """纯数字 user_key 无微信绑定时 400（宁拒绝不猜键，与 reply-mode 同口径）。"""
    client, _engine, _app = api
    r = await client.get("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                         params={"user_key": str(_VIEWER_ID)})
    assert r.status_code == 400, r.text
    assert "未绑定" in r.json()["detail"]


async def test_post_user_key_numeric_unbound_is_400(api, hm_file):
    """POST 写入方向同门：无绑定数字 id 拒绝写覆盖行（不写坏键）。"""
    client, _engine, _app = api
    r = await client.post("/api/humanize/config", headers=_bearer(_ADMIN_ID),
                          json={"user_key": str(_VIEWER_ID), "typo_rate": 1})
    assert r.status_code == 400, r.text
    assert hm_file.exists() is False or "user_humanize" not in _humanize_block(hm_file)


# ═══════════════════════════════════════════════════════════
# NEW-34（2026-10-29）：用户自助拟人节奏 /api/user/humanize
# 作用域键服务端从 token 派生（绑定还原，不猜键）；未绑定 → GET 全局视图 / 写 400
# ═══════════════════════════════════════════════════════════


async def test_user_self_unbound_get_global_post_400(api, hm_file):
    """未绑定微信：GET 回全局视图（scope=global）；写/清 400 宁拒绝不猜键。"""
    client, _engine, _app = api
    r = await client.get("/api/user/humanize", headers=_bearer(_VIEWER_ID))
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "global"

    r2 = await client.post("/api/user/humanize", headers=_bearer(_VIEWER_ID),
                           json={"typo_rate": 5})
    assert r2.status_code == 400
    assert "未绑定" in r2.json()["detail"]

    r3 = await client.delete("/api/user/humanize", headers=_bearer(_VIEWER_ID))
    assert r3.status_code == 400


async def test_user_self_bound_write_read_clear(api, hm_file):
    """已绑定用户全链路：写覆盖 → 合并视图生效 → 清除回落。"""
    from api.database import WechatBinding

    client, engine, app = api
    # 给 viewer 补一条微信绑定（_user_session_key_for 按此还原 N:wxid）
    engine2 = engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _sm

    session_factory = _sm(engine2, expire_on_commit=False)
    async with session_factory() as session:
        session.add(WechatBinding(user_id=_VIEWER_ID, wxid="wx_selftest", nickname="selftest"))
        await session.commit()

    r1 = await client.get("/api/user/humanize", headers=_bearer(_VIEWER_ID))
    assert r1.status_code == 200
    body = r1.json()
    assert body["scope"] == "global"  # 无覆盖行 → 合并视图=全局，但键已在
    assert body["user_key"].startswith(f"{_VIEWER_ID}:")

    r2 = await client.post("/api/user/humanize", headers=_bearer(_VIEWER_ID),
                           json={"typo_rate": 7})
    assert r2.status_code == 200, r2.text
    assert r2.json()["scope"] == "user"

    r3 = await client.get("/api/user/humanize", headers=_bearer(_VIEWER_ID))
    assert r3.json()["scope"] == "user"
    assert r3.json()["config"]["typo_rate"] == 7
    # 真源落在 user_humanize 覆盖库（conftest 沙箱重定向；scheduler_config.json 只放全局块）
    import sqlite3 as _sq

    from utils import humanize_config as _hc

    conn = _sq.connect(str(_hc._OVERRIDE_DB_PATH))
    try:
        row = conn.execute(
            "SELECT config FROM user_humanize WHERE user_key = ?",
            (r1.json()["user_key"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and 'typo_rate' in row[0]

    r4 = await client.delete("/api/user/humanize", headers=_bearer(_VIEWER_ID))
    assert r4.status_code == 200
    assert r4.json()["status"] == "ok"


async def test_user_self_ignores_client_user_key(api, hm_file):
    """🔴 安全语义：客户端自报 user_key 一律忽略（不能借自助端点改别人节奏）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker as _sm

    from api.database import WechatBinding

    client, engine, _app = api
    session_factory = _sm(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(WechatBinding(user_id=_VIEWER_ID, wxid="wx_selftest2", nickname="selftest2"))
        await session.commit()

    r = await client.post(
        "/api/user/humanize",
        headers=_bearer(_VIEWER_ID),
        json={"typo_rate": 9, "user_key": "1:evil@im.wechat"},
    )
    assert r.status_code == 200, r.text
    # 写入的是自己派生的键，不是客户端报的键（真源 = user_humanize 覆盖库）
    import sqlite3 as _sq

    from utils import humanize_config as _hc

    conn = _sq.connect(str(_hc._OVERRIDE_DB_PATH))
    try:
        rows = dict(conn.execute("SELECT user_key, config FROM user_humanize").fetchall())
    finally:
        conn.close()
    assert "1:evil@im.wechat" not in rows
    own_key = f"{_VIEWER_ID}:wx_selftest2"
    assert own_key in rows and "typo_rate" in rows[own_key]
