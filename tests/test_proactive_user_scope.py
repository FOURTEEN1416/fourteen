"""主动调度 per-user 覆盖（D20，2026-10-30）—— 行为契约测试。

被测面（**行为**断言，不是「表写进去了」）：

1. ``utils/proactive_config`` 三级读取：用户覆盖 > 全局（``config/system.yaml``
   的 ``proactive`` 块 + ``data/scheduler_config.json``）> 静态默认；脏行 /
   库不可用 → 逐字段回落全局，**绝不抛异常**；全局默认值必须等于**生产装配**
   的真值（threshold=2.0 / daily=8 / interval=30 / cooldown=15），不是自洽的
   硬编码。
2. 4 个自助端点（``/api/user/proactive-config`` GET/POST/DELETE +
   ``/api/user/proactive/pause``）：A 的覆盖不影响 B；请求体里自报的
   ``user_key`` 一律忽略；四条未绑定微信全部 400；admin 的
   ``/api/proactive/config`` 全局值不受任何用户覆盖影响（真实 ASEHub 首引擎
   污染路径）+ admin 五条写/读面闸原样保留。
3. scheduler 决策级 + 投递级：per-user ``paused`` / ``enabled`` / 静默时段只让
   **该用户**在该闸 return（账本 reason 逐条断言），另一用户照常走到 LLM 决策；
   引擎级覆盖直调该用户引擎且 ``_replay["runtime_config"]`` 保持为空；
   **清除覆盖后引擎回滚到全局**（墓碑行 → 下一拍还原 → 删行）。
4. 删号 purge：``structured_memory.purge_owner_data`` 必须清掉本表行（隐私红线）。

数据全部合成：``tests/conftest.py`` 已把 ``_OVERRIDE_DB_PATH`` /
``_CONFIG_PATH`` 重定向到 per-test 沙箱（否则会写开发机真实
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


def _write_override_row(user_key: str, raw: dict) -> None:
    """绕过写侧校验直接落一行（模拟「手改坏了的库」）。"""
    from utils import proactive_config

    with contextlib.closing(proactive_config._override_conn()) as conn, conn:
        conn.execute(
            f"INSERT INTO {proactive_config.OVERRIDE_TABLE} (user_key, config, updated_at) "
            f"VALUES (?, ?, '') ON CONFLICT(user_key) DO UPDATE SET config = excluded.config",
            (user_key, json.dumps(raw, ensure_ascii=False)),
        )


@pytest.fixture
def clean_store():
    """每个用例从空覆盖表起步（``_OVERRIDE_DB_PATH`` 已由 conftest 沙箱化）。"""
    from utils import proactive_config

    with contextlib.closing(proactive_config._override_conn()) as conn, conn:
        conn.execute(f"DELETE FROM {proactive_config.OVERRIDE_TABLE}")
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
    6.25/5 + 旧 replay 5.5/6 会返回 5.5/6，admin GET / 用户 global_default /
    墓碑回滚全部读旧值。
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


def test_user_override_wins_over_global(clean_store):
    """覆盖行命中 → 逐键覆盖全局；未覆盖的键仍跟随全局（键缺失 = 继承）。"""
    from utils import proactive_config

    _seed_global_config({
        "llm_proactive": {"enabled": True},
        "quiet_hours": {"start": 22, "end": 8},
    })
    proactive_config.write_proactive_config(
        user_key=_KEYS_A, paused=True, min_interval_minutes=90,
    )

    cfg = proactive_config.effective(_KEYS_A)

    assert cfg["paused"] is True                    # 用户覆盖
    assert cfg["min_interval_minutes"] == 90        # 用户覆盖
    assert cfg["enabled"] is True                   # 未覆盖 → 全局
    assert cfg["quiet_hours_start"] == 22           # 未覆盖 → 全局
    assert cfg["max_daily_messages"] == 8           # 未覆盖 → 部署默认
    assert cfg.overridden_keys == frozenset({"paused", "min_interval_minutes"})
    # 覆盖是**该用户**的：另一个键仍是全局视图
    assert proactive_config.effective(_KEYS_B).overridden_keys == frozenset()


def test_dirty_override_row_falls_back_per_field(clean_store):
    """库内脏值/未知键/越界 → 逐字段回落全局与默认，且绝不抛异常。"""
    from utils import proactive_config

    _seed_global_config({"proactive": {"paused": True}, "quiet_hours": {"start": 1, "end": 6}})
    _write_override_row(_KEYS_A, {
        "paused": "yes",             # 类型不符 → 丢弃（回落全局 True）
        "threshold": 999,            # 越界 → 丢弃（回落部署默认 2.0）
        "quiet_hours_end": 30,       # 越界小时 → 丢弃（回落全局 6）
        "max_daily_messages": 3,     # 合法 → 生效
        "bogus_key": 1,              # 未知 → 丢弃
    })

    cfg = proactive_config.effective(_KEYS_A)

    assert cfg["paused"] is True
    assert cfg["threshold"] == 2.0
    assert cfg["quiet_hours_end"] == 6
    assert cfg["max_daily_messages"] == 3
    assert "bogus_key" not in cfg.values
    assert cfg.overridden_keys == frozenset({"max_daily_messages"})


def test_override_db_unavailable_falls_back_to_global(clean_store, monkeypatch):
    """覆盖库不可用（损坏/权限）→ 回落全局，**绝不抛异常炸主链**。"""
    import sqlite3

    from utils import proactive_config

    _seed_global_config({"proactive": {"paused": False}, "llm_proactive": {"enabled": False}})

    def _boom():
        raise sqlite3.OperationalError("database is locked / unreadable")

    monkeypatch.setattr(proactive_config, "_override_conn", _boom)

    cfg = proactive_config.effective(_KEYS_A)
    assert cfg["enabled"] is False
    assert cfg.overridden_keys == frozenset()
    assert proactive_config.read_override(_KEYS_A) == {}
    assert proactive_config.has_override(_KEYS_A) is False
    assert proactive_config.pending_revert_keys(_KEYS_A) == []


def test_clear_override_surfaces_db_failure(clean_store, monkeypatch):
    """清除失败必须**上抛**（旧实现吞异常返回 False → DELETE 回 noop 假成功）。"""
    import sqlite3

    from utils import proactive_config

    def _boom():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(proactive_config, "_override_conn", _boom)
    with pytest.raises(sqlite3.Error):
        proactive_config.clear_override(_KEYS_A)


def test_write_rejects_unknown_out_of_range_and_empty_key(clean_store):
    """写侧严格：未知键 / 类型不符 / 越界 / 空 user_key → ``ValueError`` 且不落行。"""
    from utils import proactive_config

    with pytest.raises(ValueError):
        proactive_config.write_proactive_config(user_key=_KEYS_A, nope=1)
    with pytest.raises(ValueError):
        proactive_config.write_proactive_config(user_key=_KEYS_A, paused="yes")
    with pytest.raises(ValueError):
        proactive_config.write_proactive_config(user_key=_KEYS_A, quiet_hours_start=24)
    with pytest.raises(ValueError):
        proactive_config.write_proactive_config(user_key=_KEYS_A, max_daily_messages=0)
    with pytest.raises(ValueError):
        # 本模块没有全局写路径（全局面唯一 owner = /api/proactive/config）
        proactive_config.write_proactive_config(user_key="", paused=True)

    assert proactive_config.read_override(_KEYS_A) == {}


def test_override_read_modify_write_is_one_immediate_transaction(clean_store, monkeypatch):
    """「读—合并—写」必须在同一 ``BEGIN IMMEDIATE`` 事务里（跨进程串行化）。

    复核缺陷（2026-10-30 二轮 low）：旧实现读、写各开一个连接，4 worker 下
    DELETE 与 POST 并发会用陈旧快照互相覆盖（丢新覆盖 / 墓碑被写回）。
    """
    from utils import proactive_config

    executed: list[str] = []
    real_conn = proactive_config._override_conn

    class _Recorder:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, *args, **kwargs):
            executed.append(str(sql).strip().split()[0].upper() if str(sql).strip() else "")
            return self._conn.execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        proactive_config, "_override_conn", lambda: _Recorder(real_conn()), raising=False,
    )

    proactive_config.write_proactive_config(user_key=_KEYS_A, paused=True)
    assert "BEGIN" in executed, "读改写没有开显式写事务"
    assert executed.index("BEGIN") < executed.index("INSERT")
    assert proactive_config.read_override(_KEYS_A) == {"paused": True}


def test_override_write_failure_rolls_back_whole_transaction(clean_store, monkeypatch):
    """事务内写失败必须整体回滚（不留半份覆盖行）。"""
    import sqlite3

    from utils import proactive_config

    real_write = proactive_config._write_override_conn

    def _write_then_fail(conn, user_key, data):
        real_write(conn, user_key, data)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        proactive_config, "_write_override_conn", _write_then_fail, raising=False,
    )
    with pytest.raises(sqlite3.OperationalError):
        proactive_config.write_proactive_config(user_key=_KEYS_A, paused=True)

    # 只还原本用例打的补丁（不能用 monkeypatch.undo()：会连带撤销 conftest 的
    # 运行时文件沙箱，后续读会打到宿主真实 data/sqlite.db）。
    monkeypatch.setattr(proactive_config, "_write_override_conn", real_write, raising=False)
    assert proactive_config.read_override(_KEYS_A) == {}, "事务未回滚（写了半份）"


def test_write_partial_merge_and_clear(clean_store):
    """partial merge 只改传入键；非引擎键清覆盖 = 直接删行（幂等 noop）。"""
    from utils import proactive_config

    proactive_config.write_proactive_config(user_key=_KEYS_A, paused=True)
    proactive_config.write_proactive_config(user_key=_KEYS_A, enabled=False)

    raw = proactive_config.read_override(_KEYS_A)
    assert raw == {"paused": True, "enabled": False}

    assert proactive_config.clear_override(_KEYS_A) is True
    assert proactive_config.clear_override(_KEYS_A) is False
    assert proactive_config.read_override(_KEYS_A) == {}
    assert proactive_config.has_override(_KEYS_A) is False


def test_clear_override_with_dirty_engine_row_still_arms_tombstone(clean_store):
    """引擎键值已损坏（sanitize 会丢弃）时，clear 仍必须留墓碑。

    复核缺陷（2026-10-30 low）：旧实现按 ``_sanitize_override`` 的结果算引擎键，
    坏值被丢弃 → 直接删行、不写墓碑 → DELETE 回 noop，而引擎此前确按旧的有效
    值写过个人值，永远不回落到全局。
    """
    from utils import proactive_config

    _write_override_row(_KEYS_A, {"threshold": 999})  # 越界 → sanitize 丢弃
    assert proactive_config.read_override(_KEYS_A) == {}

    assert proactive_config.clear_override(_KEYS_A) is True
    assert proactive_config.pending_revert_keys(_KEYS_A) == ["threshold"]


def test_clear_revert_marker_keeps_new_overrides_and_concurrent_tombstones(clean_store):
    """摘墓碑只摘「本次已回滚」的键：新写入的覆盖与并发新墓碑都保留。"""
    from utils import proactive_config

    proactive_config.write_proactive_config(user_key=_KEYS_A, threshold=9.0)
    proactive_config.clear_override(_KEYS_A)
    proactive_config.write_proactive_config(user_key=_KEYS_A, paused=True)

    assert proactive_config.clear_revert_marker(_KEYS_A, {"threshold"}) is True
    assert proactive_config.read_override(_KEYS_A) == {"paused": True}
    assert proactive_config.pending_revert_keys(_KEYS_A) == []

    # 并发场景：又冒出一条新墓碑（min_interval），用旧的 expected 集不该吞掉它
    proactive_config.write_proactive_config(user_key=_KEYS_A, min_interval_minutes=5)
    proactive_config.clear_override(_KEYS_A)
    assert proactive_config.pending_revert_keys(_KEYS_A) == ["min_interval_minutes"]
    assert proactive_config.clear_revert_marker(_KEYS_A, {"threshold"}) is True
    # 旧 expected 集（threshold）不得吞掉新墓碑（min_interval）
    assert proactive_config.pending_revert_keys(_KEYS_A) == ["min_interval_minutes"]
    # clear_override 的语义是「清掉全部覆盖」：paused 也随之上一条清空
    assert proactive_config.read_override(_KEYS_A) == {}


def test_clear_engine_override_leaves_revert_tombstone(clean_store):
    """引擎级覆盖被清 → 墓碑行（含待回滚键），读面已回全局。

    墓碑是「引擎还按个人值跑」的唯一跨重启证据：scheduler 靠它把引擎还原成
    全局值后再删行（见 ``test_clear_override_reverts_engine_to_global``）。
    """
    from utils import proactive_config

    proactive_config.write_proactive_config(
        user_key=_KEYS_A, threshold=9.0, min_interval_minutes=90,
    )
    assert proactive_config.clear_override(_KEYS_A) is True

    # 读面：已经是全局值（墓碑不算「有覆盖」）
    assert proactive_config.read_override(_KEYS_A) == {}
    assert proactive_config.has_override(_KEYS_A) is False
    assert proactive_config.effective(_KEYS_A)["threshold"] == 2.0
    # 引擎回滚清单仍在
    assert sorted(proactive_config.pending_revert_keys(_KEYS_A)) == [
        "min_interval_minutes", "threshold",
    ]
    # 墓碑未清前再点一次 DELETE 仍算「有待办」（不是空 noop）
    assert proactive_config.clear_override(_KEYS_A) is True
    assert proactive_config.purge_override_row(_KEYS_A) is True
    assert proactive_config.pending_revert_keys(_KEYS_A) == []
    assert proactive_config.clear_override(_KEYS_A) is False


def test_revert_marker_keeps_overrides_written_after_delete(clean_store):
    """DELETE 后立刻重新设置：回滚只摘墓碑，不得删掉新覆盖（复核 high）。

    旧实现回滚完成即 ``purge`` 整行 → 「先取消、再把 paused 打开」的设置被
    一起抹掉，UI 行消失。此处钉住：墓碑与 active 覆盖共存，摘标记后覆盖仍在。
    """
    from utils import proactive_config

    proactive_config.write_proactive_config(user_key=_KEYS_A, threshold=9.0)
    assert proactive_config.clear_override(_KEYS_A) is True        # → 墓碑
    proactive_config.write_proactive_config(user_key=_KEYS_A, paused=True)

    # 两个东西同时存在：待回滚的引擎键 + 刚写入的 paused
    assert proactive_config.read_override(_KEYS_A) == {"paused": True}
    assert proactive_config.pending_revert_keys(_KEYS_A) == ["threshold"]
    assert proactive_config.has_override(_KEYS_A) is True

    assert proactive_config.clear_revert_marker(_KEYS_A) is True
    assert proactive_config.read_override(_KEYS_A) == {"paused": True}, "新覆盖被回滚抹掉了"
    assert proactive_config.pending_revert_keys(_KEYS_A) == []
    # 再摘一次 → noop
    assert proactive_config.clear_revert_marker(_KEYS_A) is False


# ═══════════════════════════════════════════════════════════
# 2. 端点隔离（真实 app + 真实 JWT 门）
# ═══════════════════════════════════════════════════════════


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


async def test_get_returns_effective_global_and_flags(api):
    client, _engine, _app = api
    r = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["effective"] == _DEFAULTS
    assert body["global_default"] == _DEFAULTS
    assert body["overridden_keys"] == []
    assert body["scope"] == "global"
    assert body["user_key_present"] is True


async def test_post_user_override_does_not_leak_to_other_user(api):
    """A 写覆盖后：A 生效、B 完全不变（隔离的核心断言）。"""
    client, _engine, _app = api
    r = await client.post(
        "/api/user/proactive-config",
        headers=_bearer(_USER_A),
        json={"paused": True, "min_interval_minutes": 45},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user"
    assert sorted(body["overridden_keys"]) == ["min_interval_minutes", "paused"]
    assert body["effective"]["paused"] is True
    assert body["effective"]["min_interval_minutes"] == 45
    assert body["global_default"]["paused"] is False

    other = await client.get("/api/user/proactive-config", headers=_bearer(_USER_B))
    assert other.status_code == 200, other.text
    assert other.json()["effective"]["paused"] is False
    assert other.json()["effective"]["min_interval_minutes"] == 30
    assert other.json()["overridden_keys"] == []
    assert other.json()["scope"] == "global"


async def test_post_ignores_client_reported_user_key(api):
    """请求体自报 ``user_key`` 一律忽略：只能改自己，改不到别人。"""
    from utils import proactive_config

    client, _engine, _app = api
    r = await client.post(
        "/api/user/proactive-config",
        headers=_bearer(_USER_A),
        json={"paused": True, "user_key": _KEYS_B},
    )
    assert r.status_code == 200, r.text

    assert proactive_config.read_override(_KEYS_B) == {}, "自报归属生效了（跨用户写入）"
    assert proactive_config.read_override(_KEYS_A) == {"paused": True}


async def test_pause_endpoint_only_touches_paused(api):
    from utils import proactive_config

    client, _engine, _app = api
    await client.post(
        "/api/user/proactive-config",
        headers=_bearer(_USER_A),
        json={"min_interval_minutes": 15},
    )
    r = await client.post(
        "/api/user/proactive/pause", headers=_bearer(_USER_A), json={"paused": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True
    assert proactive_config.read_override(_KEYS_A) == {
        "min_interval_minutes": 15, "paused": True,
    }

    r2 = await client.post(
        "/api/user/proactive/pause", headers=_bearer(_USER_A), json={"paused": False},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["effective"]["paused"] is False
    assert proactive_config.read_override(_KEYS_A)["min_interval_minutes"] == 15


async def test_delete_clears_only_own_override_and_is_idempotent(api):
    client, _engine, _app = api
    await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_A), json={"paused": True},
    )
    await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_B), json={"enabled": False},
    )

    r = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["scope"] == "global"

    again = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert again.json()["status"] == "noop"

    b = await client.get("/api/user/proactive-config", headers=_bearer(_USER_B))
    assert b.json()["effective"]["enabled"] is False, "B 的覆盖被人删掉了（越权清除）"


async def test_delete_response_reports_bound_user_key(api):
    """R1-5：DELETE 回执必须带**实际** user_key（已绑定用户不得显示未绑定）。

    旧实现 ``_proactive_config_payload(None)`` → ``user_key_present=False``，
    前端把「恢复继承全局」后的正常回执误判成「账号未绑定微信」（假横幅，
    一次往返后才自愈）。
    """
    client, _engine, _app = api
    await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_A),
        json={"max_daily_messages": 3},
    )

    r = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["user_key_present"] is True, "DELETE 回执把已绑定用户报成未绑定"
    assert body["user_key"] == _KEYS_A
    assert body["scope"] == "global"

    # 一次往返后 GET 仍为 true（自愈不是修复的替代品）
    got = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert got.json()["user_key_present"] is True

    # 幂等 noop 路径（非引擎级覆盖：无墓碑，清完第二次即 noop）同样不得
    # 把已绑定用户报成未绑定。
    await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_B),
        json={"paused": True},
    )
    await client.delete("/api/user/proactive-config", headers=_bearer(_USER_B))
    again = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_B))
    assert again.json()["status"] == "noop"
    assert again.json()["user_key_present"] is True


async def test_delete_triggers_engine_reconcile_when_scheduler_present(api, monkeypatch):
    """DELETE 清引擎级覆盖后必须触发即时回滚（无调度器时由下一拍墓碑兜住）。"""
    from api.deps import deps

    class _Sched:
        def __init__(self):
            self.calls: list[str] = []

        def reconcile_user_engine(self, key: str) -> bool:
            self.calls.append(key)
            return True

    class _Orch:
        def __init__(self, sched):
            self._ase = None
            self.components = {"scheduler": sched}

    client, _engine, _app = api
    await client.post(
        "/api/user/proactive-config",
        headers=_bearer(_USER_A),
        json={"threshold": 9.0},
    )
    sched = _Sched()
    monkeypatch.setattr(deps, "orch", _Orch(sched), raising=False)

    r = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 200, r.text
    assert sched.calls == [_KEYS_A], "清引擎级覆盖后没有触发回滚"

    # A 的覆盖已清、B 不受影响
    a = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert a.json()["effective"]["threshold"] == 2.0
    assert a.json()["overridden_keys"] == []


async def test_delete_surfaces_db_failure_as_500(api, monkeypatch):
    """覆盖库不可用时 DELETE 必须 500（不得回 noop 冒充成功）。"""
    import sqlite3

    from utils import proactive_config

    def _boom():
        raise sqlite3.OperationalError("disk I/O error")

    client, _engine, _app = api
    monkeypatch.setattr(proactive_config, "_override_conn", _boom)
    r = await client.delete("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 500, r.text


async def test_unbound_user_all_endpoints_are_400(api):
    """未绑定微信 → 四条自助端点一律 400「不猜键」（域规格原文）。"""
    client, _engine, _app = api

    read = await client.get("/api/user/proactive-config", headers=_bearer(_USER_UNBOUND))
    assert read.status_code == 400, read.text

    post = await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_UNBOUND), json={"paused": True},
    )
    assert post.status_code == 400, post.text
    pause = await client.post(
        "/api/user/proactive/pause", headers=_bearer(_USER_UNBOUND), json={"paused": True},
    )
    assert pause.status_code == 400, pause.text
    delete = await client.delete(
        "/api/user/proactive-config", headers=_bearer(_USER_UNBOUND),
    )
    assert delete.status_code == 400, delete.text


async def test_endpoints_require_auth(api):
    client, _engine, _app = api
    for call in (
        client.get("/api/user/proactive-config"),
        client.post("/api/user/proactive-config", json={"paused": True}),
        client.post("/api/user/proactive/pause", json={"paused": True}),
        client.delete("/api/user/proactive-config"),
    ):
        r = await call
        assert r.status_code == 401, f"{r.request.method} {r.request.url} -> {r.status_code}"


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

    # 用户侧的覆盖不受影响（两条分支各自独立）
    mine = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert mine.json()["effective"]["threshold"] == 2.0  # 端点里没写覆盖，只测读面
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


async def test_user_get_global_default_reflects_admin_saved_hub_values(api, monkeypatch):
    """用户 GET 的 global_default/effective 必须含 admin 已保存的全局引擎值。

    复核 medium：旧实现只走 ``effective()``（部署默认 2.0/8），admin 已经保存
    的 5.5/6 看不见 —— 用户据此以为「跟随全局」会回到 2.0。
    """
    from api.deps import deps
    from proactive.ase_hub import ASEHub

    client, _engine, _app = api
    hub = ASEHub(_FakeEngine)
    hub.apply_runtime_config(threshold=5.5, max_daily_messages=6)
    monkeypatch.setattr(deps, "orch", _FakeOrchForAdmin(hub), raising=False)

    r = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["global_default"]["threshold"] == 5.5
    assert body["global_default"]["max_daily_messages"] == 6
    assert body["effective"]["threshold"] == 5.5
    assert body["effective"]["max_daily_messages"] == 6

    # 用户覆盖某键后：effective 用用户值，global_default 仍是 hub 全局值
    await client.post(
        "/api/user/proactive-config", headers=_bearer(_USER_A), json={"threshold": 9.0},
    )
    after = (await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))).json()
    assert after["effective"]["threshold"] == 9.0
    assert after["global_default"]["threshold"] == 5.5
    assert after["effective"]["cooldown_after_reply_minutes"] == 15  # 未覆盖 → 部署默认


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

    mine = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert mine.json()["global_default"]["threshold"] == 6.25
    assert mine.json()["global_default"]["max_daily_messages"] == 5


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
    worker 的用户 ``global_default`` / 墓碑回滚值会回落部署默认 —— 同一次 admin
    保存出现两种读数。
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

    # 用户 GET 的 global_default 也必须是同一次保存的值（非部署默认 2.0/8）
    mine = await client.get("/api/user/proactive-config", headers=_bearer(_USER_A))
    assert mine.status_code == 200, mine.text
    assert mine.json()["global_default"]["threshold"] == 6.25
    assert mine.json()["global_default"]["max_daily_messages"] == 5
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
    from utils import proactive_config

    sched, hub, keys, events, spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], paused=True)

    _tick(sched)

    paused_events = [e for e in events if e.get("reason") == "paused"]
    assert [e["session_key"] for e in paused_events] == [keys[0]]
    # 关键：不是「全站暂停」——全局 _paused 仍为 False，且 B 走到了 LLM 决策
    assert sched.is_paused() is False
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]
    # 被暂停的用户不产生任何引擎 tick/投递
    assert hub.get(keys[0]).tick_calls == []


def test_per_user_disabled_only_blocks_that_user(scope_sched):
    from utils import proactive_config

    sched, _hub, keys, events, spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], enabled=False)

    _tick(sched)

    assert [e["session_key"] for e in events if e.get("reason") == "web_disabled"] == [keys[0]]
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]


def test_per_user_quiet_hours_use_own_window(scope_sched):
    """per-user 静默时段 = 覆盖了哪一端用哪一端，另一端沿用全局时段；
    且决策上下文里的 ``quiet_hours`` 必须是同一份窗口（否则上下文说「别打扰」
    而闸放行 = 自相矛盾）。"""
    from proactive.ase_engine import _local_now
    from utils import proactive_config

    sched, _hub, keys, events, spy = scope_sched
    hour = _local_now().hour
    # 全局时段明显不含当前小时（把「现在」排除在外）
    sched._quiet_hours = ((hour + 2) % 24, (hour + 3) % 24)
    # 只覆盖「开始」为当前小时、结束为下一小时 → 该用户进入静默
    proactive_config.write_proactive_config(
        user_key=keys[0],
        quiet_hours_start=hour,
        quiet_hours_end=(hour + 1) % 24,
    )

    _tick(sched)

    assert [e["session_key"] for e in events if e.get("reason") == "quiet_hours"] == [keys[0]]
    assert [c["session_key"] for c in spy.contexts] == [keys[1]]
    # 半对覆盖：未覆盖的「结束」沿用全局（不是回落到 7）
    start, end = sched._quiet_hours_for(proactive_config.effective(keys[0]))
    assert (start, end) == (hour, (hour + 1) % 24)
    assert sched._quiet_hours_for(proactive_config.effective(keys[1])) == (
        (hour + 2) % 24, (hour + 3) % 24,
    )
    # 走通的用户：上下文里的窗口 = 该会话的窗口（全局值，无覆盖）
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
    from utils import proactive_config

    sched, _hub, keys, events, _spy = scope_sched
    hour = _local_now().hour
    sched._quiet_hours = (hour, (hour + 1) % 24)          # 全局：现在就是静默
    per_user = ((hour + 6) % 24, (hour + 7) % 24)          # A：现在不静默
    proactive_config.write_proactive_config(
        user_key=keys[0],
        quiet_hours_start=per_user[0],
        quiet_hours_end=per_user[1],
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
        "决策链没把 per-user 静默窗口交给投递层（设置端到端不生效）"
    )
    # 未覆盖的用户：全局静默仍然生效，且投递层从未被调用
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
    """引擎级覆盖直调该用户引擎；``_replay`` 为空（未经 hub 扇出）；B 不受影响。"""
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(
        user_key=keys[0], threshold=9.0, max_daily_messages=3,
    )

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
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], threshold=9.0)

    _tick(sched)
    first = len(hub.get(keys[0]).apply_calls)
    _tick(sched)

    assert first == 1
    assert len(hub.get(keys[0]).apply_calls) == 1, "每 tick 重复下发会把该用户的日配额永远清零"


def test_clear_override_reverts_engine_to_global(scope_sched):
    """清除覆盖后，已加载引擎必须回落到**全局**值（复核 high 缺陷的回归）。"""
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(
        user_key=keys[0], threshold=9.0, min_interval_minutes=90,
    )
    _tick(sched)
    eng = hub.get(keys[0])
    assert (eng._urgency_threshold, eng._min_interval_minutes) == (9.0, 90)

    assert proactive_config.clear_override(keys[0]) is True
    # 墓碑在：读面已回全局，但引擎值还没动（等下一拍）
    assert eng._urgency_threshold == 9.0
    assert sorted(proactive_config.pending_revert_keys(keys[0])) == [
        "min_interval_minutes", "threshold",
    ]

    _tick(sched)

    assert (eng._urgency_threshold, eng._min_interval_minutes) == (2.0, 30), (
        "清除覆盖后引擎仍按个人值调度（DELETE 回执假成功）"
    )
    # 回滚完成 → 墓碑清除、行彻底消失
    assert proactive_config.pending_revert_keys(keys[0]) == []
    assert proactive_config.read_override(keys[0]) == {}
    with contextlib.closing(proactive_config._override_conn()) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {proactive_config.OVERRIDE_TABLE} WHERE user_key = ?",
            (keys[0],),
        ).fetchone()
    assert row is None, "回滚已完成但墓碑行没删（脏行）"


def test_delete_then_repause_reverts_engine_and_keeps_override(scope_sched):
    """DELETE → 重新 paused=True：引擎回滚到全局，paused 覆盖必须存活。

    复核 high：回滚若按整行 purge，会把刚写的 paused 删掉 —— 用户看到
    「已暂停」却不再生效（设置行消失），且引擎可能停在旧个人值。
    """
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(
        user_key=keys[0], threshold=9.0, max_daily_messages=3,
    )
    _tick(sched)
    eng = hub.get(keys[0])
    assert (eng._urgency_threshold, eng._max_daily_messages) == (9.0, 3)

    proactive_config.clear_override(keys[0])          # → 墓碑
    proactive_config.write_proactive_config(user_key=keys[0], paused=True)  # 新覆盖
    _tick(sched)

    assert (eng._urgency_threshold, eng._max_daily_messages) == (2.0, 8), "引擎没回滚"
    assert proactive_config.read_override(keys[0]) == {"paused": True}, (
        "回滚把 DELETE 后新写的 paused 一起删了"
    )
    assert proactive_config.has_override(keys[0]) is True
    assert proactive_config.pending_revert_keys(keys[0]) == []


def test_clear_override_revert_is_idempotent(scope_sched):
    """回滚后再跑一拍不得重复下发（重复下发 = 重建控制器、日配额归零）。"""
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], threshold=9.0)
    _tick(sched)
    proactive_config.clear_override(keys[0])
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
    """覆盖行被手改成非法值时，引擎里的旧个人值必须自动回落到全局。

    复核缺陷（2026-10-30 二轮 medium）：``read_override`` 会丢弃非法引擎键，
    而旧实现只在 ``overridden_keys``/墓碑非空时下发 —— 库行被改成
    ``threshold=999`` 后读面回落 2.0、引擎却仍停在 9.0，且没有墓碑可触发回滚。
    修法：目标值 = 用户值 else 全局值 → 丢弃该键后自然对齐全局。
    """
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], threshold=9.0)
    _tick(sched)
    eng = hub.get(keys[0])
    assert eng._urgency_threshold == 9.0

    # 手改坏（越界 → sanitize 丢弃）；引擎里仍是 9.0
    _write_override_row(keys[0], {"threshold": 999})
    assert proactive_config.read_override(keys[0]) == {}
    assert proactive_config.effective(keys[0])["threshold"] == 2.0

    _tick(sched)

    assert eng._urgency_threshold == 2.0, "非法覆盖行把引擎永远钉在旧个人值"
    # 另一用户不受影响
    assert hub.get(keys[1])._urgency_threshold == 2.0


def test_tombstone_race_stale_user_cfg_still_reverts(scope_sched):
    """DELETE 落在「读 user_cfg」与「下发」之间时，引擎仍须回滚到全局。

    复核缺陷（2026-10-30 medium）：旧实现 target 只看 ``user_cfg.overridden_keys``，
    陈旧的 user_cfg 会用旧个人值当目标 → payload 为空 → 还摘掉墓碑 = 墓碑没了、
    引擎停在旧值。修法：pending 遮蔽同名的 overridden 键。
    """
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], threshold=9.0)
    _tick(sched)
    eng = hub.get(keys[0])
    assert eng._urgency_threshold == 9.0

    stale_cfg = proactive_config.effective(keys[0])   # 决策链开头读到的旧快照
    assert "threshold" in stale_cfg.overridden_keys
    proactive_config.clear_override(keys[0])          # 竞态：DELETE 在两步之间落地

    # 用陈旧快照直接下发（模拟竞态窗口）——必须按墓碑回滚，而不是重放旧值
    sched._apply_user_engine_config(hub, eng, keys[0], stale_cfg)

    assert eng._urgency_threshold == 2.0, "陈旧 user_cfg 把旧个人值又写回去了"
    assert proactive_config.pending_revert_keys(keys[0]) == []


def test_reconcile_user_engine_is_immediate(scope_sched):
    """DELETE 端点的即时回滚入口（本 worker 持有引擎时不必等下一拍）。"""
    from utils import proactive_config

    sched, hub, keys, _events, _spy = scope_sched
    proactive_config.write_proactive_config(user_key=keys[0], threshold=9.0)
    _tick(sched)
    eng = hub.get(keys[0])
    proactive_config.clear_override(keys[0])

    assert sched.reconcile_user_engine(keys[0]) is True
    assert eng._urgency_threshold == 2.0
    assert proactive_config.pending_revert_keys(keys[0]) == []


def test_no_override_means_zero_engine_writes(scope_sched):
    """没有任何覆盖 → 引擎一次都不写（既有全局行为逐字节不变）。"""
    sched, hub, keys, _events, spy = scope_sched

    _tick(sched)

    assert hub.get(keys[0]).apply_calls == []
    assert hub.get(keys[1]).apply_calls == []
    assert [c["session_key"] for c in spy.contexts] == keys


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

    proactive_config.write_proactive_config(
        user_key=_KEYS_A, max_daily_messages=1,
    )
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    globals_ = proactive_config.global_engine_values()
    sched._apply_user_engine_config(
        None, eng, _KEYS_A, proactive_config.effective(_KEYS_A),
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
        None, eng, _KEYS_A, proactive_config.effective(_KEYS_A),
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


# ═══════════════════════════════════════════════════════════
# 4. 删号 purge（隐私红线）
# ═══════════════════════════════════════════════════════════


def test_purge_owner_data_removes_proactive_override(tmp_path, monkeypatch):
    """``structured_memory.purge_owner_data`` 必须清掉本表行（删号不留个人调度）。"""
    from shisi.memory.legacy.structured_memory import StructuredMemory
    from utils import proactive_config

    db_path = tmp_path / "purge.db"
    monkeypatch.setattr(proactive_config, "_OVERRIDE_DB_PATH", db_path, raising=False)
    memory = StructuredMemory(str(db_path))
    try:
        proactive_config.write_proactive_config(user_key="4:peer_a", paused=True)
        assert proactive_config.read_override("4:peer_a") == {"paused": True}

        counts = memory.purge_owner_data(4, session_keys=["4:peer_a"])
        assert counts.get("user_proactive_config") == 1, counts
        assert proactive_config.read_override("4:peer_a") == {}
        # count 面同口径（verify 依赖它）
        assert memory.count_owner_rows(4, session_keys=["4:peer_a"]).get(
            "user_proactive_config", 0
        ) == 0
    finally:
        memory.close()
