"""主动调度 per-user 覆盖（三级读取：用户覆盖 > 全局 > 默认）—— 读写唯一真源。

为什么需要（2026-10-30 D20 用户主权裁决「用户需要能够自己进行设置，而不是
全站统一」）：主动搭话的开关 / 暂停 / 免打扰时段 / 频率参数此前只有
``/api/proactive/*`` 那条 **admin 全局** 通道（``data/scheduler_config.json`` +
``ASEHub.apply_runtime_config``），普通注册用户既没有入口、也无法只影响自己。
本模块把全局真源**降为默认值**，在其上叠一层 per-user 覆盖：

    用户覆盖（``user_proactive_config`` 表）→ 全局 → 静态默认

对齐既有唯一模板 ``utils/humanize_config.py``（声明式字段表 / 覆盖表 /
三级合并 / 幂等建表 / 每次热读无缓存）与 ``utils/reply_mode.py``
（``_OVERRIDE_DB_PATH`` 模块常量 + 同库同隔离域）。**不新开第二个真源**：
覆盖表与 ``user_profile`` / ``user_facts`` / ``user_humanize`` /
``user_reply_mode`` 同库（``data/sqlite.db``），键形态同为完整会话键
``N:wxid``（v1.28 隔离域）。本模块**从不写全局文件**（admin 端点
``/api/proactive/config`` 是全局面唯一 owner，原样保留）。

全局值的真源分三处（2026-10-30 复核修正：此前硬编码 4.0/10 与生产不符）：

- ``enabled`` ← ``data/scheduler_config.json`` 的 ``llm_proactive.enabled``
- ``paused`` ← 同文件 ``proactive.paused``
- ``quiet_hours_start/end`` ← 同文件 ``quiet_hours.{start,end}``（缺省 23/7）
- ``threshold`` / ``max_daily_messages`` / ``min_interval_minutes`` /
  ``cooldown_after_reply_minutes`` ← ``config/system.yaml`` 的 ``proactive`` 块
  （与 ``orchestrator/_init_mixin.py`` 构造 ``ASEEngine`` 用的是同一份
  ``cfg.proactive``），按 ``ConfigManager._effective_data`` **同一顺序**叠加：
  ``system.yaml`` → ``system_<env>.yaml`` → ``${ENV}`` 解析 → ``AI_GF_*`` 点号
  环境覆盖（三者复用 ConfigManager 的实现，不复制语法）；yaml 缺键/缺失时回落
  ``observability.config_models.ProactiveConfig`` 的模型默认
  （threshold=2.0 / daily=8 / interval=30 / cooldown=15）。
- 运行时若 admin 已保存过全局值，``scheduler_config.json`` 的
  ``proactive_runtime`` 块（跨 worker 持久真源）逐键**压过**本进程
  ``ASEHub._replay``（见 :func:`global_engine_values`）：4 worker 下只有接到那次
  POST 的 worker 会更新 ``_replay``，其余 worker 的 replay 是旧值 —— 让 replay
  优先会让 admin GET / 用户 ``global_default`` / 墓碑回滚读到旧全局值
  （2026-10-30 二轮复核）。``ASEHub.get_runtime_config()`` 是「第一个已加载
  引擎」视图，会被 per-user 值污染，**不得**当作全局真值。

**不做 user_key × character_id 双维**：主动消息按会话（=人）调度，
``_llm_proactive_one_user`` 的读取点只有会话键，per-character 维度没有任何
消费方（registry 登记 deferred）。

首批可覆盖键 = **运行时真有 per-user 读取点** 的八个：``enabled`` / ``paused``
（决策前置闸）、``quiet_hours_start/end``（决策闸 + 上下文 + judge + 投递）、
``threshold`` / ``max_daily_messages`` / ``min_interval_minutes`` /
``cooldown_after_reply_minutes``（引擎实例字段，直调该用户的引擎）。
``llm_proactive_*`` 提示词类字段与 ``dice_gate`` 不在首批（前者是全局风格，
后者是引擎级概率闸）→ deferred。

🔴 引擎级下发的硬约束（值在本模块，下发纪律在 scheduler）：per-user 值
**不得**经 ``ASEHub.apply_runtime_config`` —— 它会写 ``_replay["runtime_config"]``
并扇出到**全部**引擎（``proactive/ase_hub.py:235-241``），既跨用户泄漏、又会被
``_apply_replay`` 传染给新创建的引擎。必须对该用户的引擎直调
``eng.apply_runtime_config(...)``。

清除后的回滚（2026-10-30 复核补齐）：``DELETE /api/user/proactive-config``
若该用户覆盖过**引擎级**键，不能只删行——已写进引擎（且被
``ASEEngine.save_state`` 持久化、重启后 ``_load_state`` 恢复）的个人值会留在
原地，用户看到「已回到全局」却仍按个人值调度。故 :func:`clear_override`
写入一条**墓碑行**（``config = {"__revert__": [待回滚的引擎键]}``），
scheduler 在下一拍按它把该用户引擎还原成全局值后再 :func:`purge_override_row`
删行。墓碑行不算「有覆盖」（:func:`has_override` 只看有效覆盖键），
API 读面照常显示 scope=global。

并发口径（照 ``utils/humanize_config``）：读侧**每次热读、无进程缓存**；
覆盖行写入只经 :func:`write_proactive_config`（单行 upsert，不碰全局文件）；
覆盖库不可用 / 行内脏值 → **逐字段回落全局**，绝不抛异常炸主链；写/清失败
**上抛**（调用方必须知道没生效，不得回执假成功）。三处「读 → 改 → 写」
（:func:`write_proactive_config` / :func:`clear_override` /
:func:`clear_revert_marker`）全程在同一个 ``BEGIN IMMEDIATE`` 事务内
（:func:`_override_txn`）：4 worker 并发 DELETE×POST 不会用陈旧快照互相覆盖。
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from observability.config_models import ProactiveConfig
from utils import json_state

logger = logging.getLogger("proactive_config")

#: 全局运行时真源（只读！admin 的 ``/api/proactive/config`` 是写入方）。
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"

#: 引擎级默认值的部署真源（与 ``_init_mixin`` 构造引擎用的 ``cfg.proactive`` 同源）。
_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "system.yaml"

#: per-user 覆盖库：与 ``user_profile`` / ``user_facts`` / ``user_humanize`` /
#: ``user_reply_mode`` **同库同隔离域**（``data/sqlite.db``）。模块常量便于
#: 测试隔离（``tests/conftest.py`` 把真实库重定向到沙箱）。
_OVERRIDE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

#: 覆盖表名（删号 purge / count / verify 的清单都引用它，防三处字面量漂移）。
OVERRIDE_TABLE = "user_proactive_config"

#: 墓碑行的内部键：待回滚的引擎级键清单（不是用户可写字段，读侧 sanitize 丢弃）。
_REVERT_KEY = "__revert__"

#: 全局文件内的默认值来源（与 scheduler 读的是同一批键，不另立真源口径）。
_GLOBAL_ENABLED_BLOCK = "llm_proactive"
_GLOBAL_PAUSED_BLOCK = "proactive"
_GLOBAL_QUIET_BLOCK = "quiet_hours"
#: admin 经 ``/api/proactive/config`` 保存的**引擎级全局值**（跨 worker 持久真源）。
#: 只在本进程 ``ASEHub._replay`` 里记是不够的：4 worker 中只有接到那次 POST 的
#: worker 有 replay，其余 worker 的用户 GET/墓碑回滚会回落部署默认 → 同一次
#: 保存在不同 worker 上读数不一致（2026-10-30 复核）。
GLOBAL_RUNTIME_BLOCK = "proactive_runtime"
_FALLBACK_QUIET_HOURS = (23, 7)

#: 模型默认（yaml 缺键时的兜底）—— 与 ``_init_mixin`` 构造引擎的
#: ``cfg.proactive`` 默认完全一致；**不是**随手写的字面量。
_MODEL_DEFAULTS = ProactiveConfig()

#: 模块键 → ``config/system.yaml`` 的 ``proactive`` 键名（两处命名不同：
#: yaml/模型用 ``urgency_threshold``，引擎 kwarg 与 ``get_runtime_config`` 用
#: ``threshold``）。漏了这层映射 → yaml 永远读不到阈值、静默回落默认
#: （2026-10-30 突变验证当场抓到）。
_SYSTEM_KEY = {"threshold": "urgency_threshold"}


@dataclass(frozen=True)
class ProactiveField:
    """声明式字段表的一行（单一字段表，禁止多处字面量分叉）。"""

    name: str
    type: str                        # "bool" | "int" | "float"
    default: Any
    bounds: tuple[float, float] | None = None
    label: str = ""
    engine_key: str = ""             # 非空 = 引擎级字段（apply_runtime_config 同名 kwarg）


#: 顺序即落盘键序（新增键追加在末尾，保持行内 JSON 形态稳定）。
FIELDS: tuple[ProactiveField, ...] = (
    ProactiveField("enabled", "bool", True, None, "主动消息总开关"),
    ProactiveField("paused", "bool", False, None, "暂停主动消息"),
    ProactiveField("quiet_hours_start", "int", _FALLBACK_QUIET_HOURS[0], (0, 23), "免打扰开始小时"),
    ProactiveField("quiet_hours_end", "int", _FALLBACK_QUIET_HOURS[1], (0, 23), "免打扰结束小时"),
    ProactiveField(
        "threshold", "float", float(_MODEL_DEFAULTS.urgency_threshold),
        (0.0, 20.0), "紧迫度阈值", engine_key="threshold",
    ),
    ProactiveField(
        "max_daily_messages", "int", int(_MODEL_DEFAULTS.max_daily_messages),
        (1, 200), "每日主动上限", engine_key="max_daily_messages",
    ),
    ProactiveField(
        "min_interval_minutes", "int", int(_MODEL_DEFAULTS.min_interval_minutes),
        (0, 1440), "两次主动最小间隔(分钟)", engine_key="min_interval_minutes",
    ),
    ProactiveField(
        "cooldown_after_reply_minutes", "int", int(_MODEL_DEFAULTS.cooldown_after_reply_minutes),
        (0, 1440), "用户说话后冷却(分钟)", engine_key="cooldown_after_reply_minutes",
    ),
)

_FIELD_BY_NAME: dict[str, ProactiveField] = {f.name: f for f in FIELDS}

#: 静态默认值（全局文件/system.yaml 都缺席时使用）。
DEFAULTS: dict[str, Any] = {f.name: f.default for f in FIELDS}

#: 全部可覆盖键（顺序同字段表）。
FIELDS_ALL: tuple[str, ...] = tuple(f.name for f in FIELDS)

#: 引擎级键 → 引擎下发时用的 kwarg 名（与 ``get_runtime_config`` 同名字段）。
ENGINE_KEYS: tuple[str, ...] = tuple(f.name for f in FIELDS if f.engine_key)

#: 引擎级键里会触发 ``FrequencyController`` 重建的三个（下发必须同批）。
FREQUENCY_KEYS: frozenset[str] = frozenset(
    {"max_daily_messages", "min_interval_minutes", "cooldown_after_reply_minutes"}
)

#: 进/出闸键（调度器前置闸读它们，不涉及引擎）。
GATE_KEYS: tuple[str, ...] = ("enabled", "paused")
QUIET_KEYS: tuple[str, ...] = ("quiet_hours_start", "quiet_hours_end")


@dataclass(frozen=True)
class EffectiveConfig:
    """三级合并后的生效值 + 该用户真正覆盖了哪些键。

    ``overridden_keys`` 是 scheduler 的「per-user 优先、否则全局」判据：
    静默时段只有被覆盖的那一端才使用用户值，另一端仍用调度器内存里的
    全局时段（``self._quiet_hours``）—— 与人工设置半对时段的直觉一致。
    """

    values: dict[str, Any]
    overridden_keys: frozenset[str]
    global_values: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        """投影为 API/前端可消费的平铺字典（键序同字段表）。"""
        return {key: self.values[key] for key in FIELDS_ALL}


# ── 全局真源读取 ──────────────────────────────────────────────
#
# **每次热读、无进程缓存**（2026-10-30 复核）：先前按 (path, mtime_ns, size) 缓存
# 有两个缺陷——文件被替换但 size/mtime 未变会读到陈旧值；且绕过
# ``ConfigManager`` 的 ``${ENV}`` 解析，部署真源与运行时 ``cfg.proactive`` 可能
# 不一致。现每次解析，并复用 ``observability.config_manager._resolve_env_vars``
# 这一既有 env 解析单一 owner（不复制一套占位符语法 = 不制造第二 owner）。


def _system_proactive_block() -> dict[str, Any]:
    """``config/system.yaml`` 的 ``proactive`` 块（缺失/损坏 → 空 dict）。

    为了「部署真源 == 运行时 ``cfg.proactive``」，本函数按
    ``ConfigManager._effective_data`` 的**同一顺序**处理（2026-10-30 二轮复核）：

    1. ``config/system_<env>.yaml`` 的 ``proactive`` 块浅合并在基底之上
       （文件不存在 = 无操作，当前仓库无该文件）；
    2. ``${VAR}`` / ``${VAR:-default}`` 用 ``ConfigManager`` 的同一解析器；
    3. 点号路径环境变量覆盖（``AI_GF_*``）用 ``ConfigManager`` 的同一实现
       ``_apply_dot_env_overrides``。

    ⚠️ 已知口径（与运行时**一致**，不是本模块的缺口）：``_apply_dot_env_overrides``
    把 ``AI_GF_PROACTIVE_URGENCY_THRESHOLD`` 拆成嵌套路径
    ``proactive.urgency.threshold``，而 pydantic 模型按扁平键
    ``proactive.urgency_threshold`` 读、忽略未知嵌套键 —— 因此该环境变量在
    **运行时 ``cfg.proactive`` 与这里都不生效**（两边同为部署默认）。要改阈值
    请用 ``AI_GF_PROACTIVE`` 之外的正规途径（yaml / admin 端点）。
    """
    path = Path(_SYSTEM_CONFIG_PATH)
    try:
        import yaml  # 依赖已随 config_manager 引入（PyYAML）

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 config/system.yaml 失败（用模型默认）: %s", e)
        return {}
    if not isinstance(raw, dict):
        return {}

    # ① 环境专用覆盖（system_<env>.yaml）——与 ConfigManager._effective_data 同序
    try:
        env_name = os.environ.get("AI_GF_ENV", str(raw.get("env", "dev")))
        env_path = path.with_name(f"system_{env_name}.yaml")
        if env_path.exists():
            extra = yaml.safe_load(env_path.read_text(encoding="utf-8")) or {}
            extra_block = extra.get("proactive") if isinstance(extra, dict) else None
            if isinstance(extra_block, dict):
                base_block = raw.get("proactive")
                merged = dict(base_block) if isinstance(base_block, dict) else {}
                merged.update(extra_block)
                raw["proactive"] = merged
        elif env_name != str(raw.get("env", "dev")):
            # 与 ConfigManager._read_yaml 同口径：环境专用文件缺席 = 静默无操作。
            # 必须 debug（本函数每拍每 worker 都会调用，WARNING 会刷屏）。
            logger.debug(
                "AI_GF_ENV=%s 但 %s 不存在：proactive 全局值按 base yaml 计",
                env_name, env_path.name,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("环境专用 yaml 合并失败（用 base yaml）: %s", e)

    # ② + ③ 与 ConfigManager 共用的解析/覆盖实现（不复制占位符语法与环境变量拆分逻辑）
    try:
        from observability.config_manager import _apply_dot_env_overrides, _resolve_env_vars

        resolved = _resolve_env_vars(raw)
        if isinstance(resolved, dict):
            raw = resolved
        _apply_dot_env_overrides(raw)
    except Exception as e:  # noqa: BLE001
        logger.debug("${ENV} / 点号环境变量覆盖失败（用原值）: %s", e)

    block = raw.get("proactive")
    return dict(block) if isinstance(block, dict) else {}


def _coerce_engine_value(field: ProactiveField, value: Any) -> Any | None:
    """把**外部来源**（yaml / 持久块 / hub replay）的引擎级值归一；非法 → ``None``。

    容错范围与**引擎自身**一致，而不是套用写侧字段表的上界：

    - 数字字符串接受（``${VAR}`` 解析后是字符串；``ConfigManager``→pydantic
      路径同样把 ``"3.75"`` 收成 float）；
    - ``threshold`` / ``min_interval_minutes`` / ``cooldown_after_reply_minutes``
      只做**下限 0** 钳制（``ASEEngine.apply_runtime_config`` 的既有行为）；
    - ``max_daily_messages`` 原样保留（引擎不钳制）；
    - 非有限数字（``inf`` / ``nan``，含 yaml ``.inf`` 与 JSON ``Infinity``）
      一律视为非法 → ``None``（单字段回落，不炸整条主动链）。

    🔴 不套 ``FIELDS[*].bounds``（0-20 / 1-200 / 0-1440）：admin 端点
    ``/api/proactive/config`` 的 ``ProactiveConfigRequest`` 从来没有 ge/le，
    旧契约接受 ``threshold=25`` / ``max_daily=0``；读侧若按写侧上界丢弃，
    就会「保存成功、读回部署默认」= admin 面契约回退（2026-10-30 二轮复核）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            value = int(text) if field.type == "int" else float(text)
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    # 🔴 2026-10-31 R1-1：非有限值防御。yaml 的 `.inf/.nan`、JSON 的
    # `Infinity/NaN`、hub replay 里的脏值都会走到这里；`int(inf)` 抛
    # OverflowError、`int(nan)` 抛 ValueError，异常沿调用链被
    # `_check_ase` 吞成「ASE check failed」→ **全线主动消息停跳**。
    # 非有限值一律视为非法 → 逐字段回落全局/默认（与既有读侧容错语义一致）。
    # 只对 float 调 isfinite：超大 int 转 float 会 OverflowError，而 Python
    # int 本身天然有限。
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if field.name == "max_daily_messages":
            return int(value)
        if field.type == "float":
            return max(0.0, float(value))
        return max(0, int(value))
    except (OverflowError, ValueError, TypeError):
        # 兜底：字符串解析之外的极端数值（如 1e308 以上的超大浮点转 int）
        # 也不得让单个脏值炸掉整条主动链。
        return None


