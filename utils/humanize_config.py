"""拟人度（humanize）配置 —— 段间延时与主动消息拆条（读写唯一真源）。

真源：``data/scheduler_config.json`` 的 ``humanize`` 键 —— 与 ``quiet_hours`` /
``reply_mode`` / ``llm_proactive`` **同一份跨 worker 运行时配置文件**
（uvicorn 4 worker 共读，控制端写入即对所有 worker 生效，无需重启；
本模块**不复用** ``ProactiveScheduler._CONFIG_PATH`` 之外的任何新配置文件）。

字段与默认值来自爱语 2.8.8 反编译
``SettingsActivity.java:157-192`` 的爱语键位（见设计契约 ``humanize_schema``）。
默认值组合保证**段间延时零行为变更**：
``dynamic + per_char_ms=45 + delay_floor_ms=400 + delay_ceil_ms=1600`` 逐位复现
现行 ``max(0.4, min(1.6, len(prev) * 0.045))`` —— ``45 / 1000.0`` 与字面量
``0.045`` 同为正确舍入的同一 double；``bracket_delay_ms=0`` 使括号分支惰性。
（唯一例外见 ``wechat_direct.wechat_connector`` 档位段的注记：**纯指令段**
段间 0.4s→0.0s，属有意变更、非本模块默认值所致。）

声明式字段表（2026-10-27 G-1）：所有字段收敛到 :data:`HUMANIZE_FIELDS` 单一
字段表（名称/类型/默认值/边界/可写/可按用户覆盖/标签），``DEFAULTS`` /
``INT_BOUNDS`` / ``WRITABLE_FIELDS`` 均由它派生 —— 新增字段只改一处，杜绝
API 校验、读侧回落、前端镜像三处字面量再次分叉（v1.56 R7 键漂移 P0 教训）。

按用户覆盖（2026-10-27 G-2/Q2 裁决）：全局 ``humanize`` 块降为**默认值**；
``user_humanize`` 表（``data/sqlite.db``，与 ``user_profile`` / ``user_facts``
同库同隔离域）存**按用户覆盖**。读取顺序：用户覆盖 → 全局块 → 默认值；
**键缺失 = 继承**（不是「缺失 = 钉死默认」）。回滚总开关
``humanize.user_override_enabled=false`` 即忽略覆盖表（表保留不读、无迁移）。

并发口径（**不得绕过**）：
- 写侧只经 :func:`write_humanize` → :func:`utils.json_state.update_json`
  （进程内锁 + POSIX flock + ``os.replace``），锁内读改写；绝不在本模块内
  用「读 → 改 → atomic_write_json」两步式（两步式的读在锁外，多 worker 会
  用陈旧快照互相覆盖 = ``utils/json_state.py`` 记录的 W3 缺陷 D 类丢更新）。
- 读侧**每次热读、无进程缓存**（照 ``utils.reply_mode.read_reply_mode``）；
  任何进程内缓存都会让 4 worker 各持陈旧视图。

容错读：块缺失 / 非 dict / 单字段类型不符或越界 → **逐字段回落默认**，不抛异常。
跨字段不一致（``random_min_ms > random_max_ms`` 或 ``delay_floor_ms > delay_ceil_ms``）
同样在**读侧**愈合（两侧一并回落默认）—— 单字段各自都在界内、组合起来却
无法使用（``randint(min, max)`` 会直接抛 ``ValueError``），若照原样透出，
一个手改坏的真源文件就能让发送路径炸在投递时刻。按用户覆盖的坏值同理：
只回滚该用户冲突的两侧键，不影响全局。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import json_state

logger = logging.getLogger("humanize_config")

# ── 段间延时模式 ──────────────────────────────────────────────────────────────
DELAY_MODE_DYNAMIC = "dynamic"   # 按上一段字数逐字估算（现行公式）
DELAY_MODE_FIXED = "fixed"       # 固定延时
DELAY_MODE_RANDOM = "random"     # 区间内随机
DELAY_MODE_OFF = "off"           # 不延时（段间连发）
DELAY_MODES = (DELAY_MODE_DYNAMIC, DELAY_MODE_FIXED, DELAY_MODE_RANDOM, DELAY_MODE_OFF)
DEFAULT_DELAY_MODE = DELAY_MODE_DYNAMIC

#: 可**落盘**档位的正则（由 :data:`DELAY_MODES` 派生，供 API 层
#: ``HumanizeConfigRequest.delay_mode`` 的 ``pattern`` 使用）。
#: 此前 API 侧硬编码 ``^(dynamic|fixed|random|off)$``，与本元组无交叉约束 ——
#: 新增档位时两处会静默分叉（2026-10-16 复审 P2「词表分裂」）。
#: 注意：连接器内部另有 fast/normal/slow **打字速度预设**（仅 ``mode=`` 参数
#: 可达，**不可落盘**），故本正则 === 可落盘档位全集，不是连接器可解析档位全集。
DELAY_MODE_PATTERN = "^(" + "|".join(DELAY_MODES) + ")$"

#: 单次回复拆条上限 —— 本批保持用户既有裁决锁死 2，写侧非 2 一律 ValueError；
#: 前端只读。Q6「放宽到 3–4 段」（2026-10-27 裁决）的配置语义与连接器接线
#: 在后续批次单独实施（配置层先行会造成半成品）。
MAX_SEGMENTS = 2

DELAY_MODE_LABELS: dict[str, str] = {
    DELAY_MODE_DYNAMIC: "逐字(按字数)",
    DELAY_MODE_FIXED: "固定延时",
    DELAY_MODE_RANDOM: "随机区间",
    DELAY_MODE_OFF: "不延时",
}


@dataclass(frozen=True)
class HumanizeField:
    """声明式字段表的一行（G-1：单一字段表，禁止多处字面量分叉）。"""

    name: str
    type: str                       # "str" | "int" | "bool"
    default: Any
    bounds: tuple[int, int] | None = None
    writable: bool = True           # 是否可经 API/文件写入
    per_user: bool = True           # 是否支持 user_humanize 按用户覆盖
    label: str = ""


#: 爱语默认键位对照见设计契约；本项目的默认值 = 现行公式的等价参数。
#: 顺序即落盘键序（新增键追加在末尾，保持文件形态稳定）。
HUMANIZE_FIELDS: tuple[HumanizeField, ...] = (
    HumanizeField("delay_mode", "str", DEFAULT_DELAY_MODE, None, True, True, "段间延时模式"),
    HumanizeField("per_char_ms", "int", 45, (5, 200), True, True, "逐字每字毫秒"),
    HumanizeField("fixed_ms", "int", 600, (0, 5000), True, True, "固定延时毫秒"),
    HumanizeField("random_min_ms", "int", 400, (0, 10000), True, True, "随机区间下界"),
    HumanizeField("random_max_ms", "int", 1600, (0, 10000), True, True, "随机区间上界"),
    HumanizeField("delay_floor_ms", "int", 400, (0, 10000), True, True, "延时下钳"),
    HumanizeField("delay_ceil_ms", "int", 1600, (0, 60000), True, True, "延时上钳"),
    HumanizeField("bracket_delay_ms", "int", 0, (0, 5000), True, True, "括号段覆盖延时"),
    HumanizeField("proactive_split", "bool", False, None, True, True, "主动消息拆条"),
    HumanizeField("user_override_enabled", "bool", True, None, True, False, "按用户覆盖总开关"),
    HumanizeField("max_segments", "int", MAX_SEGMENTS, None, False, False, "单次拆条上限（只读）"),
)

_FIELD_BY_NAME: dict[str, HumanizeField] = {f.name: f for f in HUMANIZE_FIELDS}

#: 由字段表派生（对外名称保持不变，供 API/测试/前端恢复面消费）。
DEFAULTS: dict[str, Any] = {f.name: f.default for f in HUMANIZE_FIELDS if f.writable}

#: 整型字段的闭区间（写侧硬校验；读侧越界 → 回落默认）。
INT_BOUNDS: dict[str, tuple[int, int]] = {
    f.name: f.bounds for f in HUMANIZE_FIELDS
    if f.type == "int" and f.bounds is not None
}

#: 可写字段（依序落盘，保证文件形态稳定）；``max_segments`` 只读、不在其中。
WRITABLE_FIELDS: tuple[str, ...] = tuple(f.name for f in HUMANIZE_FIELDS if f.writable)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"
_HUMANIZE_KEY = "humanize"

#: per-user 覆盖库（2026-10-27 G-2）：与 ``user_profile`` / ``user_facts``
#: **同库同隔离域**（``data/sqlite.db``），不新开第二个真源。模块常量便于
#: 测试隔离（conftest 已把真实 ``data/sqlite.db`` 重定向到沙箱）。
_OVERRIDE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

#: 覆盖表名（删号 purge/count/verify 三处清单都引用它，防三处字面量漂移）。
OVERRIDE_TABLE = "user_humanize"


@dataclass(frozen=True)
class HumanizeConfig:
    """生效中的拟人度配置（值语义；``max_segments`` 只读，见字段表）。"""

    delay_mode: str = DEFAULT_DELAY_MODE
    per_char_ms: int = 45
    fixed_ms: int = 600
    random_min_ms: int = 400
    random_max_ms: int = 1600
    delay_floor_ms: int = 400
    delay_ceil_ms: int = 1600
    bracket_delay_ms: int = 0
    proactive_split: bool = False
    user_override_enabled: bool = True
    max_segments: int = MAX_SEGMENTS

    def as_dict(self) -> dict[str, Any]:
        """投影为 API/前端可消费的平铺字典（含只读字段）。"""
        return {
            "delay_mode": self.delay_mode,
            "per_char_ms": self.per_char_ms,
            "fixed_ms": self.fixed_ms,
            "random_min_ms": self.random_min_ms,
            "random_max_ms": self.random_max_ms,
            "delay_floor_ms": self.delay_floor_ms,
            "delay_ceil_ms": self.delay_ceil_ms,
            "bracket_delay_ms": self.bracket_delay_ms,
            "proactive_split": self.proactive_split,
            "user_override_enabled": self.user_override_enabled,
            "max_segments": self.max_segments,
        }


def delay_mode_label(mode: str) -> str:
    """延时模式的展示标签（仅 dynamic 一项由契约钉死字面量）。"""
    return DELAY_MODE_LABELS.get(mode, mode)


def _read_int(block: dict[str, Any], key: str) -> int:
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return int(DEFAULTS[key])
    lo, hi = INT_BOUNDS[key]
    if value < lo or value > hi:
        logger.debug("humanize.%s 越界 %r，回落默认 %r", key, value, DEFAULTS[key])
        return int(DEFAULTS[key])
    return value


def _read_bool(block: dict[str, Any], key: str) -> bool:
    value = block.get(key)
    if isinstance(value, bool):
        return value
    return bool(DEFAULTS[key])


def _effective_global_values(block: dict[str, Any]) -> dict[str, Any]:
    """把全局 ``humanize`` 块按「容错读」折算成完整取值字典（含跨字段愈合）。"""
    mode = block.get("delay_mode")
    random_min = _read_int(block, "random_min_ms")
    random_max = _read_int(block, "random_max_ms")
    if random_min > random_max:
        logger.warning("humanize 随机区间倒置（%s > %s），两侧回落默认", random_min, random_max)
        random_min = int(DEFAULTS["random_min_ms"])
        random_max = int(DEFAULTS["random_max_ms"])
    floor = _read_int(block, "delay_floor_ms")
    ceil = _read_int(block, "delay_ceil_ms")
    if floor > ceil:
        logger.warning("humanize 延时钳位倒置（%s > %s），两侧回落默认", floor, ceil)
        floor = int(DEFAULTS["delay_floor_ms"])
        ceil = int(DEFAULTS["delay_ceil_ms"])
    return {
        "delay_mode": mode if mode in DELAY_MODES else DEFAULT_DELAY_MODE,
        "per_char_ms": _read_int(block, "per_char_ms"),
        "fixed_ms": _read_int(block, "fixed_ms"),
        "random_min_ms": random_min,
        "random_max_ms": random_max,
        "delay_floor_ms": floor,
        "delay_ceil_ms": ceil,
        "bracket_delay_ms": _read_int(block, "bracket_delay_ms"),
        "proactive_split": _read_bool(block, "proactive_split"),
        "user_override_enabled": _read_bool(block, "user_override_enabled"),
    }


def read_humanize(user_key: str | None = None) -> HumanizeConfig:
    """热读当前拟人度配置（用户覆盖 → 全局块 → 默认值）。

    Args:
        user_key: 完整会话键（v1.28 形态 ``N:wxid``，由
            ``StructuredMemory.user_key_from_session`` 产出）。``None``/空串
            → 只读全局默认（后台线/提醒线等无会话上下文的调用点保持旧行为）。

    语义（D15 显式声明、绝不猜测）：
      - 覆盖表里没有该 user_key 的行 → 回落全局块，不做任何模糊匹配；
      - 覆盖行存在但单字段非法 → 该字段回落全局（不信任脏数据，不抛异常）；
      - 覆盖库不可用（缺失/损坏/权限）→ 回落全局，**绝不抛异常炸主链**；
      - ``user_override_enabled=false`` → 覆盖表整体忽略（回滚开关）。

    本函数**不做缓存**（含无 lru_cache、无模块级快照）：4 worker 中任一进程
    写入后，其他 worker 的下一次调用必须立刻看到新值。
    """
    raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    block = raw.get(_HUMANIZE_KEY) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    values = _effective_global_values(block)

    if user_key and values.get("user_override_enabled", True):
        override = _read_override(str(user_key).strip())
        if override:
            merged = {**values, **override}
            if merged["random_min_ms"] > merged["random_max_ms"]:
                logger.warning(
                    "user_humanize 随机区间倒置（user=%s），该用户覆盖回落全局", user_key,
                )
                merged["random_min_ms"] = values["random_min_ms"]
                merged["random_max_ms"] = values["random_max_ms"]
            if merged["delay_floor_ms"] > merged["delay_ceil_ms"]:
                logger.warning(
                    "user_humanize 延时钳位倒置（user=%s），该用户覆盖回落全局", user_key,
                )
                merged["delay_floor_ms"] = values["delay_floor_ms"]
                merged["delay_ceil_ms"] = values["delay_ceil_ms"]
            values = merged

    return HumanizeConfig(**values, max_segments=MAX_SEGMENTS)


def _check_cross_field(values: dict[str, Any]) -> None:
    """跨字段约束（写侧严格判定；读侧由调用方做愈合）。"""
    if values.get("random_min_ms", 0) > values.get("random_max_ms", 0):
        raise ValueError(
            f"random_min_ms 不得大于 random_max_ms（{values['random_min_ms']} > {values['random_max_ms']}）"
        )
    if values.get("delay_floor_ms", 0) > values.get("delay_ceil_ms", 0):
        raise ValueError(
            f"delay_floor_ms 不得大于 delay_ceil_ms（{values['delay_floor_ms']} > {values['delay_ceil_ms']}）"
        )


def validate_payload(values: dict[str, Any]) -> dict[str, Any]:
    """校验并归一化一份**完整**配置（缺键取默认），非法即抛 ``ValueError``。

    ``None`` 视同「未提供」（取默认）—— 与 partial merge 的 None 过滤同口径，
    也避免文件里既有的 ``null`` 让一次无关字段的写入整体失败。
    跨字段约束按**合并后**的取值判定（partial merge 下单独看入参不够）。
    """
    mode = values.get("delay_mode")
    if mode is None:
        mode = DEFAULTS["delay_mode"]
    if mode not in DELAY_MODES:
        raise ValueError(f"delay_mode 非法: {mode!r}；可选 {DELAY_MODES}")

    out: dict[str, Any] = {"delay_mode": mode}
    for key, (lo, hi) in INT_BOUNDS.items():
        value = values.get(key)
        if value is None:
            value = DEFAULTS[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} 必须是整数，收到 {type(value).__name__}")
        if value < lo or value > hi:
            raise ValueError(f"{key} 越界: {value}（允许 {lo}..{hi}）")
        out[key] = int(value)

    for key in ("proactive_split", "user_override_enabled"):
        value = values.get(key)
        if value is None:
            value = DEFAULTS[key]
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值，收到 {type(value).__name__}")
        out[key] = value

    _check_cross_field(out)
    return out


def _validate_partial(payload: dict[str, Any]) -> None:
    """按字段表严格校验一份 partial payload；未知/只读/类型不符/越界 → ``ValueError``。"""
    for key, value in payload.items():
        field = _FIELD_BY_NAME.get(key)
        if field is None or not field.writable:
            raise ValueError(f"未知或只读的拟人度字段: {key!r}；可写字段为 {list(WRITABLE_FIELDS)}")
        if field.type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须是布尔值，收到 {type(value).__name__}")
            continue
        if field.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须是整数，收到 {type(value).__name__}")
            lo, hi = field.bounds or (0, 0)
            if value < lo or value > hi:
                raise ValueError(f"{key} 越界: {value}（允许 {lo}..{hi}）")
            continue
        if key == "delay_mode" and value not in DELAY_MODES:
            raise ValueError(f"delay_mode 非法: {value!r}；可选 {DELAY_MODES}")


def write_humanize(*, user_key: str | None = None, **partial: Any) -> HumanizeConfig:
    """partial merge 写入（只改传入键）；返回写入后的生效配置。

    Args:
        user_key: 非空 → 写**该用户**的覆盖行（不动全局默认）；空/``None`` →
            写全局默认（旧行为，全站生效）。
        **partial: 可写字段的局部更新（``None`` 视同未提供）。

    真源文件是**共享**的（``/api/proactive/config`` 也读写同一份），故
    「读 → 改 → 写」全程在 :func:`utils.json_state.update_json` 的锁内完成，
    且只触碰 ``humanize`` 块 —— 其余顶层键（reply_mode / quiet_hours /
    llm_proactive / vault …）逐字保留。校验失败时直接抛 ``ValueError``，
    **不落盘**（异常在 ``update_json`` 的写盘之前抛出）。

    ``max_segments`` 为只读字段：传入非合法档位一律 ``ValueError``；
    恰为默认档位时不落盘（读侧恒定由字段表产出）。
    """
    payload = {k: v for k, v in partial.items() if v is not None}
    unknown = sorted(set(payload) - set(WRITABLE_FIELDS) - {"max_segments"})
    if unknown:
        raise ValueError(f"未知的拟人度字段: {unknown}；可写字段为 {list(WRITABLE_FIELDS)}")
    if "max_segments" in payload:
        requested = payload.pop("max_segments")
        if int(requested) != MAX_SEGMENTS:
            raise ValueError(
                f"max_segments 固定为 {MAX_SEGMENTS}（用户裁决），不可修改；收到 {requested!r}"
            )

    _validate_partial(payload)

    uk = str(user_key or "").strip()
    if uk:
        for key in payload:
            if not _FIELD_BY_NAME[key].per_user:
                raise ValueError(f"{key} 是全局开关，不支持按用户覆盖")
        current = read_humanize(uk)
        candidate = {
            f.name: getattr(current, f.name) for f in HUMANIZE_FIELDS if f.writable
        }
        candidate.update(payload)
        _check_cross_field(candidate)
        stored = _read_override_raw(uk)
        stored.update(payload)
        _write_override(uk, stored)
        return read_humanize(uk)

    def _mutate(data: dict[str, Any]) -> None:
        block = data.get(_HUMANIZE_KEY)
        if not isinstance(block, dict):
            logger.warning("humanize 块非 dict（%s），按空块重建", type(block).__name__)
            block = {}
        merged: dict[str, Any] = {k: block.get(k) for k in WRITABLE_FIELDS}
        merged.update(payload)
        # 校验失败时抛穿 update_json —— 锁释放、文件保持原样（不写半份配置）
        data[_HUMANIZE_KEY] = validate_payload(merged)

    json_state.update_json(_CONFIG_PATH, _mutate)
    return read_humanize()


def clear_humanize_override(user_key: str) -> bool:
    """删除该用户的覆盖行（回落全局默认）。返回是否有行被删。"""
    uk = str(user_key or "").strip()
    if not uk:
        return False
    try:
        with closing(_override_conn()) as conn, conn:
            cur = conn.execute(f"DELETE FROM {OVERRIDE_TABLE} WHERE user_key = ?", (uk,))
            return bool(cur.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.warning("清除拟人度覆盖失败 user=%s: %s", uk, e)
        return False


# ── per-user 覆盖存储（2026-10-27 G-2）──────────────────────────
#
# 为什么放在 `shisi` 侧的同一个 sqlite.db：用户隔离域（user_key = 完整会话键
# `N:wxid`）与 `user_profile` / `user_facts` / `user_reply_mode` 完全一致。
# 开第二个库只会制造「删了一个、漏了另一个」的第二个真源。
#
# 幂等建表：每次连接 `CREATE TABLE IF NOT EXISTS`（SQLite 的 DDL 是原子的）。
# 不写迁移脚本 —— 旧库首次访问即建表，与 `UserProfileStore._ensure_schema` 同款。

_OVERRIDE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {OVERRIDE_TABLE} (
    user_key TEXT PRIMARY KEY,
    config TEXT NOT NULL DEFAULT '{{}}',
    updated_at TEXT NOT NULL DEFAULT ''
)
"""


