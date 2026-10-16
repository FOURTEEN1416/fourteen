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
一个手改坏的真源文件就能让发送路径炸在投递时刻。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

#: 单次回复拆条上限 —— 用户裁决锁死 2，写侧非 2 一律 ValueError；前端只读。
MAX_SEGMENTS = 2

DELAY_MODE_LABELS: dict[str, str] = {
    DELAY_MODE_DYNAMIC: "逐字(按字数)",
    DELAY_MODE_FIXED: "固定延时",
    DELAY_MODE_RANDOM: "随机区间",
    DELAY_MODE_OFF: "不延时",
}

#: 爱语默认键位对照见设计契约；本项目的默认值 = 现行公式的等价参数。
DEFAULTS: dict[str, Any] = {
    "delay_mode": DEFAULT_DELAY_MODE,
    "per_char_ms": 45,
    "fixed_ms": 600,
    "random_min_ms": 400,
    "random_max_ms": 1600,
    "delay_floor_ms": 400,
    "delay_ceil_ms": 1600,
    "bracket_delay_ms": 0,
    "proactive_split": False,
}

#: 整型字段的闭区间（写侧硬校验；读侧越界 → 回落默认）。
INT_BOUNDS: dict[str, tuple[int, int]] = {
    "per_char_ms": (5, 200),
    "fixed_ms": (0, 5000),
    "random_min_ms": (0, 10000),
    "random_max_ms": (0, 10000),
    "delay_floor_ms": (0, 10000),
    "delay_ceil_ms": (0, 60000),
    "bracket_delay_ms": (0, 5000),
}

#: 可写字段（依序落盘，保证文件形态稳定）；``max_segments`` 只读、不在其中。
WRITABLE_FIELDS: tuple[str, ...] = (
    "delay_mode",
    "per_char_ms",
    "fixed_ms",
    "random_min_ms",
    "random_max_ms",
    "delay_floor_ms",
    "delay_ceil_ms",
    "bracket_delay_ms",
    "proactive_split",
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"
_HUMANIZE_KEY = "humanize"


@dataclass(frozen=True)
class HumanizeConfig:
    """生效中的拟人度配置（值语义；``max_segments`` 恒为 :data:`MAX_SEGMENTS`）。"""

    delay_mode: str = DEFAULT_DELAY_MODE
    per_char_ms: int = 45
    fixed_ms: int = 600
    random_min_ms: int = 400
    random_max_ms: int = 1600
    delay_floor_ms: int = 400
    delay_ceil_ms: int = 1600
    bracket_delay_ms: int = 0
    proactive_split: bool = False
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


def read_humanize() -> HumanizeConfig:
    """热读当前拟人度配置；块缺失/非 dict/单字段非法一律逐字段回落默认。

    本函数**不做缓存**（含无 lru_cache、无模块级快照）：4 worker 中任一进程
    写入后，其他 worker 的下一次调用必须立刻看到新值。

    跨字段不一致（min>max / floor>ceil）两侧一并回落默认 —— 见模块 docstring：
    这类文件组合会让 ``randint(min, max)`` 在投递时刻抛异常。
    """
    raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    block = raw.get(_HUMANIZE_KEY) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
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
    return HumanizeConfig(
        delay_mode=mode if mode in DELAY_MODES else DEFAULT_DELAY_MODE,
        per_char_ms=_read_int(block, "per_char_ms"),
        fixed_ms=_read_int(block, "fixed_ms"),
        random_min_ms=random_min,
        random_max_ms=random_max,
        delay_floor_ms=floor,
        delay_ceil_ms=ceil,
        bracket_delay_ms=_read_int(block, "bracket_delay_ms"),
        proactive_split=_read_bool(block, "proactive_split"),
        max_segments=MAX_SEGMENTS,
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

    split = values.get("proactive_split")
    if split is None:
        split = DEFAULTS["proactive_split"]
    if not isinstance(split, bool):
        raise ValueError(f"proactive_split 必须是布尔值，收到 {type(split).__name__}")
    out["proactive_split"] = split

    if out["random_min_ms"] > out["random_max_ms"]:
        raise ValueError(
            f"random_min_ms 不得大于 random_max_ms（{out['random_min_ms']} > {out['random_max_ms']}）"
        )
    if out["delay_floor_ms"] > out["delay_ceil_ms"]:
        raise ValueError(
            f"delay_floor_ms 不得大于 delay_ceil_ms（{out['delay_floor_ms']} > {out['delay_ceil_ms']}）"
        )
    return out


def write_humanize(**partial: Any) -> HumanizeConfig:
    """partial merge 写入（只改传入键）；返回写入后的生效配置。

    真源文件是**共享**的（``/api/proactive/config`` 也读写同一份），故
    「读 → 改 → 写」全程在 :func:`utils.json_state.update_json` 的锁内完成，
    且只触碰 ``humanize`` 块 —— 其余顶层键（reply_mode / quiet_hours /
    llm_proactive / vault …）逐字保留。校验失败时直接抛 ``ValueError``，
    **不落盘**（异常在 ``update_json`` 的写盘之前抛出）。

    ``max_segments`` 为只读字段：传入非 :data:`MAX_SEGMENTS` 一律 ``ValueError``；
    恰为 2 时不落盘（读侧恒定产出 2，文件里没有该键）。
    """
    payload = {k: v for k, v in partial.items() if v is not None}
    unknown = sorted(set(payload) - set(WRITABLE_FIELDS) - {"max_segments"})
    if unknown:
        raise ValueError(f"未知的拟人度字段: {unknown}；可写字段为 {list(WRITABLE_FIELDS)}")
    if "max_segments" in payload and int(payload["max_segments"]) != MAX_SEGMENTS:
        raise ValueError(
            f"max_segments 固定为 {MAX_SEGMENTS}（用户裁决），不可修改；收到 {payload['max_segments']!r}"
        )
    payload.pop("max_segments", None)

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