def _persisted_engine_values() -> dict[str, Any]:
    """``scheduler_config.json`` 的 ``proactive_runtime`` 块里**真的存在**的引擎键。

    只返回文件中出现的键（已归一）；文件缺失/损坏/块类型不符 → 空 dict。
    单独成函数是为了让 :func:`global_config` 与 :func:`global_engine_values`
    用同一份「持久真值」，并让后者能判断「该键是否已由持久文件定义」。
    """
    try:
        raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    except Exception as e:  # noqa: BLE001
        logger.debug("读取持久化引擎全局值失败: %s", e)
        return {}
    block = raw.get(GLOBAL_RUNTIME_BLOCK) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ENGINE_KEYS:
        if key not in block:
            continue
        coerced = _coerce_engine_value(_FIELD_BY_NAME[key], block[key])
        if coerced is not None:
            out[key] = coerced
    return out


def global_config() -> dict[str, Any]:
    """全局默认值 = 静态默认 → ``system.yaml`` 引擎参数 → ``scheduler_config.json``。

    只读、容错：任一来源缺失/损坏/类型不符 → 该字段回落上一层，不抛异常。
    """
    values: dict[str, Any] = dict(DEFAULTS)

    block = _system_proactive_block()
    for key in ENGINE_KEYS:
        source = _SYSTEM_KEY.get(key, key)
        if source not in block:
            continue
        coerced = _coerce_engine_value(_FIELD_BY_NAME[key], block[source])
        if coerced is not None:
            values[key] = coerced

    values.update(_persisted_engine_values())

    try:
        raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    except Exception as e:  # noqa: BLE001
        logger.debug("读取全局主动调度配置失败（用静态默认）: %s", e)
        return values
    if not isinstance(raw, dict):
        return values

    llm_block = raw.get(_GLOBAL_ENABLED_BLOCK)
    if isinstance(llm_block, dict) and isinstance(llm_block.get("enabled"), bool):
        values["enabled"] = llm_block["enabled"]

    paused_block = raw.get(_GLOBAL_PAUSED_BLOCK)
    if isinstance(paused_block, dict) and isinstance(paused_block.get("paused"), bool):
        values["paused"] = paused_block["paused"]

    quiet_block = raw.get(_GLOBAL_QUIET_BLOCK)
    if isinstance(quiet_block, dict):
        start = _coerce_hour(quiet_block.get("start"))
        end = _coerce_hour(quiet_block.get("end"))
        if start is not None:
            values["quiet_hours_start"] = start
        if end is not None:
            values["quiet_hours_end"] = end
    return values