def _override_conn() -> sqlite3.Connection:
    """打开覆盖库并幂等建表。

    路径父目录不存在时先建（冷启动/测试 tmp 场景）；失败上抛，由调用方决定
    是「回落全局」还是「写入失败上抛」。
    """
    path = Path(_OVERRIDE_DB_PATH)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute(_OVERRIDE_SCHEMA)
    conn.commit()
    return conn


def _sanitize_override(data: dict[str, Any]) -> dict[str, Any]:
    """宽松修复库里存的手改坏值：未知键/不可覆盖键/类型不符/越界一律丢弃。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        field = _FIELD_BY_NAME.get(key)
        if field is None or not field.per_user:
            logger.debug("user_humanize 忽略不可覆盖字段 %r", key)
            continue
        if field.type == "bool":
            if isinstance(value, bool):
                out[key] = value
            continue
        if field.type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            lo, hi = field.bounds or (0, 0)
            if lo <= value <= hi:
                out[key] = int(value)
            continue
        if key == "delay_mode" and value in DELAY_MODES:
            out[key] = value
    return out


def _read_override_raw(user_key: str) -> dict[str, Any]:
    """读该用户的覆盖字典（已宽松修复）；无键/无行/库不可用 → 空 dict。"""
    uk = str(user_key or "").strip()
    if not uk:
        return {}
    try:
        with closing(_override_conn()) as conn:
            row = conn.execute(
                f"SELECT config FROM {OVERRIDE_TABLE} WHERE user_key = ?", (uk,)
            ).fetchone()
        if not row or not row[0]:
            return {}
        data = json.loads(row[0])
        if not isinstance(data, dict):
            logger.warning("user_humanize.config 非 dict（user=%s），忽略", uk)
            return {}
        return _sanitize_override(data)
    except Exception as e:  # noqa: BLE001
        logger.debug("读取拟人度覆盖失败 user=%s（回落全局）: %s", uk, e)
        return {}


def _read_override(user_key: str) -> dict[str, Any]:
    """``read_humanize`` 的覆盖读取入口（语义同 :func:`_read_override_raw`）。"""
    return _read_override_raw(user_key)


def _write_override(user_key: str, overrides: dict[str, Any]) -> None:
    """写/更新该用户的覆盖行。失败**上抛**（调用方必须知道没生效）。"""
    with closing(_override_conn()) as conn, conn:
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
