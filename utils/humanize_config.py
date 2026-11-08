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
字段表（名称/类型/默认值/边界/可写/标签），``DEFAULTS`` / ``INT_BOUNDS`` /
``WRITABLE_FIELDS`` 均由它派生 —— 新增字段只改一处，杜绝 API 校验、读侧回落、
前端镜像三处字面量再次分叉（v1.56 R7 键漂移 P0 教训）。

## 2026-11-08 批：per-user 覆盖层退休

用户裁决（D-A）：「后端 /api/user/humanize 端点、user_humanize 读层整体退役」
——「拟人节奏」不再是账号页的全站/按人偏好，而是「用户 × 这个角色」的
设置项（`utils.character_settings` 的 humanize 组）。因此本模块的读取链收敛为：

    角色快照（user_key × character_id，`character_settings.read`）
        ↓ 无角色上下文
    全局默认（``humanize`` 块）
        ↓ 缺失/脏值
    静态默认（字段表）

`user_humanize` 表与 `user_override_enabled` 全局开关一并退役（后者的唯一作用
就是开关那张表）；存量覆盖行由
`scripts/migrate_per_user_prefs_to_my_settings.py` 迁入各用户当前激活角色的快照。

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

#: 单次回复拆条上限 —— 2026-10-27 Q6 用户裁决：默认 **3**，可在 2/3/4 之间配置
#: （「放宽到 3–4 段」；超上限时剩余内容并入最后一条，内容逐字无损）。
MAX_SEGMENTS = 3
SEGMENT_CAP_CHOICES = (2, 3, 4)

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
    label: str = ""


#: 爱语默认键位对照见设计契约；本项目的默认值 = 现行公式的等价参数。
#: 顺序即落盘键序（新增键追加在末尾，保持文件形态稳定）。
HUMANIZE_FIELDS: tuple[HumanizeField, ...] = (
    HumanizeField("delay_mode", "str", DEFAULT_DELAY_MODE, None, True, "段间延时模式"),
    HumanizeField("per_char_ms", "int", 45, (5, 200), True, "逐字每字毫秒"),
    HumanizeField("fixed_ms", "int", 600, (0, 5000), True, "固定延时毫秒"),
    HumanizeField("random_min_ms", "int", 400, (0, 10000), True, "随机区间下界"),
    HumanizeField("random_max_ms", "int", 1600, (0, 10000), True, "随机区间上界"),
    HumanizeField("delay_floor_ms", "int", 400, (0, 10000), True, "延时下钳"),
    HumanizeField("delay_ceil_ms", "int", 1600, (0, 60000), True, "延时上钳"),
    HumanizeField("bracket_delay_ms", "int", 0, (0, 5000), True, "括号段覆盖延时"),
    HumanizeField("proactive_split", "bool", False, None, True, "主动消息拆条"),
    HumanizeField("read_delay_enabled", "bool", True, None, True, "生成前阅读延迟"),
    HumanizeField("read_delay_min_ms", "int", 1000, (0, 30000), True, "阅读延迟下界"),
    HumanizeField("read_delay_max_ms", "int", 6000, (0, 60000), True, "阅读延迟上界"),
    HumanizeField("read_delay_chars_per_second", "int", 0, (0, 100), True, "阅读速度(字/秒,0=关)"),
    HumanizeField("read_delay_max_total_ms", "int", 30000, (0, 120000), True, "阅读延迟总上限"),
    HumanizeField("read_delay_channel_cap_ms", "int", 5000, (0, 60000), True, "无typing渠道封顶"),
    HumanizeField("typing_enabled", "bool", True, None, True, "输入状态(typing)"),
    HumanizeField("typo_rate", "int", 0, (0, 100), True, "沉浸式口误概率(%)"),
    HumanizeField("max_segments", "int", MAX_SEGMENTS, None, True, "单次拆条上限(2/3/4)"),
)

_FIELD_BY_NAME: dict[str, HumanizeField] = {f.name: f for f in HUMANIZE_FIELDS}

#: 由字段表派生（对外名称保持不变，供 API/测试/前端恢复面消费）。
DEFAULTS: dict[str, Any] = {f.name: f.default for f in HUMANIZE_FIELDS if f.writable}