def write_engine_globals(**partial: Any) -> dict[str, Any]:
    """把 admin 保存的引擎级全局值**持久化**到 ``scheduler_config.json``。

    跨 worker 真源（4 worker 共读，写入即对所有 worker 生效，无需重启）：
    ``ASEHub._replay`` 只是**本进程**记账，别的 worker 读不到 —— 只写 replay
    会让「admin 保存的全局值」在不同 worker 上出现两种读数（2026-10-30 复核）。

    只触碰 ``proactive_runtime`` 块（partial merge，``None`` 视同未提供），
    其余顶层键（llm_proactive / quiet_hours / reply_mode / humanize / dice_gate /
    topic_state …）逐字保留；经 ``utils.json_state.update_json`` 的锁内读改写。

    Raises:
        ValueError: 未知键 / 非引擎级键 / 非数字值（**不落盘**）。
        OSError: 写盘失败上抛（调用方必须知道没生效）。

    🔴 约束口径（2026-10-30 二轮复核）：本函数是 **admin 全局面**的持久化出口，
    必须沿用 ``ProactiveConfigRequest`` 的旧契约（无 ge/le，引擎只做下限钳制）——
    **不套**用户自助字段表的 0-20 / 1-200 / 0-1440 上界，否则 ``threshold=25`` /
    ``max_daily=0`` 这类旧行为可接受的保存会变成 400（admin API 契约回退）。
    归一规则见 :func:`_coerce_engine_value`（与 ``ASEEngine.apply_runtime_config``
    同口径）。写侧的用户自助路径仍走 :func:`validate_partial` 的严格上界。
    """
    payload: dict[str, Any] = {}
    for key, value in partial.items():
        if value is None:
            continue
        field = _FIELD_BY_NAME.get(key)
        if field is None or key not in ENGINE_KEYS:
            raise ValueError(
                f"未知的引擎级全局字段: {key!r}；可写字段为 {list(ENGINE_KEYS)}"
            )
        coerced = _coerce_engine_value(field, value)
        if coerced is None:
            raise ValueError(f"{key} 取值非法: {value!r}（需要数字）")
        payload[key] = coerced
    if payload:
        def _mutate(data: dict[str, Any]) -> None:
            block = data.get(GLOBAL_RUNTIME_BLOCK)
            if not isinstance(block, dict):
                block = {}
            block.update(payload)
            data[GLOBAL_RUNTIME_BLOCK] = block

        json_state.update_json(_CONFIG_PATH, _mutate)
    return global_engine_values()


