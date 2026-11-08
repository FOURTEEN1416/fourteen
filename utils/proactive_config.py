"""主动调度设置 —— 「我 × 这个角色」快照 + 全局默认（读唯一真源）。

## 2026-11-08 批：per-user 覆盖层退休

用户裁决（D-A / 本批任务书 item 3）：「后端 /api/user/reply-mode 与
/api/user/humanize 端点、user_reply_mode 与 user_humanize 读层整体退役」；
`user_proactive_config` per-user 层同样退役（保留 `/api/user/proactive/history`
只读面）。主动开关 / 暂停 / 免打扰 / 频率参数因此不再按「人」叠一层覆盖，
而是收进「用户 × 这个角色」的全量快照（`utils.character_settings`）：

    角色快照（user_key × character_id，唯一用户可写真源）
        ↓ 无角色上下文
    全局默认（config/system.yaml 的 proactive 块 + data/scheduler_config.json）
        ↓ 缺失
    静态默认（字段表 / ProactiveConfig 模型默认）

**墓碑机制一并退役**：旧实现给「清除覆盖」写 `{"__revert__": [...]}` 墓碑行，
由 scheduler 下一拍把该用户引擎还原成全局值。快照语义下不需要墓碑——
`DELETE /api/characters/{id}/my-settings` 后下一次读取会按当前出厂默认整份
重新物化，scheduler 的值比较（`_apply_user_engine_config`）自动把引擎对齐回
全局，稳态零写入。

## 全局值的真源分三处（2026-10-30 复核修正：此前硬编码 4.0/10 与生产不符）

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
  优先会让 admin GET / 用户 ``global_default`` 读到旧全局值
  （2026-10-30 二轮复核）。``ASEHub.get_runtime_config()`` 是「第一个已加载
  引擎」视图，会被 per-user 值污染，**不得**当作全局真值。

可配置键 = 决策前置闸的 ``enabled`` / ``paused``、静默时段两端、四个引擎级
频率参数。``llm_proactive_*`` 提示词类字段与 ``dice_gate`` 走各自 owner
（`proactive.llm_proactive` / `proactive.frequency`）。

🔴 引擎级下发的硬约束（值在本模块，下发纪律在 scheduler）：角色快照里的引擎级
值**不得**经 ``ASEHub.apply_runtime_config`` —— 它会写
``_replay["runtime_config"]`` 并扇出到**全部**引擎（``proactive/ase_hub.py:235-241``），
既跨用户泄漏、又会被 ``_apply_replay`` 传染给新创建的引擎。必须对该用户的引擎
直调 ``eng.apply_runtime_config(...)``。

并发口径：读侧**每次热读、无进程缓存**；全局文件写入只经
:func:`write_engine_globals`（`utils.json_state.update_json` 锁内读改写）。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from observability.config_models import ProactiveConfig
from utils import json_state

logger = logging.getLogger("proactive_config")

#: 全局运行时真源（只读！admin 的 ``/api/proactive/config`` 是写入方）。
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"

#: 引擎级默认值的部署真源（与 ``_init_mixin`` 构造引擎用的 ``cfg.proactive`` 同源）。
_SYSTEM_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "system.yaml"

#: 全局文件内的默认值来源（与 scheduler 读的是同一批键，不另立真源口径）。
_GLOBAL_ENABLED_BLOCK = "llm_proactive"
_GLOBAL_PAUSED_BLOCK = "proactive"
_GLOBAL_QUIET_BLOCK = "quiet_hours"
#: admin 经 ``/api/proactive/config`` 保存的**引擎级全局值**（跨 worker 持久真源）。
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
QUIET_KEYS: tuple[str, ...] = ("quiet_hours_start", "quiet_hours_end")


@dataclass(frozen=True)
class EffectiveConfig:
    """生效值 + 该作用域真正覆盖了哪些键。

    ``overridden_keys`` 是 scheduler 的「快照优先、否则全局」判据：
    - 走角色快照时 = 字段表全部键（快照是**全量**设置，不是差异）；
    - 只读全局时 = 空集（所有键都取全局值）。
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


def _coerce_hour(value: Any) -> int | None:
    """0..23 的整数小时；其余（bool/浮点/越界/字符串）→ None（保持默认）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= 23:
        return None
    return value


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
    同口径）。用户侧设置不再有独立的写入口 —— per-user 层已退役，改走
    `PUT /api/characters/{id}/my-settings`（严格字段表在 `utils.character_settings`）。
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
            ``hub.get_runtime_config()``：那是首引擎视图，会被某个用户的角色
            快照值污染。

    🔴 优先级（2026-10-30 二轮复核）：**持久文件逐键压过 replay**。动机是
    4-worker 下的陈旧 replay —— admin 后一次保存只更新接到 POST 的那个 worker
    的 ``_replay``；其余 worker 的 replay 仍是上一次的值。若 replay 优先，它们
    的 admin GET / 角色快照的全局默认 / 引擎对齐会读旧值（实证：文件
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


def effective(
    user_key: str | None = None, character_id: str | None = None
) -> EffectiveConfig:
    """热读生效配置：「我 × 这个角色」快照 → 全局默认。

    Args:
        user_key: 完整会话键（v1.28 形态 ``N:wxid``）。``None``/空串 → 只读
            全局默认（后台线 / 无会话上下文的调用点保持旧行为）。
        character_id: 卡片 id。**两者都给才走快照**（`utils.character_settings`
            唯一真源，用户裁决「每个角色各自一套设置」）；缺任一项 → 全局。

    语义（D15 显式声明、绝不猜测）：
      - 快照行全量存在（首次读取物化），所以 ``overridden_keys`` 为该组全部键；
      - 快照不可用 → 回落全局，**绝不抛异常炸主链**；
      - 旧的 per-user 覆盖层已于 2026-11-08 退役，本函数不再读它。
    """
    base = global_config()

    if user_key and character_id:
        try:
            from utils import character_settings

            snapshot = character_settings.read(str(user_key).strip(), str(character_id).strip())
            values = {**base, **{k: v for k, v in snapshot.items() if k in FIELDS_ALL}}
            return EffectiveConfig(
                values=values,
                overridden_keys=frozenset(FIELDS_ALL),
                global_values=base,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "角色级主动设置读取失败，回落全局 user=%s ch=%s: %s",
                user_key, character_id, exc,
            )

    return EffectiveConfig(values=base, overridden_keys=frozenset(), global_values=base)