#: 整型字段的闭区间（写侧硬校验；读侧越界 → 回落默认）。
INT_BOUNDS: dict[str, tuple[int, int]] = {
    f.name: f.bounds for f in HUMANIZE_FIELDS
    if f.type == "int" and f.bounds is not None
}

#: 可写字段（依序落盘，保证文件形态稳定；``max_segments`` 仅接受 2/3/4）。
WRITABLE_FIELDS: tuple[str, ...] = tuple(f.name for f in HUMANIZE_FIELDS if f.writable)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"
_HUMANIZE_KEY = "humanize"


@dataclass(frozen=True)
class HumanizeConfig:
    """生效中的拟人度配置（值语义；``max_segments`` 取值 2/3/4，见字段表）。"""

    delay_mode: str = DEFAULT_DELAY_MODE
    per_char_ms: int = 45
    fixed_ms: int = 600
    random_min_ms: int = 400
    random_max_ms: int = 1600
    delay_floor_ms: int = 400
    delay_ceil_ms: int = 1600
    bracket_delay_ms: int = 0
    proactive_split: bool = False
    read_delay_enabled: bool = True
    read_delay_min_ms: int = 1000
    read_delay_max_ms: int = 6000
    read_delay_chars_per_second: int = 0
    read_delay_max_total_ms: int = 30000
    read_delay_channel_cap_ms: int = 5000
    typing_enabled: bool = True
    typo_rate: int = 0
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
            "read_delay_enabled": self.read_delay_enabled,
            "read_delay_min_ms": self.read_delay_min_ms,
            "read_delay_max_ms": self.read_delay_max_ms,
            "read_delay_chars_per_second": self.read_delay_chars_per_second,
            "read_delay_max_total_ms": self.read_delay_max_total_ms,
            "read_delay_channel_cap_ms": self.read_delay_channel_cap_ms,
            "typing_enabled": self.typing_enabled,
            "typo_rate": self.typo_rate,
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


def _read_segment_cap(block: dict[str, Any]) -> int:
    """拆条上限（Q6）：只接受 2/3/4；其余（含手改坏值）回落默认 3。"""
    value = block.get("max_segments")
    if isinstance(value, bool) or not isinstance(value, int):
        return MAX_SEGMENTS
    if value not in SEGMENT_CAP_CHOICES:
        logger.debug("humanize.max_segments 非法 %r，回落默认 %r", value, MAX_SEGMENTS)
        return MAX_SEGMENTS
    return value


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
    read_min = _read_int(block, "read_delay_min_ms")
    read_max = _read_int(block, "read_delay_max_ms")
    if read_min > read_max:
        logger.warning("humanize 阅读延迟区间倒置（%s > %s），两侧回落默认", read_min, read_max)
        read_min = int(DEFAULTS["read_delay_min_ms"])
        read_max = int(DEFAULTS["read_delay_max_ms"])
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
        "read_delay_enabled": _read_bool(block, "read_delay_enabled"),
        "read_delay_min_ms": read_min,
        "read_delay_max_ms": read_max,
        "read_delay_chars_per_second": _read_int(block, "read_delay_chars_per_second"),
        "read_delay_max_total_ms": _read_int(block, "read_delay_max_total_ms"),
        "read_delay_channel_cap_ms": _read_int(block, "read_delay_channel_cap_ms"),
        "typing_enabled": _read_bool(block, "typing_enabled"),
        "typo_rate": _read_int(block, "typo_rate"),
        "max_segments": _read_segment_cap(block),
    }