def global_engine_values(replay: dict[str, Any] | None = None) -> dict[str, Any]:
    """四个引擎级键的**全局**真值：持久文件 > 本进程 hub 记账 > 部署默认。

    Args:
        replay: ``ASEHub._replay["runtime_config"]``（**本进程**的 admin 记账）。
            传 None 表示本进程尚未有 admin 保存。**不要**传
            ``hub.get_runtime_config()``：那是首引擎视图，会被 per-user 值污染。

    🔴 优先级（2026-10-30 二轮复核）：**持久文件逐键压过 replay**。动机是
    4-worker 下的陈旧 replay —— admin 后一次保存只更新接到 POST 的那个 worker
    的 ``_replay``；其余 worker 的 replay 仍是上一次的值。若 replay 优先，它们
    的 admin GET / 用户 ``global_default`` / 墓碑回滚会读旧值（实证：文件
    6.25/5 + 旧 replay 5.5/6 → 旧实现返回 5.5/6）。故：文件里**存在**的键用
    文件值；文件里缺该键时 replay 才作为兜底（覆盖「本进程刚保存但持久写失败
    之前」与「本批之前只写过 replay 的遗留」两种情形）；最后才是 yaml/模型默认。

    返回：``{threshold, max_daily_messages, min_interval_minutes,
    cooldown_after_reply_minutes}``（类型已归一）。
    """
    values = global_config()
    out: dict[str, Any] = {key: values[key] for key in ENGINE_KEYS}
    persisted = _persisted_engine_values()
    if isinstance(replay, dict):
        for key in ENGINE_KEYS:
            if key in persisted:
                # 持久文件（跨 worker 真源）更新 → 忽略本进程旧 replay
                continue
            if replay.get(key) is None:
                continue
            coerced = _coerce_engine_value(_FIELD_BY_NAME[key], replay[key])
            if coerced is not None:
                out[key] = coerced
    return out


