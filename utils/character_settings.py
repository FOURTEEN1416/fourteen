"""per-(用户 × 角色) 设置唯一真源（2026-11-07 用户裁决批）。

## 为什么需要

2026-11-07 用户裁决（原话）：「先选角色，对这个角色进行设置」「不要接收偏好，
就是需要单个单个角色的设置，每个角色的单独设置」「LLM 主动决策 / 骰子闸 /
话题引擎 / 记忆整理本来就是和用户自己体验和个性化强相关的设置，就应该给用户
自己设置」「管理员只需要在基础的每一个用户的体验上加上网站运维」。

旧实现是「全局配置 + 可选的 per-user 覆盖」两层，**没有角色维度**：
`utils/proactive_config.py` 的模块注释甚至明确写着「不做 user_key × character_id
双维（per-character 维度没有任何消费方，registry 登记 deferred）」。本模块把
那个 deferred 打开，并把 `user_humanize` / `llm_proactive` / `dice_gate` /
`topic_state` 四组可调项统一收进同一份「我 × 这个角色」的快照。

## 语义（三层，但只有一层是用户可写的）

    出厂默认值（admin 维护：scheduler_config.json + config/system.yaml）
          ↓ 首次物化时**整份复制**
    我 × 这个角色 的快照（user_character_settings 表，唯一生效真源）

- **全量快照，不是差异**：用户裁决「每人每角色一份」——行里存的是完整取值，
  不是「与全局不同的那几个键」。改出厂默认不会回头改已物化用户的值。
- **首次访问即物化**：`read()` 发现没有行时，从出厂默认整份复制并落库；
  迁移脚本 `scripts/backfill_character_settings.py` 会给存量「用户 × 角色」
  组合批量物化（已存在的 per-user 覆盖值会被继承进快照，不丢用户设置）。
- **角色维度的隔离域**：`user_key` 用完整会话键（`N:wxid`，v1.28 口径），
  `character_id` 用卡片 id；两者共同构成主键，A 用户调林挽夏不影响 B 用户
  调林挽夏，也不影响 A 自己调孙颖莎。
- **读侧逐字段容错**：行内脏值/越界/未知键 → 该字段回落出厂默认，绝不抛异常
  炸主链；**写侧严格**：越界/类型不符/未知键 → `ValueError`，调用方必须知道
  没生效（禁止回执假成功）。
- **不新开第二个真源**：与 `user_profile` / `user_facts` 同库
  （`data/sqlite.db`）同隔离域；全局文件仍由既有 admin 端点
  （`/api/humanize/config`、`/api/proactive/config`、`/api/topic-state/config`）
  作为「出厂默认」唯一写方，本模块**从不写全局文件**。

## 2026-11-08 批：`reply` 组与旧 per-user 层退役

用户裁决（原话）：「D-A 个人偏好区……整块从账号页删除，能力划归为用户×角色
的角色设置；后端 `/api/user/reply-mode` 与 `/api/user/humanize` 端点、
`user_reply_mode` 与 `user_humanize` 读层整体退役」；「D-C 回复模式契约：
my-settings 快照新增字段 reply_mode，字段组键 reply，取值仅限 immersive 或
novel，缺省/未设置=跟随全局默认」。

因此本模块新增 `reply` 组（`reply_mode` 唯一字段）。该字段的「未设置」用
空串表示（全量快照里也存得下），运行时解析链：
快照值（immersive/novel）→ 全局默认（`data/scheduler_config.json`）→
immersive 兜底；解析实现在 `utils/reply_mode.read_reply_mode`。

同时**旧的 per-user 覆盖层（`user_humanize` / `user_proactive_config` /
`user_reply_mode`）整体退役**：首次物化不再继承旧覆盖行，存量数据由
`scripts/migrate_per_user_prefs_to_my_settings.py` 一次性迁入快照
（默认 dry-run）。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("character_settings")

#: 与既有覆盖表同库同隔离域（conftest 会把真实 data/sqlite.db 重定向到沙箱）。
_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

#: 表名（删号 purge / count / verify 清单引用它，防字面量漂移）。
TABLE = "user_character_settings"

#: 五组 + 回复模式（`group` 供前端分区渲染，不参与校验）。
#: 2026-11-08：`reply` 组置首 —— 回复模式决定「怎么回你」，是角色设置的第一层，
#: 前端面板也把它排在「① 说话节奏」之前。
GROUPS: tuple[str, ...] = ("reply", "humanize", "proactive", "llm", "dice", "topic")


@dataclass(frozen=True)
class SettingField:
    """声明式字段表的一行（单一字段表，禁止多处字面量分叉）。"""

    name: str
    type: str                       # "str" | "int" | "float" | "bool"
    default: Any
    group: str
    bounds: tuple[float, float] | None = None
    choices: tuple[str, ...] | None = None
    max_len: int | None = None
    label: str = ""


#: 全部用户可写字段（顺序即落盘键序，新增键追加在末尾保持形态稳定）。
#: 默认值与既有 owner 逐位一致（humanize_config.HUMANIZE_FIELDS /
#: proactive_config 部署默认 / llm_proactive.DEFAULT_WEB_CONFIG /
#: frequency.DICE_GATE_DEFAULTS / topic_state.DEFAULTS）。
SETTING_FIELDS: tuple[SettingField, ...] = (
    # ── ⓪ 回复模式（reply，2026-11-08 D-C 裁决）────────────
    # 取值仅 immersive / novel；空串 = 未设置 = 跟随全局默认（admin 的
    # `/api/proactive/config` 写 `scheduler_config.json.reply_mode`）。
    # 空串是「未设置」的存储形态，不是第三个模式。
    SettingField("reply_mode", "str", "", "reply",
                 choices=("", "immersive", "novel"), label="回复模式（空=跟随全局）"),
    # ── ① 说话节奏（humanize）──────────────────────────────
    SettingField("delay_mode", "str", "dynamic", "humanize",
                 choices=("dynamic", "fixed", "random", "off"), label="段间延时模式"),
    SettingField("per_char_ms", "int", 45, "humanize", (5, 200), label="逐字每字毫秒"),
    SettingField("fixed_ms", "int", 600, "humanize", (0, 5000), label="固定延时毫秒"),
    SettingField("random_min_ms", "int", 400, "humanize", (0, 10000), label="随机区间下界"),
    SettingField("random_max_ms", "int", 1600, "humanize", (0, 10000), label="随机区间上界"),
    SettingField("delay_floor_ms", "int", 400, "humanize", (0, 10000), label="延时下钳"),
    SettingField("delay_ceil_ms", "int", 1600, "humanize", (0, 60000), label="延时上钳"),
    SettingField("bracket_delay_ms", "int", 0, "humanize", (0, 5000), label="括号段延时"),
    SettingField("proactive_split", "bool", False, "humanize", label="主动消息拆句"),
    SettingField("max_segments", "int", 3, "humanize", (2, 4), label="单次拆条上限"),
    SettingField("read_delay_enabled", "bool", True, "humanize", label="生成前阅读延迟"),
    SettingField("read_delay_min_ms", "int", 1000, "humanize", (0, 30000), label="阅读延迟下界"),
    SettingField("read_delay_max_ms", "int", 6000, "humanize", (0, 60000), label="阅读延迟上界"),
    SettingField("read_delay_chars_per_second", "int", 0, "humanize", (0, 100), label="阅读速度"),
    SettingField("read_delay_max_total_ms", "int", 30000, "humanize", (0, 120000), label="阅读延迟总上限"),
    SettingField("read_delay_channel_cap_ms", "int", 5000, "humanize", (0, 60000), label="无typing封顶"),
    SettingField("typing_enabled", "bool", True, "humanize", label="输入状态(typing)"),
    SettingField("typo_rate", "int", 0, "humanize", (0, 100), label="沉浸式口误概率"),
    # ── ② 主动消息调度（proactive）────────────────────────
    SettingField("enabled", "bool", True, "proactive", label="主动消息总开关"),
    SettingField("paused", "bool", False, "proactive", label="暂停"),
    SettingField("threshold", "float", 2.0, "proactive", (0, 20), label="紧迫阈值"),
    SettingField("max_daily_messages", "int", 8, "proactive", (1, 200), label="每日上限"),
    SettingField("min_interval_minutes", "int", 30, "proactive", (0, 1440), label="最小间隔"),
    SettingField("cooldown_after_reply_minutes", "int", 15, "proactive", (0, 1440), label="回复后冷却"),
    SettingField("quiet_hours_start", "int", 23, "proactive", (0, 23), label="免打扰开始"),
    SettingField("quiet_hours_end", "int", 7, "proactive", (0, 23), label="免打扰结束"),
    # ── ③ LLM 主动决策（llm）──────────────────────────────
    SettingField("llm_enabled", "bool", True, "llm", label="启用 LLM 主动决策"),
    SettingField("respect_quiet_hours", "bool", True, "llm", label="免打扰写入 prompt"),
    SettingField("intensity", "str", "normal", "llm",
                 choices=("low", "normal", "high"), label="力度"),
    SettingField("style_hint", "str", "", "llm", max_len=200, label="风格提示"),
    SettingField("character_hint", "str", "", "llm", max_len=200, label="角色补充提示"),
    SettingField("jitter_seconds", "int", 120, "llm", (0, 900), label="等待窗抖动(秒)"),
    # ── ④ 零 token 骰子闸（dice）──────────────────────────
    SettingField("dice_enabled", "bool", False, "dice", label="启用骰子闸"),
    SettingField("dice_dry_run", "bool", True, "dice", label="干跑"),
    SettingField("peak_line", "int", 20, "dice", (0, 100), label="峰时命中线"),
    SettingField("offpeak_line", "int", 60, "dice", (0, 100), label="谷时命中线"),
    SettingField("miss_bonus", "float", 10.0, "dice", (0, 40), label="连没中加成"),
    SettingField("miss_bonus_cap", "float", 40.0, "dice", (0, 40), label="加成上限"),
    SettingField("idle_short_minutes", "int", 20, "dice", (0, 1440), label="短空闲阈值"),
    SettingField("idle_long_minutes", "int", 360, "dice", (1, 10080), label="长空闲阈值"),
    SettingField("idle_short_factor", "float", 0.15, "dice", (0, 1), label="短空闲因子"),
    SettingField("idle_long_factor", "float", 1.5, "dice", (1, 10), label="长空闲因子"),
    # ── ⑤ 话题与记忆（topic）──────────────────────────────
    SettingField("topic_enabled", "bool", True, "topic", label="话题状态机"),
    SettingField("llm_judge_enabled", "bool", True, "topic", label="稀疏 LLM 判定"),
)

_FIELD_BY_NAME: dict[str, SettingField] = {f.name: f for f in SETTING_FIELDS}

#: 出厂默认（静态兜底；`global_defaults()` 会用全局 owner 的真值覆盖）。
STATIC_DEFAULTS: dict[str, Any] = {f.name: f.default for f in SETTING_FIELDS}

#: 组 → 字段名清单（前端分区与 API 过滤共用）。
FIELDS_BY_GROUP: dict[str, tuple[str, ...]] = {
    g: tuple(f.name for f in SETTING_FIELDS if f.group == g) for g in GROUPS
}


# ── 库连接 ────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    path = Path(_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            user_key     TEXT NOT NULL,
            character_id TEXT NOT NULL,
            config       TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (user_key, character_id)
        )
        """
    )