def read_humanize(
    user_key: str | None = None, character_id: str | None = None
) -> HumanizeConfig:
    """热读当前拟人度配置（用户×角色快照 → 全局块 → 默认值）。

    Args:
        user_key: 完整会话键（v1.28 形态 ``N:wxid``，由
            ``StructuredMemory.user_key_from_session`` 产出）。
        character_id: 卡片 id。**两者都给才走「我 × 这个角色」的全量快照**
            （`utils.character_settings` 唯一真源，用户裁决「每个角色各自一套」）；
            缺任一项 → 只读全局默认（机器面/无角色上下文的调用点）。

    语义（D15 显式声明、绝不猜测）：
      - 快照行缺失 → 由 `character_settings.read` 按当前出厂默认整份物化；
      - 快照不可用（库损坏/权限）→ 回落全局，**绝不抛异常炸主链**；
      - 全局块单字段非法 → 该字段回落静态默认（不信任脏数据）。

    本函数**不做缓存**（含无 lru_cache、无模块级快照）：4 worker 中任一进程
    写入后，其他 worker 的下一次调用必须立刻看到新值。
    """
    raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    block = raw.get(_HUMANIZE_KEY) if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    values = _effective_global_values(block)

    if user_key and character_id:
        # 2026-11-07 用户裁决：设置按「我 × 这个角色」全量快照。
        # 2026-11-08：跨角色的 per-user 层已退役，此处不再回落它。
        try:
            from utils import character_settings

            snapshot = character_settings.read(str(user_key).strip(), str(character_id).strip())
            for name in ("delay_mode", "per_char_ms", "fixed_ms", "random_min_ms",
                         "random_max_ms", "delay_floor_ms", "delay_ceil_ms",
                         "bracket_delay_ms", "proactive_split", "read_delay_enabled",
                         "read_delay_min_ms", "read_delay_max_ms",
                         "read_delay_chars_per_second", "read_delay_max_total_ms",
                         "read_delay_channel_cap_ms", "typing_enabled", "typo_rate",
                         "max_segments"):
                if name in snapshot:
                    values[name] = snapshot[name]
            return HumanizeConfig(**values)
        except Exception as exc:  # noqa: BLE001
            logger.warning("角色级拟人设置读取失败，回落全局 user=%s ch=%s: %s",
                           user_key, character_id, exc)

    return HumanizeConfig(**values)


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
    if values.get("read_delay_min_ms", 0) > values.get("read_delay_max_ms", 0):
        raise ValueError(
            f"read_delay_min_ms 不得大于 read_delay_max_ms"
            f"（{values['read_delay_min_ms']} > {values['read_delay_max_ms']}）"
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

    for key in ("proactive_split", "read_delay_enabled", "typing_enabled"):
        value = values.get(key)
        if value is None:
            value = DEFAULTS[key]
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值，收到 {type(value).__name__}")
        out[key] = value

    cap = values.get("max_segments")
    if cap is None:
        cap = MAX_SEGMENTS
    if isinstance(cap, bool) or not isinstance(cap, int) or cap not in SEGMENT_CAP_CHOICES:
        raise ValueError(f"max_segments 仅支持 {list(SEGMENT_CAP_CHOICES)}，收到 {cap!r}")
    out["max_segments"] = cap

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
            if key == "max_segments":
                if value not in SEGMENT_CAP_CHOICES:
                    raise ValueError(
                        f"max_segments 仅支持 {list(SEGMENT_CAP_CHOICES)}，收到 {value!r}"
                    )
                continue
            lo, hi = field.bounds or (0, 0)
            if value < lo or value > hi:
                raise ValueError(f"{key} 越界: {value}（允许 {lo}..{hi}）")
            continue
        if key == "delay_mode" and value not in DELAY_MODES:
            raise ValueError(f"delay_mode 非法: {value!r}；可选 {DELAY_MODES}")


def write_humanize(**partial: Any) -> HumanizeConfig:
    """partial merge 写入**全局默认**（只改传入键）；返回写入后的生效配置。

    2026-11-08：per-user 写入路径已随 `user_humanize` 读层一并退役——用户的
    拟人节奏属于「我 × 这个角色」，写入走
    `PUT /api/characters/{id}/my-settings`（`utils.character_settings`）。
    本函数只剩全局默认这一个写入面（owner = admin 的 `/api/humanize/config`）。

    Args:
        **partial: 可写字段的局部更新（``None`` 视同未提供）。

    真源文件是**共享**的（``/api/proactive/config`` 也读写同一份），故
    「读 → 改 → 写」全程在 :func:`utils.json_state.update_json` 的锁内完成，
    且只触碰 ``humanize`` 块 —— 其余顶层键（reply_mode / quiet_hours /
    llm_proactive / vault …）逐字保留。校验失败时直接抛 ``ValueError``，
    **不落盘**（异常在 ``update_json`` 的写盘之前抛出）。
    """
    payload = {k: v for k, v in partial.items() if v is not None}
    unknown = sorted(set(payload) - set(WRITABLE_FIELDS))
    if unknown:
        raise ValueError(f"未知的拟人度字段: {unknown}；可写字段为 {list(WRITABLE_FIELDS)}")

    _validate_partial(payload)

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