def _coerce_hour(value: Any) -> int | None:
    """0..23 的整数小时；其余（bool/浮点/越界/字符串）→ None（保持默认）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= 23:
        return None
    return value


def _type_ok(field: ProactiveField, value: Any) -> bool:
    if field.type == "bool":
        return isinstance(value, bool)
    if field.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif field.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    else:  # pragma: no cover - 字段表只声明上面三种
        return False
    if field.bounds is not None:
        lo, hi = field.bounds
        return lo <= float(value) <= hi
    return True


def _normalize(field: ProactiveField, value: Any) -> Any:
    """按字段类型归一（int 键钳成 int；float 键钳成 float；bool 原样）。"""
    if field.type == "int":
        return int(value)
    if field.type == "float":
        return float(value)
    return bool(value)


def _sanitize_override(data: dict[str, Any]) -> dict[str, Any]:
    """宽松修复库里存的手改坏值：未知键/类型不符/越界一律丢弃（回落全局）。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == _REVERT_KEY:
            continue
        field = _FIELD_BY_NAME.get(key)
        if field is None:
            logger.debug("user_proactive_config 忽略未知字段 %r", key)
            continue
        if not _type_ok(field, value):
            logger.debug("user_proactive_config 忽略非法值 %r=%r", key, value)
            continue
        out[key] = _normalize(field, value)
    return out