# ── 出厂默认 ──────────────────────────────────────────────


def global_defaults() -> dict[str, Any]:
    """出厂默认值 = 全局 owner 的真值（admin 维护），失败逐组回落静态默认。

    惰性 import：避免 `utils` 与 `proactive` / 兄弟模块的循环导入。
    """
    values = dict(STATIC_DEFAULTS)

    # humanize：读全局文件（不带 user_key = 全局视图）
    try:
        from utils.humanize_config import read_humanize

        g = read_humanize(None)
        for name in FIELDS_BY_GROUP["humanize"]:
            if hasattr(g, name):
                values[name] = getattr(g, name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("humanize 全局默认读取失败（用静态默认）: %s", exc)

    # proactive：全局引擎值
    try:
        from utils.proactive_config import global_config

        g = global_config()
        for name in FIELDS_BY_GROUP["proactive"]:
            if name in g:
                values[name] = g[name]
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactive 全局默认读取失败（用静态默认）: %s", exc)

    # llm：scheduler_config.json → llm_proactive
    try:
        from utils.json_state import read_json
        from utils.project_paths import project_path

        block = read_json(project_path("data", "scheduler_config.json"), default={})
        llm = (block or {}).get("llm_proactive") if isinstance(block, dict) else None
        if isinstance(llm, dict):
            for name in FIELDS_BY_GROUP["llm"]:
                key = "enabled" if name == "llm_enabled" else name
                if key in llm:
                    values[name] = llm[key]
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_proactive 全局默认读取失败（用静态默认）: %s", exc)

    # dice：frequency.DICE_GATE_DEFAULTS 的文件覆盖
    try:
        from utils.json_state import read_json
        from utils.project_paths import project_path

        block = read_json(project_path("data", "scheduler_config.json"), default={})
        dice = (block or {}).get("dice_gate") if isinstance(block, dict) else None
        if isinstance(dice, dict):
            for name in FIELDS_BY_GROUP["dice"]:
                key = name[5:] if name.startswith("dice_") else name
                if key in dice:
                    values[name] = dice[key]
    except Exception as exc:  # noqa: BLE001
        logger.debug("dice_gate 全局默认读取失败（用静态默认）: %s", exc)

    # topic：topic_state.DEFAULTS 的文件覆盖
    try:
        from utils.topic_state import read_topic_config

        t = read_topic_config()
        if "enabled" in t:
            values["topic_enabled"] = t["enabled"]
        if "llm_judge_enabled" in t:
            values["llm_judge_enabled"] = t["llm_judge_enabled"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("topic_state 全局默认读取失败（用静态默认）: %s", exc)

    return _sanitize(values)


# ── 校验 ──────────────────────────────────────────────────


def _coerce(name: str, value: Any) -> Any:
    """写侧严格校验：类型/边界/枚举/长度不符 → ValueError。"""
    field = _FIELD_BY_NAME.get(name)
    if field is None:
        raise ValueError(f"未知设置字段: {name}")

    if field.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{name} 必须是布尔值，收到 {type(value).__name__}")
        return value

    if field.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} 必须是整数，收到 {type(value).__name__}")
        if field.bounds is not None:
            lo, hi = field.bounds
            if not (lo <= value <= hi):
                raise ValueError(f"{name} 越界: {value}（允许 {lo}..{hi}）")
        return value

    if field.type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} 必须是数字，收到 {type(value).__name__}")
        f = float(value)
        if not math.isfinite(f):
            raise ValueError(f"{name} 不能是 NaN/Inf")
        if field.bounds is not None:
            lo, hi = field.bounds
            if not (lo <= f <= hi):
                raise ValueError(f"{name} 越界: {f}（允许 {lo}..{hi}）")
        return f

    # str
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是字符串，收到 {type(value).__name__}")
    if field.choices is not None and value not in field.choices:
        raise ValueError(f"{name} 非法取值: {value!r}（允许 {field.choices}）")
    if field.max_len is not None and len(value) > field.max_len:
        raise ValueError(f"{name} 超过最大长度 {field.max_len}")
    return value


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
    """读侧容错：逐字段类型/边界校验，坏值回落静态默认，绝不抛异常。"""
    out = dict(STATIC_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for name, value in raw.items():
        field = _FIELD_BY_NAME.get(name)
        if field is None:
            continue
        try:
            if field.type == "bool":
                out[name] = value if isinstance(value, bool) else field.default
            elif field.type == "int":
                ok = isinstance(value, int) and not isinstance(value, bool)
                if not ok:
                    continue
                lo, hi = field.bounds or (value, value)
                out[name] = value if lo <= value <= hi else field.default
            elif field.type == "float":
                ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                if not ok:
                    continue
                f = float(value)
                lo, hi = field.bounds or (f, f)
                out[name] = f if math.isfinite(f) and lo <= f <= hi else field.default
            else:
                if not isinstance(value, str):
                    continue
                if field.choices is not None and value not in field.choices:
                    out[name] = field.default
                elif field.max_len is not None and len(value) > field.max_len:
                    out[name] = value[: field.max_len]
                else:
                    out[name] = value
        except Exception:  # noqa: BLE001
            continue
    _heal_cross(out)
    return out


def _heal_cross(values: dict[str, Any]) -> None:
    """跨字段愈合（读侧）：区间倒置时两边回落默认。"""
    if values["random_min_ms"] > values["random_max_ms"]:
        values["random_min_ms"] = STATIC_DEFAULTS["random_min_ms"]
        values["random_max_ms"] = STATIC_DEFAULTS["random_max_ms"]
    if values["delay_floor_ms"] > values["delay_ceil_ms"]:
        values["delay_floor_ms"] = STATIC_DEFAULTS["delay_floor_ms"]
        values["delay_ceil_ms"] = STATIC_DEFAULTS["delay_ceil_ms"]
    if values["read_delay_min_ms"] > values["read_delay_max_ms"]:
        values["read_delay_min_ms"] = STATIC_DEFAULTS["read_delay_min_ms"]
        values["read_delay_max_ms"] = STATIC_DEFAULTS["read_delay_max_ms"]


def _check_cross(values: dict[str, Any]) -> None:
    """跨字段校验（写侧）：区间倒置 → ValueError。"""
    if values["random_min_ms"] > values["random_max_ms"]:
        raise ValueError("random_min_ms 不得大于 random_max_ms")
    if values["delay_floor_ms"] > values["delay_ceil_ms"]:
        raise ValueError("delay_floor_ms 不得大于 delay_ceil_ms")
    if values["read_delay_min_ms"] > values["read_delay_max_ms"]:
        raise ValueError("read_delay_min_ms 不得大于 read_delay_max_ms")


def _validate_pairs() -> None:
    """静态自检：字段表本身不重复、组归属合法。"""
    names = [f.name for f in SETTING_FIELDS]
    assert len(names) == len(set(names)), "SETTING_FIELDS 存在重名"
    for f in SETTING_FIELDS:
        assert f.group in GROUPS, f"未知分组: {f.group}"


_validate_pairs()


# ── 读写 ──────────────────────────────────────────────────


def _row_config(conn: sqlite3.Connection, user_key: str, character_id: str) -> dict[str, Any] | None:
    cur = conn.execute(
        f"SELECT config FROM {TABLE} WHERE user_key = ? AND character_id = ?",
        (user_key, character_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        logger.warning("[character_settings] 行内 JSON 损坏，回落出厂默认 user=%s ch=%s",
                       user_key, character_id)
        return None
    return data if isinstance(data, dict) else None


def _upsert(conn: sqlite3.Connection, user_key: str, character_id: str, values: dict[str, Any]) -> None:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).isoformat()
    conn.execute(
        f"""
        INSERT INTO {TABLE} (user_key, character_id, config, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_key, character_id)
        DO UPDATE SET config = excluded.config, updated_at = excluded.updated_at
        """,
        (user_key, character_id, json.dumps(values, ensure_ascii=False, sort_keys=True), now),
    )


def read(user_key: str, character_id: str) -> dict[str, Any]:
    """读取「我 × 这个角色」的完整设置；缺行时从出厂默认整份物化。"""
    uk = str(user_key or "").strip()
    cid = str(character_id or "").strip()
    if not uk:
        raise ValueError("user_key 不能为空")
    if not cid:
        raise ValueError("character_id 不能为空")

    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        raw = _row_config(conn, uk, cid)
        if raw is None:
            # 首次物化：直接落当前出厂默认（全量快照，不是差异）。
            # 旧的 per-user 覆盖层已于 2026-11-08 退役，存量值只经迁移脚本进入。
            values = _seed_values()
            _upsert(conn, uk, cid, values)
            return values
        # 老行缺新字段 → 用出厂默认补齐并回写（保持「全量快照」语义）
        merged = {**global_defaults(), **{k: v for k, v in raw.items() if k in _FIELD_BY_NAME}}
        values = _sanitize(merged)
        if set(raw) != set(values):
            _upsert(conn, uk, cid, values)
        return values


def write(user_key: str, character_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """写入补丁（全量快照更新）；未知键/越界/类型不符 → ValueError。"""
    uk = str(user_key or "").strip()
    cid = str(character_id or "").strip()
    if not uk:
        raise ValueError("user_key 不能为空")
    if not cid:
        raise ValueError("character_id 不能为空")
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")

    unknown = sorted(set(patch) - set(_FIELD_BY_NAME))
    if unknown:
        raise ValueError(f"未知设置字段: {unknown}")
    coerced = {name: _coerce(name, value) for name, value in patch.items()}

    current = read(uk, cid)
    merged = {**current, **coerced}
    _check_cross(merged)

    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        _upsert(conn, uk, cid, merged)
    return merged


def reset(user_key: str, character_id: str) -> bool:
    """删除该「我 × 角色」的快照；下一次 read 会从**当前**出厂默认重新物化。"""
    uk = str(user_key or "").strip()
    cid = str(character_id or "").strip()
    if not uk or not cid:
        raise ValueError("user_key / character_id 不能为空")
    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        cur = conn.execute(
            f"DELETE FROM {TABLE} WHERE user_key = ? AND character_id = ?", (uk, cid)
        )
        return cur.rowcount > 0


def has_row(user_key: str, character_id: str) -> bool:
    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        cur = conn.execute(
            f"SELECT 1 FROM {TABLE} WHERE user_key = ? AND character_id = ?",
            (str(user_key).strip(), str(character_id).strip()),
        )
        return cur.fetchone() is not None


def _seed_values() -> dict[str, Any]:
    """物化初值 = 当前出厂默认的完整快照。

    2026-11-08：不再继承旧的 per-user 覆盖层（`user_humanize` /
    `user_proactive_config` / `user_reply_mode` 三张表已退役）——存量值由
    `scripts/migrate_per_user_prefs_to_my_settings.py` 一次性迁入快照，
    运行时不做「隐形继承」，否则就是被裁决删除的那一层又活了一遍。
    """
    return global_defaults()


def seed_from_defaults(user_key: str, character_id: str) -> dict[str, Any]:
    """按**当前出厂默认**物化「我 × 角色」快照（迁移/回填复用）。"""
    uk = str(user_key or "").strip()
    cid = str(character_id or "").strip()
    if not uk or not cid:
        raise ValueError("user_key / character_id 不能为空")
    values = _seed_values()
    with closing(_conn()) as conn, conn:
        _ensure_table(conn)
        _upsert(conn, uk, cid, values)
    return values


def backfill_pairs(pairs: Iterable[tuple[str, str]]) -> int:
    """批量物化存量「用户 × 角色」组合；返回新建行数（已存在则跳过）。"""
    created = 0
    for user_key, character_id in pairs:
        uk, cid = str(user_key or "").strip(), str(character_id or "").strip()
        if not uk or not cid:
            continue
        if has_row(uk, cid):
            continue
        seed_from_defaults(uk, cid)
        created += 1
    return created


def group_of(name: str) -> str:
    field = _FIELD_BY_NAME.get(name)
    if field is None:
        raise ValueError(f"未知设置字段: {name}")
    return field.group


def sanitize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """宽松过滤一份补丁：未知键/非法值**逐键丢弃**，返回可安全写入的子集。

    用途只有一个：存量迁移（`scripts/migrate_per_user_prefs_to_my_settings.py`）
    把旧表里的手改坏值折进快照时，不能因为一个脏键让整条迁移失败 —— 但也不能
    把脏值塞进快照。**写侧（`write`）仍然严格**，本函数不是写入路径的兜底。
    """
    out: dict[str, Any] = {}
    if not isinstance(patch, dict):
        return out
    for name, value in patch.items():
        if name not in _FIELD_BY_NAME:
            continue
        try:
            out[name] = _coerce(name, value)
        except ValueError:
            logger.debug("迁移跳过非法字段 %r=%r", name, value)
    return out


__all__ = [
    "TABLE",
    "GROUPS",
    "SETTING_FIELDS",
    "STATIC_DEFAULTS",
    "FIELDS_BY_GROUP",
    "global_defaults",
    "read",
    "write",
    "reset",
    "has_row",
    "seed_from_defaults",
    "backfill_pairs",
    "group_of",
    "sanitize_patch",
]
