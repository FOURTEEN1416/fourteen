"""主动调度设置（D20 / 2026-11-08 快照化）—— 行为契约测试。

被测面（**行为**断言，不是「表写进去了」）：

1. ``utils/proactive_config`` 的**全局**读取：``config/system.yaml`` 的
   ``proactive`` 块 + ``data/scheduler_config.json`` + 静态默认；脏值/非有限数
   逐字段回落，**绝不抛异常**；全局默认值必须等于**生产装配**的真值
   （threshold=2.0 / daily=8 / interval=30 / cooldown=15），不是自洽的硬编码。
2. scheduler 决策级 / 投递级：静默窗口、引擎级频率参数按「用户 × 角色」快照
   生效（`utils.character_settings` 唯一真源），A 的设置不影响 B，引擎值直调
   该用户引擎且 ``_replay["runtime_config"]`` 保持为空；清除设置（DELETE 快照）
   后引擎按重新物化的出厂默认自动回滚。
3. 全局急停/总开关是**运维覆盖层**，不得被逐对快照架空。

2026-11-08 用户裁决：`user_proactive_config` per-user 覆盖层与墓碑回滚机制
整体退役 —— 本文件里对应的存储/事务/端点用例已随之删除；管理端 admin 全局
端点用例保留（`/api/proactive/*` 仍是全局默认的唯一写面）。

数据全部合成：``tests/conftest.py`` 已把 ``_CONFIG_PATH`` /
``character_settings._DB_PATH`` 重定向到 per-test 沙箱（否则会写开发机真实
``data/sqlite.db`` / ``data/scheduler_config.json`` —— 与 2026-09-22 块E
自伤事故同型，git 看不见）。``config/system.yaml`` 是 **tracked 部署件**，
只读、不重定向（要测的就是「生产真值」）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, ".")

os.environ.setdefault("AI_GF_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-for-proactive-scope-32chars-ok!")

from api.app_factory import create_api_app
from api.auth import configure_auth
from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, WechatBinding, get_db

_ADMIN_ID = 1
_USER_A = 2          # 已绑定 wx_a
_USER_B = 3          # 已绑定 wx_b
_USER_UNBOUND = 4    # 未绑定任何微信通道

_KEYS_A = "2:wx_a"
_KEYS_B = "3:wx_b"

#: 生产装配真值（config/system.yaml proactive + ProactiveConfig 默认）：
#: threshold 2.0 / daily 8 / interval 30 / cooldown 15。**不是**随手写的数字 ——
#: 见 test_global_defaults_match_production_config 的钉死断言。
_DEFAULTS = {
    "enabled": True,
    "paused": False,
    "quiet_hours_start": 23,
    "quiet_hours_end": 7,
    "threshold": 2.0,
    "max_daily_messages": 8,
    "min_interval_minutes": 30,
    "cooldown_after_reply_minutes": 15,
}


# ═══════════════════════════════════════════════════════════
# 公共夹具
# ═══════════════════════════════════════════════════════════


def _seed_global_config(data: dict) -> None:
    """写沙箱里的全局真源（conftest 已把 ``_CONFIG_PATH`` 指向沙箱）。"""
    from utils import proactive_config

    path = proactive_config._CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def clean_store():
    """每个用例从空的「用户 × 角色」快照库起步（``_DB_PATH`` 已由 conftest 沙箱化）。"""
    from utils import character_settings

    with contextlib.closing(character_settings._conn()) as conn, conn:
        conn.execute(f"DROP TABLE IF EXISTS {character_settings.TABLE}")
    yield


# ═══════════════════════════════════════════════════════════
# 1. 三级读取（用户覆盖 > 全局 > 默认）
# ═══════════════════════════════════════════════════════════


def test_global_defaults_match_production_config(clean_store):
    """全局默认值必须等于生产装配真值（config/system.yaml + ProactiveConfig）。

    复核缺陷（2026-10-30）：旧实现硬编码 threshold=4.0 / cooldown=10，与
    ``config/system.yaml`` 的 ``proactive.urgency_threshold: 2.0`` 和
    ``ProactiveConfig.cooldown_after_reply_minutes=15`` 不符 —— 无覆盖用户的
    ``effective`` 报错值。本用例钉住「部署真值」，改 yaml 不改这里就会红。
    """
    from utils import proactive_config

    values = proactive_config.global_config()
    assert values["threshold"] == 2.0
    assert values["max_daily_messages"] == 8
    assert values["min_interval_minutes"] == 30
    assert values["cooldown_after_reply_minutes"] == 15


def test_global_config_reads_system_yaml_proactive_block(clean_store, tmp_path, monkeypatch):
    """``config/system.yaml`` 的 ``proactive`` 块确实是引擎键的全局来源。

    yaml 用 ``urgency_threshold`` 命名、引擎用 ``threshold`` —— 这层键映射漏了
    就会静默回落模型默认（2026-10-30 突变验证当场抓到）。本用例用一份刻意
    不同于模型默认的 yaml 证明「读的不是默认值」。
    """
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text(
        "proactive:\n"
        "  max_daily_messages: 5\n"
        "  min_interval_minutes: 12\n"
        "  urgency_threshold: 3.5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)

    values = proactive_config.global_config()
    assert values["threshold"] == 3.5, "yaml 的 urgency_threshold 没有被读到（键名映射缺失）"
    assert values["max_daily_messages"] == 5
    assert values["min_interval_minutes"] == 12
    assert values["cooldown_after_reply_minutes"] == 15  # yaml 无此键 → 模型默认


def test_global_config_resolves_env_placeholders_in_system_yaml(
    clean_store, tmp_path, monkeypatch,
):
    """``${VAR}`` / ``${VAR:-default}`` 与 ConfigManager 同一解析器（复核 low）。

    直读 yaml 而不解析占位符会让「部署真源」与运行时 ``cfg.proactive`` 分叉。
    """
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text(
        "proactive:\n  urgency_threshold: ${MY_TEST_TH:-3.25}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)

    monkeypatch.setenv("MY_TEST_TH", "3.75")
    assert proactive_config.global_config()["threshold"] == 3.75
    monkeypatch.delenv("MY_TEST_TH", raising=False)
    assert proactive_config.global_config()["threshold"] == 3.25


def test_global_config_rereads_system_yaml_without_stale_cache(
    clean_store, tmp_path, monkeypatch,
):
    """无进程缓存：文件被替换且 size/mtime 不变时也必须读到新值（复核 low）。"""
    import os

    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text("proactive:\n  urgency_threshold: 3.50\n", encoding="utf-8")
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)
    before = yaml_path.stat()
    assert proactive_config.global_config()["threshold"] == 3.5

    # 同长度改写 + 还原 mtime → 旧实现的 (path, mtime_ns, size) 缓存会命中陈旧值
    yaml_path.write_text("proactive:\n  urgency_threshold: 4.50\n", encoding="utf-8")
    os.utime(yaml_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert yaml_path.stat().st_size == before.st_size
    assert proactive_config.global_config()["threshold"] == 4.5, "读到了陈旧缓存"


def test_global_engine_values_prefer_hub_replay_over_deploy_defaults(clean_store):
    """本进程 replay 里**文件未持久**的键仍作为兜底（旧口径保留）。"""
    from utils import proactive_config

    out = proactive_config.global_engine_values({
        "threshold": 6.5, "max_daily_messages": 4,
        "min_interval_minutes": None,  # None = 未设置 → 用部署默认
        "cooldown_after_reply_minutes": 20,
    })
    assert out["threshold"] == 6.5
    assert out["max_daily_messages"] == 4
    assert out["min_interval_minutes"] == 30
    assert out["cooldown_after_reply_minutes"] == 20


def test_global_engine_values_persisted_file_beats_stale_replay(clean_store):
    """持久文件逐键压过**本进程旧 replay**（4 worker 陈旧 replay 缺陷回归）。

    复核缺陷（2026-10-30 二轮）：admin 后一次保存只更新接到 POST 的 worker 的
    ``_replay``；其余 worker 的 replay 是上一次的值。旧实现 replay 优先 → 文件
    6.25/5 + 旧 replay 5.5/6 会返回 5.5/6，admin GET / 角色快照的全局默认 /
    引擎对齐全部读旧值。
    """
    import json

    from utils import proactive_config

    proactive_config._CONFIG_PATH.write_text(
        json.dumps({
            "proactive_runtime": {"threshold": 6.25, "max_daily_messages": 5},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    out = proactive_config.global_engine_values({
        "threshold": 5.5, "max_daily_messages": 6,
        "min_interval_minutes": 44,           # 文件没有该键 → replay 兜底
        "cooldown_after_reply_minutes": None,
    })

    assert out["threshold"] == 6.25, "旧 replay 覆盖了持久文件（跨 worker 读旧全局值）"
    assert out["max_daily_messages"] == 5
    assert out["min_interval_minutes"] == 44, "文件缺该键时 replay 兜底失效"
    assert out["cooldown_after_reply_minutes"] == 15  # 两边都没有 → 部署默认


def test_non_finite_engine_values_fall_back_from_system_yaml(
    clean_store, tmp_path, monkeypatch,
):
    """R1-1：yaml 的 ``.inf/.nan`` 不得让全局读取抛异常（回落模型默认）。

    生产触发面：``config/system.yaml`` 的 ``proactive.max_daily_messages`` 写成
    ``.inf``，旧 ``int(inf)`` 抛 OverflowError → ``_check_ase`` 吞成
    「ASE check failed」→ **所有用户主动消息停跳**。
    """
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text(
        "proactive:\n"
        "  max_daily_messages: .inf\n"
        "  min_interval_minutes: .nan\n"
        "  urgency_threshold: -.inf\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)

    values = proactive_config.global_config()  # 不得抛异常

    assert values["max_daily_messages"] == 8
    assert values["min_interval_minutes"] == 30
    assert values["threshold"] == 2.0


def test_non_finite_engine_values_fall_back_from_persisted_block(clean_store):
    """R1-1：``scheduler_config.json`` 的 ``Infinity/NaN`` 逐字段回落默认。"""
    import json

    from utils import proactive_config

    proactive_config._CONFIG_PATH.write_text(
        json.dumps({
            "proactive_runtime": {
                "max_daily_messages": float("inf"),
                "min_interval_minutes": float("nan"),
                "cooldown_after_reply_minutes": float("-inf"),
                "threshold": float("nan"),
            },
        }),
        encoding="utf-8",
    )

    values = proactive_config.global_config()  # 不得抛异常

    assert values["max_daily_messages"] == 8
    assert values["min_interval_minutes"] == 30
    assert values["cooldown_after_reply_minutes"] == 15
    assert values["threshold"] == 2.0


def test_non_finite_engine_values_fall_back_from_hub_replay(clean_store):
    """R1-1：hub ``_replay["runtime_config"]`` 含 inf/nan 时回落部署默认。"""
    from utils import proactive_config

    out = proactive_config.global_engine_values({
        "max_daily_messages": float("inf"),
        "min_interval_minutes": float("-inf"),
        "cooldown_after_reply_minutes": float("nan"),
        "threshold": float("nan"),
    })  # 不得抛异常

    assert out == {
        "threshold": 2.0,
        "max_daily_messages": 8,
        "min_interval_minutes": 30,
        "cooldown_after_reply_minutes": 15,
    }


def test_system_proactive_block_matches_config_manager_pipeline(tmp_path, monkeypatch):
    """``config/system.yaml`` 读取与 ``ConfigManager`` 同一管线（含点号环境覆盖）。

    复核（2026-10-30 二轮 low）：直读 yaml 只复用 ``_resolve_env_vars``，
    漏了 ``ConfigManager._effective_data`` 的 ``_apply_dot_env_overrides`` ——
    部署用 ``AI_GF_*`` 点号环境变量覆盖时，本模块的 global_default 会与运行时
    ``cfg.proactive`` 分叉。本用例直接拿 ``ConfigManager().config.proactive``
    做真源对账（两边必须逐字段相等，含一个点号环境变量在场的情形）。
    """
    from observability.config_manager import ConfigManager
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text(
        "proactive:\n"
        "  max_daily_messages: 6\n"
        "  min_interval_minutes: 20\n"
        "  urgency_threshold: 3.25\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)
    monkeypatch.setenv("AI_GF_PROACTIVE_URGENCY_THRESHOLD", "9.0")
    monkeypatch.setenv("AI_GF_ENV", "test")   # 合法环境名（ConfigManager 只接受 dev|prod|test）

    values = proactive_config.global_config()
    runtime = ConfigManager(config_dir=str(tmp_path)).config.proactive

    assert values["threshold"] == runtime.urgency_threshold
    assert values["max_daily_messages"] == runtime.max_daily_messages
    assert values["min_interval_minutes"] == runtime.min_interval_minutes
    assert values["cooldown_after_reply_minutes"] == runtime.cooldown_after_reply_minutes
    # 两边都**不**受该点号变量影响（下划线拆分后是嵌套路径 proactive.urgency.threshold，
    # pydantic 按扁平键读并忽略未知嵌套键）—— 分叉为零才是本用例要钉的口径。
    assert values["threshold"] == 3.25
    assert runtime.urgency_threshold == 3.25


def test_system_block_merges_env_specific_yaml_like_config_manager(
    tmp_path, monkeypatch,
):
    """``config/system_<env>.yaml`` 的 proactive 覆盖必须与 ConfigManager 同口径合并。"""
    from observability.config_manager import ConfigManager
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text(
        "proactive:\n  urgency_threshold: 3.25\n  max_daily_messages: 6\n",
        encoding="utf-8",
    )
    (tmp_path / "system_test.yaml").write_text(
        "proactive:\n  urgency_threshold: 4.75\n", encoding="utf-8",
    )
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)
    monkeypatch.setenv("AI_GF_ENV", "test")

    values = proactive_config.global_config()
    runtime = ConfigManager(config_dir=str(tmp_path)).config.proactive

    assert values["threshold"] == runtime.urgency_threshold == 4.75, (
        "环境专用 yaml 的 proactive 覆盖没被合并（与运行时 cfg.proactive 分叉）"
    )
    assert values["max_daily_messages"] == runtime.max_daily_messages == 6


def test_system_block_handles_whole_block_env_like_config_manager(tmp_path, monkeypatch):
    """``AI_GF_PROACTIVE``（整块字符串）时两边都回落模型默认（不单拎 yaml 值）。"""
    from observability.config_manager import ConfigManager
    from utils import proactive_config

    yaml_path = tmp_path / "system.yaml"
    yaml_path.write_text("proactive:\n  urgency_threshold: 3.25\n", encoding="utf-8")
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", yaml_path, raising=False)
    monkeypatch.setenv("AI_GF_ENV", "test")
    monkeypatch.setenv("AI_GF_PROACTIVE", "oops")  # 整块被字符串替换 → 两边形同「重置」

    values = proactive_config.global_config()
    runtime = ConfigManager(config_dir=str(tmp_path)).config.proactive

    assert values["threshold"] == runtime.urgency_threshold == 2.0, (
        "点号环境变量覆盖未应用：本模块读到了 yaml 值而运行时已回落默认"
    )


def test_effective_without_override_uses_global(clean_store):
    """无覆盖行 → 全局真源生效，``overridden_keys`` 为空（scope=global）。"""
    from utils import proactive_config

    _seed_global_config({
        "llm_proactive": {"enabled": False},
        "proactive": {"paused": True},
        "quiet_hours": {"start": 2, "end": 5},
    })

    cfg = proactive_config.effective(_KEYS_A)

    assert cfg["enabled"] is False
    assert cfg["paused"] is True
    assert cfg["quiet_hours_start"] == 2
    assert cfg["quiet_hours_end"] == 5
    assert cfg.overridden_keys == frozenset()
    assert cfg.as_dict()["threshold"] == 2.0  # 引擎级键回落部署默认


def test_effective_missing_global_file_uses_static_defaults(clean_store):
    """全局文件不存在 → 全字段回落部署默认（不抛异常）。"""
    from utils import proactive_config

    cfg = proactive_config.effective(None)
    assert cfg.as_dict() == _DEFAULTS


def _build_app(tmp_path):
    db_path = str(tmp_path / "proactive_scope.db")
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
            for uid, role in (
                (_ADMIN_ID, "admin"),
                (_USER_A, "viewer"),
                (_USER_B, "viewer"),
                (_USER_UNBOUND, "viewer"),
            ):
                session.add(
                    User(
                        id=uid,
                        email=f"u{uid}@proactive.test",
                        username=f"u{uid}",
                        hashed_password=hash_password("Passw0rd!123"),
                        display_name=f"u{uid}",
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
            session.add(WechatBinding(user_id=_USER_A, wxid="wx_a"))
            session.add(WechatBinding(user_id=_USER_B, wxid="wx_b"))
            await session.commit()

    asyncio.run(_init())

    app = create_api_app()
    app.dependency_overrides[get_db] = _get_db_override
    configure_auth(False, "")  # 本域收口的是归属与作用域，与 API_KEY_ENABLED 正交

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return app, client, engine


def _bearer(uid: int) -> dict:
    """直接签发 token（不走登录链路：IP 滑窗是进程级全局计数，多例会撞 429）。"""
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


@pytest.fixture
def api(tmp_path, clean_store):
    app, client, engine = _build_app(tmp_path)
    yield client, engine, app
    asyncio.run(client.aclose())
    asyncio.run(engine.dispose())
    configure_auth(False, "")


class _FakeEngine:
    """ASE 引擎替身：够 scheduler 的闸/引擎级下发用，且可回读真值。"""

    def __init__(self, user_key: str = "", state_path: str = ""):
        self.user_key = user_key
        self.state_path = state_path
        self._daily_message_count = 0
        self._paused = False
        self._unanswered_streak = 0
        self.urgency = type("U", (), {"total": 8.5})()
        self.tick_calls: list[bool] = []
        self.commit_calls: list[dict] = []
        self.apply_calls: list[dict] = []
        # 引擎真实存在的四个运行时字段（apply_runtime_config 的真值落点）；
        # 初值 = 生产装配默认（config/system.yaml + ProactiveConfig）。
        self._urgency_threshold = 2.0
        self._min_interval_minutes = 30
        self._cooldown_after_reply_minutes = 15
        self._max_daily_messages = 8

    def _hours_since_last_chat(self):
        return 90.0

    def tick(self, hours, emotion_state=None, dry_run=False):
        self.tick_calls.append(dry_run)
        return None

    def commit_sent(self, result):
        self.commit_calls.append(result)
        self._daily_message_count += 1

    def get_runtime_config(self):
        return {
            "threshold": self._urgency_threshold,
            "max_daily_messages": self._max_daily_messages,
            "min_interval_minutes": self._min_interval_minutes,
            "cooldown_after_reply_minutes": self._cooldown_after_reply_minutes,
        }

    def apply_runtime_config(self, **kwargs):
        self.apply_calls.append(dict(kwargs))
        if kwargs.get("threshold") is not None:
            self._urgency_threshold = float(kwargs["threshold"])
        if kwargs.get("max_daily_messages") is not None:
            self._max_daily_messages = int(kwargs["max_daily_messages"])
        if kwargs.get("min_interval_minutes") is not None:
            self._min_interval_minutes = int(kwargs["min_interval_minutes"])
        if kwargs.get("cooldown_after_reply_minutes") is not None:
            self._cooldown_after_reply_minutes = int(kwargs["cooldown_after_reply_minutes"])


class _FakeOrchForAdmin:
    def __init__(self, hub, scheduler=None):
        self._ase = hub
        self.components = {"scheduler": scheduler} if scheduler is not None else {}


async def test_admin_global_config_not_polluted_by_per_user_engine(api, monkeypatch):
    """真实 ASEHub：A 的个人值落到首引擎后，admin 读面仍必须是**全局**值。

    复核缺陷（2026-10-30）：``ASEHub.get_runtime_config()`` 返回第一个已加载
    引擎的真值 → A 的 per-user threshold 会出现在 admin GET；再保存一次就被
    广播成全局（跨用户污染）。修法：四个引擎键按 hub ``_replay`` >
    ``global_config()`` 取全局面。
    """
    from api.deps import deps
    from proactive.ase_hub import ASEHub

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)

    # 模拟调度器给 A 的引擎写个人值（hub.get 创建 → A 成为首引擎）
    eng_a = hub.get(_KEYS_A)
    eng_a.apply_runtime_config(
        threshold=12.5, max_daily_messages=2,
        min_interval_minutes=90, cooldown_after_reply_minutes=90,
    )
    assert hub.get_runtime_config()["threshold"] == 12.5, (
        "前置条件：首引擎视图确实被个人值污染（不成立则本用例测不到病灶）"
    )

    r = await client.get("/api/proactive/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"] == 2.0, "admin 读到了 A 的个人阈值（跨用户污染）"
    assert body["max_daily_messages"] == 8
    assert body["min_interval_minutes"] == 30
    assert body["cooldown_after_reply_minutes"] == 15

    assert eng_a.get_runtime_config()["threshold"] == 12.5


async def test_admin_global_config_reflects_admin_saved_values(api, monkeypatch):
    """admin 保存过的全局值（hub ``_replay``）必须回显，不被部署默认盖掉。"""
    from api.deps import deps
    from proactive.ase_hub import ASEHub
    from utils import proactive_config

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)
    monkeypatch.setattr(proactive_config, "_SYSTEM_CONFIG_PATH", _missing_system_yaml(), raising=False)

    hub.apply_runtime_config(threshold=5.5, max_daily_messages=6)
    r = await client.get("/api/proactive/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    assert r.json()["threshold"] == 5.5
    assert r.json()["max_daily_messages"] == 6


def _missing_system_yaml():
    """指向不存在的 yaml（让部署默认回落模型默认，避免用例随 yaml 改动漂移）。"""
    from pathlib import Path

    return Path("__missing_system_config__.yaml")


async def test_stale_replay_does_not_override_admin_saved_file(api, monkeypatch):
    """admin 读面：持久文件（另一次保存）必须压过本进程**旧 replay**。

    复核缺陷（2026-10-30 二轮 high）：4 worker 中只有接到 POST 的 worker 更新
    ``_replay``，其余 worker 的 replay 是旧值。若 replay 优先，admin GET 读到
    旧全局值，随后再保存一次就把它广播成全局（跨用户污染）。
    """
    import json

    from api.deps import deps
    from proactive.ase_hub import ASEHub
    from utils import proactive_config

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)
    # 本进程只有「上一次」的记账
    hub.apply_runtime_config(threshold=5.5, max_daily_messages=6)
    # 另一次 admin 保存落在别的 worker（只写持久文件）
    proactive_config._CONFIG_PATH.write_text(
        json.dumps({
            "proactive_runtime": {"threshold": 6.25, "max_daily_messages": 5},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    r = await client.get("/api/proactive/config", headers=_bearer(_ADMIN_ID))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["threshold"] == 6.25, "admin 读到了本进程旧 replay（会让下一次保存广播旧值）"
    assert body["max_daily_messages"] == 5



async def test_admin_engine_params_keep_legacy_bounds(api, monkeypatch):
    """admin 引擎参数沿用旧契约（无 ge/le），新增字段边界不得回退成 400。

    复核缺陷（2026-10-30 二轮 low）：``ProactiveConfigRequest`` 从来没有
    ge/le，``apply_runtime_config`` 只做下限钳制；若持久化复用用户自助字段表
    的 0-20 / 1-200 / 0-1440 上界，``threshold=25`` / ``max_daily=0`` 这类旧行为
    可接受的保存会变成 400（admin API 契约回退）。
    """
    import json

    from api.deps import deps
    from proactive.ase_hub import ASEHub
    from utils import proactive_config

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)

    r = await client.post(
        "/api/proactive/config",
        headers=_bearer(_ADMIN_ID),
        json={"threshold": 25.0, "max_daily": 0, "min_interval_minutes": -1},
    )
    assert r.status_code == 200, r.text

    # 持久块按**引擎同口径**归一：threshold 原值、max_daily 原值、负间隔钳 0
    raw = json.loads(proactive_config._CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["proactive_runtime"] == {
        "threshold": 25.0, "max_daily_messages": 0, "min_interval_minutes": 0,
    }
    got = await client.get("/api/proactive/config", headers=_bearer(_ADMIN_ID))
    assert got.json()["threshold"] == 25.0
    assert got.json()["max_daily_messages"] == 0
    assert got.json()["min_interval_minutes"] == 0


async def test_admin_saved_engine_globals_persist_for_other_workers(api, monkeypatch):
    """admin 保存的引擎级全局值必须落 ``scheduler_config.json``（跨 worker）。

    复核缺陷（2026-10-30 medium）：只写本进程 ``ASEHub._replay`` 时，其余 3 个
    worker 的引擎对齐值会回落部署默认 —— 同一次 admin 保存出现两种读数。
    """
    from api.deps import deps
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler
    from utils import proactive_config

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)

    r = await client.post(
        "/api/proactive/config",
        headers=_bearer(_ADMIN_ID),
        json={"threshold": 6.25, "max_daily": 5},
    )
    assert r.status_code == 200, r.text

    # 「另一个 worker」= 新 hub（本进程 replay 为空）+ 重新构造的调度器
    other_worker_hub = ASEHub(_FakeEngine)
    other_worker_hub.get(_KEYS_B)  # 有已加载引擎但无 replay
    assert getattr(other_worker_hub, "_replay", {}) == {}
    sched = ProactiveScheduler(ase_engine=other_worker_hub)
    sched.reload_config = lambda: None
    assert sched._global_engine_values(other_worker_hub)["threshold"] == 6.25
    assert sched._global_engine_values(other_worker_hub)["max_daily_messages"] == 5

    # 持久块只碰该块，不破坏其他 owner 的键
    raw = json.loads(proactive_config._CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["proactive_runtime"] == {
        "threshold": 6.25, "max_daily_messages": 5,
    }


async def test_admin_write_and_read_surfaces_still_require_admin(api, monkeypatch):
    """admin 五条面（state/config GET|POST/pause/send/history）闸原样保留。"""
    from api.deps import deps
    from proactive.ase_hub import ASEHub

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)

    calls = (
        ("get", "/api/proactive/state", None),
        ("get", "/api/proactive/config", None),
        ("post", "/api/proactive/config", {"threshold": 3.0}),
        ("post", "/api/proactive/pause", {"paused": True}),
        ("post", "/api/proactive/send", {}),
        ("get", "/api/proactive/history", None),
    )
    for method, path, payload in calls:
        kwargs = {"headers": _bearer(_USER_A)}
        if payload is not None:
            kwargs["json"] = payload
        r = await getattr(client, method)(path, **kwargs)
        assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}: {r.text[:160]}"


# ═══════════════════════════════════════════════════════════
# 3. scheduler 决策级 / 投递级（per-user 生效，不是「表写进去了」）
# ═══════════════════════════════════════════════════════════


class _DecisionSpy:
    def __init__(self):
        self.contexts: list[dict] = []


def _tick(sched) -> None:
    """跑一拍调度。先清上一拍的模型等待窗 —— ``decide_proactive`` 返回
    wait_minutes=30 会在下一拍于 :1482 直接 return，测不到引擎路径。"""
    with contextlib.suppress(Exception):
        sched._llm_proactive_next_ok.clear()
    sched._check_ase()


@pytest.fixture
def scope_sched(monkeypatch, tmp_path):
    """装配一个两用户的 hub + scheduler，并把决策链的非被测边全部替身化。"""
    import proactive.ase_hub as hub_mod
    import proactive.llm_proactive as lp
    import proactive.proactive_context as pc_mod
    import proactive.proactive_hooks as hooks_mod
    import proactive.topic_recall as recall_mod
    import shisi.agent_plane.runtime as rt
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    hub = ASEHub(_FakeEngine)
    sched = ProactiveScheduler(ase_engine=hub)
    # 时钟无关：默认全局静默窗刻意避开「现在」（凌晨跑用例时 23-7 会把所有
    # 决策闸掉，让断言随运行时刻漂移）。需要静默的用例自行覆盖该值。
    from proactive.ase_engine import _local_now as _now_local

    _hour = _now_local().hour
    sched._quiet_hours = ((_hour + 2) % 24, (_hour + 3) % 24)
    keys = ["4:peer_a", "5:peer_b"]
    # 2026-11-07 v1.65 快照语义补丁：静默判定读「用户×角色」全量快照
    # （默认 23-7，overridden_keys=全键），23:00-06:59 跑测试时快照窗会压过
    # 上面的夹具全局窗 → 不测静默的用例整族闪红。把受测对的快照静默键
    # 种成夹具窗口（与生产语义一致：快照即用户配置）；测静默的用例自行再种。
    from utils import character_settings as _cs_scope

    for _k in keys:
        _cs_scope.write(
            _k, "card-1",
            {"quiet_hours_start": (_hour + 2) % 24, "quiet_hours_end": (_hour + 3) % 24},
        )
    sched.reload_config = lambda: None
    sched._collect_ase_user_keys = lambda: list(keys)
    sched._resolve_proactive_llm = lambda eng=None: object()
    sched._resolve_character_id = lambda key: "card-1"
    sched._vital_tick_for_user = lambda key, character_id="": None
    sched._primary_model_ok = lambda: True
    sched._persist_throttle_ledger = lambda: None
    sched._emit_skip_event_daily = lambda key, reason, character_id="", **kw: True
    sched._memory = None

    events: list[dict] = []

    def _append_event(**kwargs):
        events.append(kwargs)

    spy = _DecisionSpy()

    def _decide(llm, ctx, **kwargs):
        spy.contexts.append(dict(ctx))
        return {"should_contact": False, "reason": "not_now", "wait_minutes": 30}

    monkeypatch.setattr(rt, "append_proactive_event", _append_event)
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "decide_proactive", _decide)
    monkeypatch.setattr(
        lp, "build_proactive_context",
        lambda **kw: {
            "session_key": kw.get("session_key"),
            "quiet_hours": kw.get("quiet_hours"),
        },
    )
    monkeypatch.setattr(lp, "load_proactive_knowledge", lambda cid="": "")
    monkeypatch.setattr(pc_mod, "load_full_persona_hint", lambda *a, **k: "")
    monkeypatch.setattr(
        hooks_mod, "load_live_hooks",
        lambda key, memory=None: type("H", (), {"section": "", "keywords": []})(),
    )
    monkeypatch.setattr(
        recall_mod, "build_topic_recall",
        lambda *a, **k: type("R", (), {"items": [], "judge_context": ""})(),
    )

    return sched, hub, keys, events, spy


def test_per_user_paused_only_blocks_that_user(scope_sched):
    """per-user ``paused=true``：该用户账本落 paused 并 return；另一用户照常决策。"""
    from utils import character_settings

    sched, hub, keys, events, spy = scope_sched
    character_settings.write(keys[0], "card-1", {"paused": True})

    _tick(sched)

    paused_events = [e for e in events if e.get("reason") == "paused"]
    assert [e["session_key"] for e in paused_events] == [keys[0]]
    # 关键：不是「全站暂停」——全局 _paused 仍为 False，且 B 走到了 LLM 决策
    assert sched.is_paused() is False
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]
    # 被暂停的用户不产生任何引擎 tick/投递
    assert hub.get(keys[0]).tick_calls == []


def test_per_user_disabled_only_blocks_that_user(scope_sched):
    from utils import character_settings

    sched, _hub, keys, events, spy = scope_sched
    character_settings.write(keys[0], "card-1", {"enabled": False})

    _tick(sched)

    assert [e["session_key"] for e in events if e.get("reason") == "web_disabled"] == [keys[0]]
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]


def test_per_user_quiet_hours_use_own_window(scope_sched):
    """2026-11-07 起：静默窗口按「用户 × 角色」各自一份全量快照。

    旧口径是「覆盖了哪一端用哪一端，另一端沿用全局」；用户裁决改为每角色
    一套完整设置后，每个用户都有自己的一份窗口（首次物化自出厂默认），
    改全局不再回头影响已物化的用户。本用例锁定：
    - A 的窗口 = 自己快照（现在静默 → 落 quiet_hours 账本）；
    - B 的窗口 = 自己快照（现在不静默 → 进入决策，上下文窗口是 B 自己的）。
    """
    from proactive.ase_engine import _local_now
    from utils import character_settings, proactive_config

    sched, _hub, keys, events, spy = scope_sched
    hour = _local_now().hour
    character_settings.write(
        keys[0], "card-1",
        {"quiet_hours_start": hour, "quiet_hours_end": (hour + 1) % 24},
    )
    character_settings.write(
        keys[1], "card-1",
        {"quiet_hours_start": (hour + 2) % 24, "quiet_hours_end": (hour + 3) % 24},
    )

    _tick(sched)

    assert [e["session_key"] for e in events if e.get("reason") == "quiet_hours"] == [keys[0]]
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]
    assert sched._quiet_hours_for(proactive_config.effective(keys[0], "card-1")) == (
        hour, (hour + 1) % 24,
    )
    assert sched._quiet_hours_for(proactive_config.effective(keys[1], "card-1")) == (
        (hour + 2) % 24, (hour + 3) % 24,
    )
    # 走通的用户：上下文里的窗口 = 该用户自己的窗口
    assert spy.contexts[0]["quiet_hours"] == ((hour + 2) % 24, (hour + 3) % 24)


def test_per_user_quiet_window_applies_to_delivery_and_judge(scope_sched, monkeypatch):
    """投递层与 post-draft judge 必须用**该会话**的静默窗口。

    复核缺陷（2026-10-30）：旧实现决策闸用 per-user 窗口、投递/judge 用全局
    → 用户把窗口改窄后，决策放行但投递仍被全局静默拦回，设置永远不生效。
    """
    import proactive.post_draft_judge as judge_mod
    from proactive.ase_engine import _local_now

    sched, _hub, _keys, _events, _spy = scope_sched
    hour = _local_now().hour
    sched._quiet_hours = (hour, (hour + 1) % 24)  # 全局：现在就是静默

    seen: dict = {}

    def _judge(message, **kwargs):
        seen.update(kwargs)
        return type("V", (), {"allow": True, "reason": "ok"})()

    monkeypatch.setattr(judge_mod, "judge_proactive_message", _judge)

    async def _fake_targeted(message, session_key=None, quiet_hours=None):
        seen["deliver_quiet"] = quiet_hours
        return True

    monkeypatch.setattr(sched, "_send_targeted", _fake_targeted)

    per_user_window = ((hour + 6) % 24, (hour + 7) % 24)  # 用户自己的窗口：现在不静默
    assert sched._deliver("晚安", session_key=None, quiet_hours=per_user_window) is True
    assert seen["deliver_quiet"] == per_user_window
    assert seen["quiet_hours"] == per_user_window, "judge 吃的是全局静默（per-user 设置不生效）"


def test_decision_passes_per_user_quiet_window_to_delivery(scope_sched, monkeypatch):
    """端到端接线：决策链必须把**该会话**的静默窗口交给投递层。

    复核缺陷（2026-10-30 medium）：「决策闸用 per-user、投递用全局」= 用户把
    窗口改窄后设置永远不生效。本用例走完整 ``_check_ase`` → 决策 → ``_deliver``
    路径（而非直调 ``_deliver``），钉住 ``_deliver_kwargs['quiet_hours']`` 的接线；
    同一拍里未覆盖的用户仍按全局静默被拦（零行为变更的一侧）。
    """
    import proactive.llm_proactive as lp
    from proactive.ase_engine import _local_now
    from utils import character_settings

    sched, _hub, keys, events, _spy = scope_sched
    hour = _local_now().hour
    per_user = ((hour + 6) % 24, (hour + 7) % 24)          # A：现在不静默
    quiet_window = (hour, (hour + 1) % 24)                 # B：现在静默
    character_settings.write(
        keys[0], "card-1",
        {"quiet_hours_start": per_user[0], "quiet_hours_end": per_user[1]},
    )
    character_settings.write(
        keys[1], "card-1",
        {"quiet_hours_start": quiet_window[0], "quiet_hours_end": quiet_window[1]},
    )

    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx, **kw: {
            "should_contact": True, "reason": "miss",
            "wait_minutes": None, "message": "在吗，今天过得怎么样",
        },
    )
    delivered: list[tuple[str | None, tuple[int, int] | None]] = []

    def _deliver(message, session_key=None, character_id=None, **kwargs):
        delivered.append((session_key, kwargs.get("quiet_hours")))
        return True

    monkeypatch.setattr(sched, "_deliver", _deliver)

    _tick(sched)

    assert delivered == [(keys[0], per_user)], (
        "决策链没把该角色的静默窗口交给投递层（设置端到端不生效）"
    )
    # 自己窗口现在是静默的用户：投递层从未被调用
    assert [e["session_key"] for e in events if e.get("reason") == "quiet_hours"] == [keys[1]]


def test_delivery_quiet_gate_uses_passed_window(scope_sched):
    """投递闸：传了 per-user 窗口就按它判；不传仍按全局（广播/手动线不变）。"""
    from proactive.ase_engine import _local_now

    sched, _hub, _keys, _events, _spy = scope_sched
    hour = _local_now().hour
    sched._quiet_hours = (hour, (hour + 1) % 24)  # 全局：现在静默
    per_user_window = ((hour + 6) % 24, (hour + 7) % 24)  # 该用户：现在不静默

    # 不传窗口 → 全局静默拦截，且失败原因可解释
    assert asyncio.run(sched._send_targeted("晚安", None)) is False
    assert sched._last_deliver_error == "quiet_hours"

    # 传 per-user 窗口 → 不被静默拦（无通道时后续分支返回 False，但原因不是静默）
    assert asyncio.run(sched._send_targeted("晚安", None, quiet_hours=per_user_window)) is False
    assert sched._last_deliver_error != "quiet_hours"


def test_delivery_tolerates_harness_without_quiet_hours_field():
    """既有替身（只有 `_is_quiet_hours`、没有 `_quiet_hours`）不得被新代码打断。

    复核 high：``_send_to_all``/``_send_targeted`` 直接读 ``self._quiet_hours``
    会让 ``tests/test_session_key_owner.py`` 的 `_SchedHarness` 抛
    AttributeError，且 monkeypatch ``_is_quiet_hours=False`` 的用例会被真实
    窗口拦下。本用例用最小替身钉住「无显式 per-user 窗口 → 走旧判据」。
    """
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._last_deliver_error = ""
    sched._is_quiet_hours = lambda: True  # type: ignore[method-assign]

    # 没有 _quiet_hours 字段也不得 AttributeError
    assert asyncio.run(sched._send_targeted("晚安", None)) is False
    assert sched._last_deliver_error == "quiet_hours"

    # monkeypatch 成 False → 必须真的放行（不被真实 _quiet_hours 拦）
    sched._last_deliver_error = ""
    sched._is_quiet_hours = lambda: False  # type: ignore[method-assign]
    sched._channel_instances = {}
    sched._send = None
    assert asyncio.run(sched._send_targeted("晚安", None)) is False
    assert sched._last_deliver_error != "quiet_hours"


def test_engine_level_override_applies_only_to_that_user_without_fanout(scope_sched):
    """角色快照里的引擎级值直调该用户引擎；``_replay`` 为空（未经 hub 扇出）；B 不受影响。"""
    from utils import character_settings

    sched, hub, keys, _events, _spy = scope_sched
    character_settings.write(keys[0], "card-1", {"threshold": 9.0, "max_daily_messages": 3})

    _tick(sched)

    eng_a = hub.get(keys[0])
    eng_b = hub.get(keys[1])
    assert eng_a._urgency_threshold == 9.0
    assert eng_a._max_daily_messages == 3
    assert eng_a.apply_calls, "覆盖值没有直调到该用户的引擎"
    # 频率三参必须同批下发（只传一个会把 max_daily 悄悄回落默认 8）
    payload = eng_a.apply_calls[0]
    assert set(payload) >= {
        "max_daily_messages", "min_interval_minutes", "cooldown_after_reply_minutes",
    }
    assert eng_b.apply_calls == [], "B 的引擎被别人的覆盖碰了（跨用户泄漏）"
    assert eng_b._urgency_threshold == 2.0
    assert hub._replay.get("runtime_config", {}) == {}, (
        "per-user 值经 hub.apply_runtime_config 扇出/写进 replay = 会传染给新引擎"
    )


def test_engine_level_override_is_idempotent(scope_sched):
    """值一致即不再下发：``apply_runtime_config`` 会重建频率控制器（日配额归零）。"""
    from utils import character_settings

    sched, hub, keys, _events, _spy = scope_sched
    character_settings.write(keys[0], "card-1", {"threshold": 9.0})

    _tick(sched)
    first = len(hub.get(keys[0]).apply_calls)
    _tick(sched)

    assert first == 1
    assert len(hub.get(keys[0]).apply_calls) == 1, "每 tick 重复下发会把该用户的日配额永远清零"


def test_reset_snapshot_reverts_engine_to_global(scope_sched):
    """清除设置（DELETE my-settings）后，已加载引擎必须回落到**全局**值。

    2026-11-08：墓碑机制随 per-user 层退役。快照语义下 `character_settings.reset`
    删行 → 下一次读取按当前出厂默认整份物化 → scheduler 的值比较把引擎拉回全局。
    """
    from utils import character_settings

    sched, hub, keys, _events, _spy = scope_sched
    character_settings.write(
        keys[0], "card-1", {"threshold": 9.0, "min_interval_minutes": 90},
    )
    _tick(sched)
    eng = hub.get(keys[0])
    assert (eng._urgency_threshold, eng._min_interval_minutes) == (9.0, 90)

    assert character_settings.reset(keys[0], "card-1") is True
    # 删行只删了设置，引擎值还没动（等下一拍的值比较）
    assert eng._urgency_threshold == 9.0

    _tick(sched)

    assert (eng._urgency_threshold, eng._min_interval_minutes) == (2.0, 30), (
        "清除设置后引擎仍按旧值调度（回执假成功）"
    )


def test_delete_then_repause_reverts_engine_and_keeps_new_setting(scope_sched):
    """DELETE → 重新设 paused：新设置必须存活，恢复后引擎回滚到全局。

    旧实现（墓碑 + 整行 purge）会把刚写的 paused 一起删掉 —— 用户看到
    「已暂停」却不再生效。快照语义下 DELETE 只删行、之后的写入是新行，天然不丢。

    回滚时机：paused 闸在引擎对齐**之前**短路（暂停期间本来不发消息），
    因此断言分两拍 —— 暂停拍只验「设置存活 + 账本落 paused、不投递」，
    用户恢复（paused=False）后的那一拍才把引擎从旧个人值拉回全局。
    """
    from utils import character_settings

    sched, hub, keys, events, _spy = scope_sched
    character_settings.write(
        keys[0], "card-1", {"threshold": 9.0, "max_daily_messages": 3},
    )
    _tick(sched)
    eng = hub.get(keys[0])
    assert (eng._urgency_threshold, eng._max_daily_messages) == (9.0, 3)

    character_settings.reset(keys[0], "card-1")
    character_settings.write(keys[0], "card-1", {"paused": True})
    _tick(sched)

    assert [e["session_key"] for e in events if e.get("reason") == "paused"] == [keys[0]]
    assert character_settings.read(keys[0], "card-1")["paused"] is True, (
        "回滚把 DELETE 后新写的 paused 一起删了"
    )

    character_settings.write(keys[0], "card-1", {"paused": False})
    _tick(sched)

    assert (eng._urgency_threshold, eng._max_daily_messages) == (2.0, 8), "引擎没回滚"


def test_reset_snapshot_revert_is_idempotent(scope_sched):
    """回滚后再跑一拍不得重复下发（重复下发 = 重建控制器、日配额归零）。"""
    from utils import character_settings

    sched, hub, keys, _events, _spy = scope_sched
    character_settings.write(keys[0], "card-1", {"threshold": 9.0})
    _tick(sched)
    character_settings.reset(keys[0], "card-1")
    _tick(sched)
    calls = len(hub.get(keys[0]).apply_calls)
    _tick(sched)
    assert len(hub.get(keys[0]).apply_calls) == calls


def test_other_worker_engine_converges_to_persisted_global(scope_sched):
    """持久化的全局引擎值必须落到**本 worker 的引擎**，不只是读面。

    复核缺陷（2026-10-30 二轮 medium）：admin 保存只对接到 POST 的那个 worker
    调 ``apply_runtime_config``；其余 worker 的引擎仍是构造默认 —— 用户无覆盖
    时 API 报 6.25、实际按 2.0 调度。修法：每拍把「用户值 else 持久全局值」
    对齐到该用户引擎（值一致即零写入，故稳态无副作用）。
    """
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    _seed_global_config({
        "proactive_runtime": {"threshold": 6.25, "max_daily_messages": 5},
    })
    # 2026-11-07 补：夹具的静默种子会提前物化快照（threshold 冻结在当时的
    # 全局 2.0），而快照是用户自己的真源、不再跟随全局——先重置，让下一拍
    # 的 effective() 按「当前全局 6.25」重新物化，再补种静默避开窗
    # （23-7 时段防止前置闸拦在对齐步之前）。
    from proactive.ase_engine import _local_now as _conv_now
    from utils import character_settings as _cs_conv

    _h = _conv_now().hour
    for _k in keys:
        _cs_conv.reset(_k, "card-1")
        _cs_conv.write(
            _k, "card-1",
            {"quiet_hours_start": (_h + 2) % 24, "quiet_hours_end": (_h + 3) % 24},
        )

    _tick(sched)

    for key in keys:
        eng = hub.get(key)
        assert eng._urgency_threshold == 6.25, f"{key} 的引擎没跟上持久化全局值"
        assert eng._max_daily_messages == 5

    # 稳态幂等：再跑一拍不得重复下发（重复下发会重建频率控制器、日配额归零）
    before = [len(hub.get(k).apply_calls) for k in keys]
    _tick(sched)
    assert [len(hub.get(k).apply_calls) for k in keys] == before
    assert proactive_config.global_engine_values()["threshold"] == 6.25


def test_dirty_engine_override_row_reverts_engine_to_global(scope_sched):
    """快照行被手改成非法值时，引擎里的旧个人值必须自动回落到出厂默认。

    2026-11-07 起运行时真源是「用户 × 角色」快照（不再读旧的 per-user 行）。
    本用例把快照里的 threshold 手改成越界值，锁定：
    - 读侧逐字段回落（999 → 出厂默认 2.0），不抛异常；
    - 下一拍把引擎从旧的 9.0 拉回 2.0，不把用户钉死在坏值上。
    """
    import json
    import sqlite3

    from utils import character_settings, proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    character_settings.write(keys[0], "card-1", {"threshold": 9.0})
    _tick(sched)
    eng = hub.get(keys[0])
    assert eng._urgency_threshold == 9.0

    # 手改坏快照行（越界 → 读侧应丢弃并回落）
    with sqlite3.connect(str(character_settings._DB_PATH)) as conn:
        row = conn.execute(
            f"SELECT config FROM {character_settings.TABLE} "
            "WHERE user_key = ? AND character_id = ?",
            (keys[0], "card-1"),
        ).fetchone()
        assert row is not None
        cfg = json.loads(row[0])
        cfg["threshold"] = 999
        conn.execute(
            f"UPDATE {character_settings.TABLE} SET config = ? "
            "WHERE user_key = ? AND character_id = ?",
            (json.dumps(cfg), keys[0], "card-1"),
        )

    assert character_settings.read(keys[0], "card-1")["threshold"] == 2.0
    assert proactive_config.effective(keys[0], "card-1")["threshold"] == 2.0

    _tick(sched)

    assert eng._urgency_threshold == 2.0, "非法快照把引擎永远钉在旧个人值"
    # 另一用户不受影响
    assert hub.get(keys[1])._urgency_threshold == 2.0


def test_no_override_means_zero_engine_writes(scope_sched):
    """没有任何覆盖 → 引擎一次都不写（既有全局行为逐字节不变）。"""
    sched, hub, keys, _events, spy = scope_sched

    _tick(sched)

    assert hub.get(keys[0]).apply_calls == []
    assert hub.get(keys[1]).apply_calls == []
    # N 规模批（2026-11-10）A1：两用户在默认并发宽度下走并行 fan-out，跨用户
    # 执行顺序不定——本断言只约束「每用户恰决策一次」，不约束顺序（原 == keys
    # 钉的是串行实现的偶然产物；裁决 2026-11-11 workflow escalate 授权适配）。
    assert sorted(c["session_key"] for c in spy.contexts) == sorted(keys)


def _real_adaptive_engine(tmp_path):
    """真实 ASEEngine（R1-4：不得再用 _FakeEngine 覆盖 adaptive 档位语义）。"""
    from proactive.ase_engine import ASEEngine

    return ASEEngine(
        state_path=str(tmp_path / "ase_real.json"),
        generation_mode="template",
        frequency_mode="adaptive",
        max_daily_messages=8,
        min_interval_minutes=30,
        cooldown_after_reply=15,
        urgency_threshold=2.0,
    )


def test_real_adaptive_engine_user_max_daily_caps_low_and_minimal(
    tmp_path, clean_store,
):
    """R1-2/R1-4：真实 ASEEngine 在 adaptive low/minimal 档下用户 max=1 生效。

    旧实现只把用户值写进 ``normal_daily``：low 档返回 ``low_daily=3``，
    用户每日上限 1/2 不执行。修复后 ``max_daily_messages`` 是**全档位硬上限**。
    """
    from proactive.frequency import FrequencyAdapter
    from proactive.scheduler import ProactiveScheduler
    from utils import proactive_config

    # 适配器本身：配置上限低于档位值时，low 被压到上限；minimal 原值 1 已低于
    # 上限，保持 1（上限是「不超过」，不是「抬到」）。
    for cap in (1, 2):
        probe = FrequencyAdapter(normal_daily=cap)
        probe._current_level = "low"
        assert probe.get_max_daily() == cap, f"normal={cap} 时 low 档未封顶"
        probe._current_level = "minimal"
        assert probe.get_max_daily() == min(1, cap), f"normal={cap} 时 minimal 档口径错"

    eng = _real_adaptive_engine(tmp_path)
    adapter = eng._freq_adapter
    assert adapter is not None

    # 无覆盖：原档位语义保持（normal=8 / low=3 / minimal=1）
    adapter._current_level = "low"
    assert adapter.get_max_daily() == 3
    runtime = eng.get_runtime_config()
    assert runtime["max_daily_messages"] == 3
    assert runtime["max_daily_messages_configured"] == 8, (
        "配置面读数必须是 normal 上限（8），不是当前档位值（3）——"
        "否则 scheduler 在 low 档每拍都会判不一致"
    )
    adapter._current_level = "minimal"
    assert adapter.get_max_daily() == 1

    from utils import character_settings as _cs_adaptive

    _cs_adaptive.write(_KEYS_A, "card-1", {"max_daily_messages": 1})
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    globals_ = proactive_config.global_engine_values()
    sched._apply_user_engine_config(
        None, eng, _KEYS_A, proactive_config.effective(_KEYS_A, "card-1"),
        engine_globals=globals_,
    )

    assert eng._freq_adapter is adapter, "apply_runtime_config 重建了适配器（降档状态风险）"
    assert adapter._current_level == "minimal", "下发配置把已积累的降档状态清零了"
    assert adapter.get_max_daily() == 1
    adapter._current_level = "low"
    assert adapter.get_max_daily() == 1, "low 档仍返回 low_daily=3，用户上限 1 不执行"
    adapter._current_level = "minimal"
    assert adapter.get_max_daily() == 1
    adapter._current_level = "normal"
    assert adapter.get_max_daily() == 1

    # 稳态：再对齐一次必须零下发（旧实现每拍重建并刷 INFO）
    calls: list[dict] = []
    original = eng.apply_runtime_config

    def _spy(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    eng.apply_runtime_config = _spy  # type: ignore[method-assign]
    sched._apply_user_engine_config(
        None, eng, _KEYS_A, proactive_config.effective(_KEYS_A, "card-1"),
        engine_globals=globals_,
    )
    assert calls == [], "adaptive 稳态仍每拍下发（会重建适配器/清零日配额风险）"
    assert eng._freq_adapter is adapter


def test_real_adaptive_engine_without_override_keeps_tier_and_is_idempotent(
    tmp_path, clean_store,
):
    """R1-2：无覆盖时 adaptive 档位值保持，且 low 档下不得每拍下发。

    旧 ``get_runtime_config()["max_daily_messages"]`` 报当前档位（low=3），
    scheduler 拿它和 normal 上限 8 比较永远不等 → 每拍重建。
    """
    from proactive.scheduler import ProactiveScheduler
    from utils import proactive_config

    eng = _real_adaptive_engine(tmp_path)
    eng._freq_adapter._current_level = "low"

    calls: list[dict] = []
    original = eng.apply_runtime_config

    def _spy(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    eng.apply_runtime_config = _spy  # type: ignore[method-assign]
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._apply_user_engine_config(
        None, eng, "6:no_override", proactive_config.effective("6:no_override"),
        engine_globals=proactive_config.global_engine_values(),
    )

    assert calls == [], "无覆盖用户也被每拍下发（adaptive low 档比较口径错误）"
    assert eng._freq_adapter.get_max_daily() == 3, "无覆盖时 low 档原值被改"
    eng._freq_adapter._current_level = "minimal"
    assert eng._freq_adapter.get_max_daily() == 1, "无覆盖时 minimal 档原值被改"


def test_global_pause_and_switch_override_character_snapshots(scope_sched):
    """2026-11-07：全局急停/总开关是运维覆盖层，不得被逐对快照架空。

    用户裁决「设置按 用户×角色」后，每份快照都带 paused=False / enabled=True。
    若调度器只按快照取值，管理员事故时按不下全局暂停 —— 本用例钉死：
    - 全局 paused=True ⇒ 所有用户都跳过（即使各自快照 paused=False）；
    - 全局 enabled=False ⇒ 所有用户都跳过（即使各自快照 enabled=True）。
    """
    from utils import character_settings

    sched, _hub, keys, events, spy = scope_sched
    for key in keys:
        character_settings.write(key, "card-1", {"paused": False, "enabled": True})

    sched._paused = True
    _tick(sched)
    # N 规模批（2026-11-10）A1：两用户并行 fan-out 下跨用户 paused 事件落账
    # 顺序不定——本断言只约束「每个 key 的 paused 事件恰一次」，不约束顺序
    # （裁决 2026-11-11 workflow escalate 授权适配，同 test_no_override 注）。
    assert sorted(
        e["session_key"] for e in events if e.get("reason") == "paused"
    ) == sorted(keys)
    assert spy.contexts == []

    sched._paused = False
    events.clear()
    import proactive.llm_proactive as lp

    original = lp.read_web_proactive_config
    lp.read_web_proactive_config = lambda: {"enabled": False}  # type: ignore[assignment]
    try:
        _tick(sched)
    finally:
        lp.read_web_proactive_config = original  # type: ignore[assignment]
    assert spy.contexts == []