def validate_partial(payload: dict[str, Any]) -> dict[str, Any]:
    """按字段表严格校验一份 partial payload；未知/类型不符/越界 → ``ValueError``。

    写侧（用户自助端点）走严格判定 —— 与 :func:`_sanitize_override` 的
    「读侧宽松、坏值丢弃」分工互补：写坏必须当场 400，读坏绝不炸主链。
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        field = _FIELD_BY_NAME.get(key)
        if field is None:
            raise ValueError(f"未知的主动调度字段: {key!r}；可覆盖字段为 {list(FIELDS_ALL)}")
        if not _type_ok(field, value):
            hint = ""
            if field.bounds is not None:
                hint = f"（允许 {field.bounds[0]}..{field.bounds[1]}）"
            raise ValueError(f"{key} 取值非法: {value!r}{hint}")
        out[key] = _normalize(field, value)
    return out


def effective(user_key: str | None = None) -> EffectiveConfig:
    """热读该会话的生效配置（用户覆盖 → 全局 → 默认）。

    Args:
        user_key: 完整会话键（v1.28 形态 ``N:wxid``）。``None``/空串 → 只读
            全局默认（后台线 / 无会话上下文的调用点保持旧行为）。

    语义（D15 显式声明、绝不猜测）：
      - 覆盖表里没有该 user_key 的行 → 回落全局，不做任何模糊匹配；
      - 行存在但单字段非法 → 该字段回落全局（不信任脏数据，不抛异常）；
      - 覆盖库不可用（缺失 / 损坏 / 权限）→ 回落全局，**绝不抛异常炸主链**。
    """
    base = global_config()
    overrides = read_override(user_key)
    if not overrides:
        return EffectiveConfig(values=base, overridden_keys=frozenset(), global_values=base)
    merged = {**base, **overrides}
    return EffectiveConfig(
        values=merged,
        overridden_keys=frozenset(overrides),
        global_values=base,
    )


def read_override(user_key: str | None) -> dict[str, Any]:
    """读该用户的**有效**覆盖字典（已宽松修复，墓碑键不出现）；失败 → 空 dict。"""
    uk = str(user_key or "").strip()
    if not uk:
        return {}
    raw = _read_raw(uk, strict=False)
    return _sanitize_override(raw)


def pending_revert_keys(user_key: str | None) -> list[str]:
    """墓碑行里待回滚的引擎键（无墓碑/库不可用 → 空列表）。"""
    uk = str(user_key or "").strip()
    if not uk:
        return []
    raw = _read_raw(uk, strict=False)
    keys = raw.get(_REVERT_KEY)
    if not isinstance(keys, list):
        return []
    return [str(k) for k in keys if str(k) in ENGINE_KEYS]


def has_override(user_key: str) -> bool:
    """该用户是否有**有效覆盖**（墓碑行不算；前端「个人设置/跟随全局」徽标真源）。"""
    uk = str(user_key or "").strip()
    if not uk:
        return False
    try:
        with closing(_override_conn()) as conn:
            row = conn.execute(
                f"SELECT config FROM {OVERRIDE_TABLE} WHERE user_key = ?", (uk,)
            ).fetchone()
        if not row or not row[0]:
            return False
        data = json.loads(row[0])
        return bool(_sanitize_override(data)) if isinstance(data, dict) else False
    except Exception as e:  # noqa: BLE001
        logger.warning("查询主动调度覆盖存在性失败 user=%s: %s", uk, e)
        return False


def write_proactive_config(*, user_key: str, **partial: Any) -> EffectiveConfig:
    """partial merge 写**该用户**的覆盖行（只改传入键，绝不动全局默认）。

    Args:
        user_key: 必填的完整会话键。空 → ``ValueError``：本模块**没有**全局写
            路径，全局面唯一 owner 是 ``/api/proactive/config``（admin）。
        **partial: 字段表内的局部更新（``None`` 视同未提供）。

    若该用户存在待回滚墓碑，本次写入的键会从墓碑清单里摘掉（用户重新设置了
    就不该再被还原），其余引擎键继续按墓碑回滚。

    「读 → 合并墓碑 → 写」全程在同一 ``BEGIN IMMEDIATE`` 事务内（2026-10-30
    二轮复核）：旧实现用两个独立连接，4 worker 下与 ``clear_override`` 并发时
    会丢新覆盖或把刚摘掉的墓碑写回。跨进程由 SQLite RESERVED 锁串行化。

    Raises:
        ValueError: ``user_key`` 为空 / 字段未知 / 类型不符 / 越界。
        sqlite3.Error / OSError: 落盘失败**上抛**（调用方必须知道没生效）。
    """
    uk = str(user_key or "").strip()
    if not uk:
        raise ValueError("user_key 必填：全局主动调度配置的写入 owner 是 /api/proactive/config")
    payload = {k: v for k, v in partial.items() if v is not None}
    if not payload:
        return effective(uk)
    validate_partial(payload)
    with _override_txn() as conn:
        stored = _read_raw_conn(conn, uk, strict=True)
        raw_revert = stored.get(_REVERT_KEY)
        revert = {
            str(k) for k in (raw_revert if isinstance(raw_revert, list) else [])
            if str(k) in ENGINE_KEYS
        }
        stored.pop(_REVERT_KEY, None)
        stored.update(payload)
        revert -= set(payload)
        if revert:
            stored[_REVERT_KEY] = sorted(revert)
        _write_override_conn(conn, uk, stored)
    return effective(uk)


def clear_override(user_key: str) -> bool:
    """清除该用户的覆盖（回落全局）。返回是否真的清掉了东西（幂等）。

    🔴 引擎级键不能只删行：已写进该用户引擎的个人值（且被引擎状态文件持久化）
    会留在原地，用户看到「回到全局」却仍按个人值调度。故此处改写**墓碑行**
    ``{"__revert__": [引擎键...]}``，由 scheduler 在下一拍把引擎还原成全局值后
    :func:`clear_revert_marker` 摘标记（**保留**期间新写入的覆盖）。
    只覆盖非引擎键（如 paused/enabled）时直接删行 —— 没有引擎残留要清。

    「读 → 判定 → 写/删」在同一 ``BEGIN IMMEDIATE`` 事务内（2026-10-30 二轮
    复核）：并发 DELETE×2 或 DELETE×POST 不再互相覆盖（旧实现各自独立连接，
    后写者用陈旧快照回写）。

    Raises:
        sqlite3.Error / OSError: 覆盖库不可用 → **上抛**。旧实现吞异常返回
            False，DELETE 端点据此回 ``noop/200`` = 写盘失败却假成功。
    """
    uk = str(user_key or "").strip()
    if not uk:
        return False
    with _override_txn() as conn:
        stored = _read_raw_conn(conn, uk, strict=True)
        raw_revert = stored.get(_REVERT_KEY)
        pending = {
            str(k) for k in (raw_revert if isinstance(raw_revert, list) else [])
            if str(k) in ENGINE_KEYS
        }
        # 🔴 引擎键取自**原始**键集，不经 sanitize（2026-10-30 复核）：某引擎键当前
        # 值已损坏/越界会被 sanitize 丢弃，但引擎此前确按旧的有效值写过个人值 ——
        # 只按 sanitize 结果算 engine_keys 会直接删行、不写墓碑，用户的 DELETE 回
        # ``noop`` 而引擎永远停在旧个人值。
        raw_keys = {k for k in stored if k in _FIELD_BY_NAME}
        engine_keys = sorted(raw_keys & set(ENGINE_KEYS))
        had_any = bool(raw_keys) or bool(pending)
        if engine_keys or pending:
            _write_override_conn(conn, uk, {_REVERT_KEY: sorted(pending | set(engine_keys))})
        elif stored:
            _delete_row_conn(conn, uk)
    return had_any


def clear_revert_marker(user_key: str, reverted_keys: Any = None) -> bool:
    """摘掉**本次已回滚**的墓碑键，保留其余墓碑与有效覆盖（scheduler 调用）。

    🔴 不能直接删整行：``DELETE`` 之后用户常常立刻重新设置（例如先取消自动
    消息、再单独把 ``paused`` 打开）。若回滚完成时 ``purge`` 整行，就会把
    刚写进去的新覆盖一起删掉 —— UI 行消失、引擎仍可能保留旧个人值
    （2026-10-30 复核 high）。

    🔴 也不能「有墓碑就整段摘」：回滚读到的 pending 与摘标记之间可能又发生
    一次 DELETE（新墓碑）或写入。``reverted_keys`` 是**本次真正下发成功**的
    键集，只有它们从墓碑里移除；期间新加的墓碑键原样保留、下一拍继续回滚
    （2026-10-30 复核 medium：无 expected pending 集合会误摘并发新墓碑）。

    「读 → 过滤 → 写/删」在同一 ``BEGIN IMMEDIATE`` 事务内（2026-10-30 二轮
    复核）：与并发的 DELETE/POST 互斥，不会把已被改写的行按旧快照写回。

    Args:
        reverted_keys: 本次已回滚的引擎键；``None`` → 视为全部（旧调用口径）。

    返回是否真的改动了墓碑标记（无墓碑 → False）。
    """
    uk = str(user_key or "").strip()
    if not uk:
        return False
    with _override_txn() as conn:
        stored = _read_raw_conn(conn, uk, strict=True)
        raw_revert = stored.get(_REVERT_KEY)
        if not isinstance(raw_revert, list) or not raw_revert:
            return False
        current = {str(k) for k in raw_revert if str(k) in ENGINE_KEYS}
        done = current if reverted_keys is None else {
            str(k) for k in reverted_keys if str(k) in ENGINE_KEYS
        }
        remaining = sorted(current - done)
        stored.pop(_REVERT_KEY, None)
        if remaining:
            stored[_REVERT_KEY] = remaining
        if stored:
            _write_override_conn(conn, uk, stored)
        else:
            _delete_row_conn(conn, uk)
    return True


def purge_override_row(user_key: str) -> bool:
    """硬删该用户的覆盖行（运维/测试用的整行清除；scheduler 回滚走
    :func:`clear_revert_marker`，不得用本函数 —— 会连带删掉新写入的覆盖）。"""
    uk = str(user_key or "").strip()
    if not uk:
        return False
    return _delete_row(uk)


# ── per-user 覆盖存储 ─────────────────────────────────────────
#
# 与 user_profile / user_facts / user_humanize 同库同隔离域（user_key = 完整
# 会话键 `N:wxid`）。幂等建表：每次连接 `CREATE TABLE IF NOT EXISTS`
# （SQLite 的 DDL 是原子的）；不写迁移脚本 —— 旧库首次访问即建表。
#
# 删号 purge：`shisi/memory/legacy/structured_memory.py::purge_owner_data` /
# `count_owner_data` 的固定清单已登记本表（与 user_profile 同款 `_has_table`
# 守卫）—— 否则删号后残留个人调度行（隐私红线）。

_OVERRIDE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {OVERRIDE_TABLE} (
    user_key TEXT PRIMARY KEY,
    config TEXT NOT NULL DEFAULT '{{}}',
    updated_at TEXT NOT NULL DEFAULT ''
)
"""


def _override_conn() -> sqlite3.Connection:
    """打开覆盖库并幂等建表。

    路径父目录不存在时先建（冷启动 / 测试 tmp 场景）；失败上抛，由调用方决定
    是「回落全局」还是「写入失败上抛」。
    """
    path = Path(_OVERRIDE_DB_PATH)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute(_OVERRIDE_SCHEMA)
    conn.commit()
    return conn


@contextlib.contextmanager
def _override_txn() -> Iterator[sqlite3.Connection]:
    """覆盖表的**单写事务**：``BEGIN IMMEDIATE`` 起持 RESERVED 锁，提交时释放。

    为什么需要（2026-10-30 二轮复核）：``write_proactive_config`` /
    ``clear_override`` / ``clear_revert_marker`` 都是「读 → 改 → 写」三步。
    旧实现每步各开一个连接：4 worker 下 DELETE 与 POST 并发时，后写者用读到
    的**陈旧快照**回写 → 丢新覆盖、或把刚摘掉的墓碑写回。显式 ``BEGIN IMMEDIATE``
    让第二个写者在 ``timeout`` 内阻塞等待而非读脏快照（WAL/rollback-journal
    下均由 SQLite 的写锁保证）。

    失败路径整体 ``ROLLBACK`` 再上抛（写盘失败必须让调用方知道没生效）。
    """
    conn = _override_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _read_raw_conn(
    conn: sqlite3.Connection, user_key: str, *, strict: bool,
) -> dict[str, Any]:
    """在**给定连接/事务**里读原始覆盖字典（含墓碑键）。

    ``strict=False`` → 任何异常/坏 JSON → 空 dict（读侧回落全局）；
    ``strict=True`` → 上抛（写/清路径必须知道一行坏了，不能静默覆盖）。
    """
    try:
        row = conn.execute(
            f"SELECT config FROM {OVERRIDE_TABLE} WHERE user_key = ?", (user_key,)
        ).fetchone()
    except Exception as e:  # noqa: BLE001
        if strict:
            raise
        logger.debug("读取主动调度覆盖失败 user=%s（回落全局）: %s", user_key, e)
        return {}
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except Exception as e:  # noqa: BLE001
        if strict:
            raise ValueError(f"覆盖行 JSON 损坏（user={user_key}）: {e}") from e
        logger.warning("user_proactive_config.config 非法 JSON（user=%s），忽略", user_key)
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"覆盖行不是 JSON 对象（user={user_key}）")
        logger.warning("user_proactive_config.config 非 dict（user=%s），忽略", user_key)
        return {}
    return data


def _read_raw(user_key: str, *, strict: bool) -> dict[str, Any]:
    """读原始覆盖字典（含墓碑键）。``strict=False`` 时任何异常 → 空 dict。"""
    try:
        with closing(_override_conn()) as conn:
            return _read_raw_conn(conn, user_key, strict=strict)
    except Exception as e:  # noqa: BLE001
        if strict:
            raise
        logger.debug("读取主动调度覆盖失败 user=%s（回落全局）: %s", user_key, e)
        return {}


def _write_override_conn(
    conn: sqlite3.Connection, user_key: str, overrides: dict[str, Any],
) -> None:
    """在给定连接/事务里写/更新该用户的覆盖行。失败**上抛**。"""
    conn.execute(
        f"INSERT INTO {OVERRIDE_TABLE} (user_key, config, updated_at) VALUES (?, ?, ?) "
        f"ON CONFLICT(user_key) DO UPDATE SET config = excluded.config, "
        f"updated_at = excluded.updated_at",
        (
            user_key,
            json.dumps(overrides, ensure_ascii=False, sort_keys=True),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _delete_row_conn(conn: sqlite3.Connection, user_key: str) -> bool:
    """在给定连接/事务里删行；返回是否有行被删。失败**上抛**。"""
    cur = conn.execute(f"DELETE FROM {OVERRIDE_TABLE} WHERE user_key = ?", (user_key,))
    return bool(cur.rowcount)


def _delete_row(user_key: str) -> bool:
    """删行（独立写事务）；返回是否有行被删。失败**上抛**。"""
    with _override_txn() as conn:
        return _delete_row_conn(conn, user_key)
