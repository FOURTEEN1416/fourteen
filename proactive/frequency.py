"""
频率自适应器（normal→low→minimal） + 频率控制器（三重检查）
+ 零 token 骰子闸纯函数与配置 owner（2026-10-28 dsh-speak-up 批）。

骰子闸的设计目标：**不花一个 token** 就能决定「这次要不要开口」。旧链路
只要走到 LLM 决策就必然产生一次远端调用（暂停/静默/节奏闸之外没有任何
本地概率闸）；骰子闸把「说话欲望」变成一个确定性可复现的概率决策——
峰时（工作日 09–12 / 14–18）命中线低（默认 20%，克制），谷时命中线高
（默认 60%，更主动），连没中会加成本地加成（`miss_bonus`），总上限 100%。
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils import json_state
from utils.local_time import now_local

logger = logging.getLogger("proactive.frequency")


class FrequencyAdapter:
    def __init__(
        self,
        normal_daily: int = 8,
        low_daily: int = 3,
        min_weekly: int = 1,
        override_max_daily: int | None = None,
    ):
        self.normal_daily = normal_daily
        self.low_daily = low_daily
        self.min_weekly = min_weekly
        # 硬上限（2026-10-31 R1-2）：用户的 ``max_daily_messages`` 必须压过
        # **所有档位**。旧实现只改 ``normal_daily`` —— 用户设 1/2 后进入
        # low 档仍按 ``low_daily=3`` 发送，个人上限不执行。
        # 缺省即 normal_daily：normal=8 时 low=3/minimal=1 不受影响（min 语义），
        # 而 normal=1/2 时所有档位都会被压到 1/2 —— 这正是「上限」的定义。
        self.override_max_daily = (
            normal_daily if override_max_daily is None else override_max_daily
        )
        self._unanswered_count = 0
        self._current_level = "normal"

    def on_reply_received(self) -> None:
        self._unanswered_count = max(0, self._unanswered_count - 1)
        if self._unanswered_count == 0:
            # 逐级回升：minimal→low→normal（旧实现只认 low→normal，
            # 落入 minimal 后永久粘滞——生产实证用户持续回复仍被限 1 条/日）
            if self._current_level == "minimal":
                self._current_level = "low"
            elif self._current_level == "low":
                self._current_level = "normal"

    def on_no_reply(self) -> None:
        self._unanswered_count += 1
        if self._unanswered_count >= 5 and self._current_level != "minimal":
            self._current_level = "minimal"
        elif self._unanswered_count >= 3 and self._current_level == "normal":
            self._current_level = "low"

    @property
    def configured_max_daily(self) -> int:
        """配置面的每日上限（不随当前档位变化）——scheduler 幂等比较的真值。

        ``get_max_daily`` 是「今天实际生效值」（会随档位下降）；拿它和
        normal 上限比较会让 adaptive 在 low/minimal 档**每拍都判定不一致**，
        进而每拍重建适配器。配置面读数必须是稳定的 normal/override 上限。
        """
        value = self.override_max_daily if self.override_max_daily is not None else self.normal_daily
        return max(0, int(value))

    def set_max_daily(self, max_daily: int) -> None:
        """更新每日上限（normal + 全档位硬上限），**保留**降档状态。

        旧 ``apply_runtime_config`` 用「新建 FrequencyAdapter + from_dict」更新
        normal_daily；本方法只改字段，对象身份与 ``_current_level`` /
        ``_unanswered_count`` 全保留，从根上消除每拍重建。
        """
        value = max(0, int(max_daily))
        self.normal_daily = value
        self.override_max_daily = value

    def get_max_daily(self) -> int:
        if self._current_level == "minimal":
            base = self.min_weekly
        elif self._current_level == "low":
            base = self.low_daily
        else:
            base = self.normal_daily
        if self.override_max_daily is not None:
            return max(0, min(int(base), int(self.override_max_daily)))
        return max(0, int(base))

    @property
    def level(self) -> str:
        return self._current_level

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_level": self._current_level,
            "unanswered_count": self._unanswered_count,
            "normal_daily": self.normal_daily,
            "low_daily": self.low_daily,
            "min_weekly": self.min_weekly,
            "override_max_daily": self.override_max_daily,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._current_level = data.get("current_level", self._current_level)
        self._unanswered_count = data.get("unanswered_count", self._unanswered_count)
        self.normal_daily = data.get("normal_daily", self.normal_daily)
        self.low_daily = data.get("low_daily", self.low_daily)
        self.min_weekly = data.get("min_weekly", self.min_weekly)
        if "override_max_daily" in data:
            self.override_max_daily = data.get("override_max_daily")
        else:
            # 旧状态文件无此键：上限跟 normal 走（旧实现只有 normal 是配置值）
            self.override_max_daily = self.normal_daily


class FrequencyController:
    def __init__(
        self,
        max_daily: int = 8,
        min_interval_minutes: int = 30,
        cooldown_after_reply_minutes: int = 10,
    ):
        self.max_daily = max_daily
        self.min_interval = timedelta(minutes=min_interval_minutes)
        self.cooldown = timedelta(minutes=cooldown_after_reply_minutes)

        self._daily_count = 0
        self._last_sent_time: datetime | None = None
        self._last_reply_time: datetime | None = None
        self._last_reset_date: datetime | None = None

    def can_send(self) -> tuple[bool, str]:
        now = datetime.now(tz=timezone.utc)
        # 日界必须按本地墙钟（与 ASE `_rollover_if_new_day` 同源）：
        # 原用 UTC date，在 UTC+8 部署下配额实际到本地 08:00 才归零。
        today = now_local().date()
        if self._last_reset_date is None or today != self._last_reset_date:
            self._daily_count = 0
            self._last_reset_date = today  # type: ignore[assignment]

        if self._daily_count >= self.max_daily:
            return False, "daily_limit"
        # 冷却/最小间隔是时间差运算，统一用 UTC aware（勿混用 naive 本地）
        if self._last_sent_time and now - self._last_sent_time < self.min_interval:
            return False, "min_interval"
        if self._last_reply_time and now - self._last_reply_time < self.cooldown:
            return False, "cooldown"
        return True, "ok"

    def record_sent(self) -> None:
        self._daily_count += 1
        self._last_sent_time = datetime.now(tz=timezone.utc)
        # 记账时钉住今日日界，保证 to_dict 落盘后 from_dict 不会把计数清掉
        if self._last_reset_date is None:
            self._last_reset_date = now_local().date()  # type: ignore[assignment]

    def record_reply(self) -> None:
        self._last_reply_time = datetime.now(tz=timezone.utc)

    def get_state(self) -> dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "remaining": self.max_daily - self._daily_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_count": self._daily_count,
            "max_daily": self.max_daily,
            "min_interval_minutes": self.min_interval.total_seconds() / 60,
            "cooldown_minutes": self.cooldown.total_seconds() / 60,
            "last_sent_time": self._last_sent_time.isoformat() if self._last_sent_time else None,
            "last_reply_time": self._last_reply_time.isoformat() if self._last_reply_time else None,
            # 日界日期必须随状态落盘：旧实现缺此字段，from_dict 后首次 can_send
            # 见 _last_reset_date=None 会把已恢复的 daily_count 整段清零（配额重置）。
            "last_reset_date": self._last_reset_date.isoformat() if self._last_reset_date else None,
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        self._daily_count = data.get("daily_count", self._daily_count)
        self.max_daily = data.get("max_daily", self.max_daily)
        if "min_interval_minutes" in data:
            self.min_interval = timedelta(minutes=data["min_interval_minutes"])
        if "cooldown_minutes" in data:
            self.cooldown = timedelta(minutes=data["cooldown_minutes"])
        if data.get("last_sent_time"):
            self._last_sent_time = datetime.fromisoformat(data["last_sent_time"])
        if data.get("last_reply_time"):
            self._last_reply_time = datetime.fromisoformat(data["last_reply_time"])
        if data.get("last_reset_date"):
            self._last_reset_date = datetime.fromisoformat(data["last_reset_date"]).date()  # type: ignore[assignment]
        elif self._last_reset_date is None and self._daily_count:
            # 无日界字段但有计数：钉在今天，避免下一拍 can_send 直接清零
            self._last_reset_date = now_local().date()  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════
#  零 token 骰子闸（dsh-speak-up，2026-10-28）
#
#  纯函数 + 配置 owner 都在本模块：引擎（`proactive/ase_engine.py`）只做
#  状态记账与账本写入，判定逻辑保持无副作用、可单测、可复现（注入 rng）。
# ═══════════════════════════════════════════════════════════════

#: 命中线总上限（百分比）。无论加成多少，单次命中概率不超过 100%。
DICE_LINE_MAX = 100.0

#: 默认值即「本批行为契约」：默认**关闭**、开启后默认**干跑**（只记账不拦截）。
DICE_GATE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "dry_run": True,
    "peak_line": 20,
    "offpeak_line": 60,
    "miss_bonus": 10.0,
    "miss_bonus_cap": 40.0,
    "idle_short_minutes": 20,
    "idle_long_minutes": 360,
    "idle_short_factor": 0.15,
    "idle_long_factor": 1.5,
}

#: 越界即判非法：读侧回落默认，写侧抛 ValueError（API → 400 / pydantic → 422）。
DICE_GATE_BOUNDS: dict[str, tuple[float, float]] = {
    "peak_line": (0.0, 100.0),
    "offpeak_line": (0.0, 100.0),
    "miss_bonus": (0.0, 40.0),
    # 连没中加成上限（与命中线总上限 DICE_LINE_MAX 不同：一个管加成，
    # 一个管最终概率；两者不可互相替代——旧草案曾都叫 cap 导致口径混淆）。
    "miss_bonus_cap": (0.0, 40.0),
    "idle_short_minutes": (0.0, 1440.0),
    "idle_long_minutes": (1.0, 10080.0),
    "idle_short_factor": (0.0, 1.0),
    "idle_long_factor": (1.0, 10.0),
}

#: 真闸是否已接入生产决策链（`proactive/scheduler.py` 一次性例外 hook）。
#: 若该例外不成立而回退只读分支，这里置 False —— 管理端据此显示「当前不生效」。
DICE_GATE_PRODUCTION_WIRED = True


def _config_path() -> Path:
    """配置真源路径（`data/scheduler_config.json`）。

    **函数内 import**：`proactive.scheduler` → `proactive.ase_engine` →
    `proactive.frequency` 已是既有导入链，模块级反向 import 会在半初始化
    阶段成环；且该 class 属性由 `tests/conftest.py` 重定向到沙箱，必须
    每次读当前值（不得在模块 import 时固化）。
    """
    from proactive.scheduler import ProactiveScheduler

    return Path(ProactiveScheduler._CONFIG_PATH)


def empty_dice_gate_state() -> dict[str, Any]:
    """双桶状态的空态（dry=干跑观测 / live=真拦截事实）。

    为什么分两桶：干跑期间如果把「会拦」也算进 live 的 miss_streak，切到
    live 时会凭空带着一串「从未真正发生过的连没中」加成，首次真拦截的概率
    就被污染了。两桶各记各的，互不影响。
    """
    return {
        "dry": {"rolls": 0, "would_send": 0, "would_skip": 0, "miss_streak": 0, "last": None},
        "live": {"rolls": 0, "hits": 0, "skips": 0, "miss_streak": 0, "last": None},
    }


def is_peak_time(now: datetime) -> bool:
    """是否峰时：周一至周五 09:00–12:00 或 14:00–18:00（左闭右开）。

    峰时（工作时段）压低头线 = 更克制；周末与夜间走谷时线 = 更愿意开口。
    """
    weekday = now.weekday()  # 0=周一 … 6=周日
    if weekday >= 5:
        return False
    hour = now.hour
    return (9 <= hour < 12) or (14 <= hour < 18)


def dice_timing_factor(
    idle_minutes: float,
    *,
    idle_short_minutes: float = 20.0,
    idle_long_minutes: float = 360.0,
    idle_short_factor: float = 0.15,
    idle_long_factor: float = 1.5,
) -> float:
    """空窗时长对命中线的乘数：刚聊完压低，久未互动抬升。

    边界值（==20 / ==360）取 1.0（「不足 20 分钟」才压），避免边界归属歧义。
    """
    try:
        idle = float(idle_minutes)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(idle):
        return 1.0
    if idle < float(idle_short_minutes):
        return float(idle_short_factor)
    if idle > float(idle_long_minutes):
        return float(idle_long_factor)
    return 1.0


def dice_base_line(now: datetime, *, peak_line: float = 20.0, offpeak_line: float = 60.0) -> float:
    """峰/谷基础命中线（百分比）。"""
    return float(peak_line) if is_peak_time(now) else float(offpeak_line)


def dice_hit_line(
    *,
    base_line: float,
    timing_factor: float,
    miss_streak: int,
    miss_bonus: float = 10.0,
    miss_bonus_cap: float = 40.0,
) -> float:
    """单次命中线 = min(100, 基础线×时机因子 + min(加成上限, 连没中×每次加成))。"""
    try:
        streak = max(0, int(miss_streak or 0))
    except (TypeError, ValueError):
        streak = 0
    bonus = min(float(miss_bonus_cap), streak * float(miss_bonus))
    line = float(base_line) * float(timing_factor) + max(0.0, bonus)
    return min(DICE_LINE_MAX, line)


def roll_percent(rng: Callable[[], float] = random.random) -> float:
    """掷一次，返回 [0, 100] 的百分点。

    fail-safe：rng 抛异常或返回非有限值 → 返回 100（必然 ≥ 任何命中线，
    即「本轮不通过」）。真闸外层还有一次 try/except 开闸兜底——骰子坏了
    宁可放过，不能永久堵死主动链。
    """
    try:
        value = float(rng())
    except Exception:  # noqa: BLE001
        return 100.0
    if not math.isfinite(value):
        return 100.0
    return max(0.0, min(100.0, value * 100.0))


@dataclass(frozen=True)
class DiceRollResult:
    """一次掷骰的完整取证（账本 payload 与前端统计同源）。"""

    hit: bool
    roll: float
    line: float
    peak: bool
    base_line: float
    timing_factor: float
    idle_minutes: float
    streak_before: int
    next_streak: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "hit": self.hit,
            "roll": round(self.roll, 2),
            "line": round(self.line, 2),
            "peak": self.peak,
            "base_line": round(self.base_line, 2),
            "timing_factor": round(self.timing_factor, 3),
            "idle_minutes": round(self.idle_minutes, 1),
            "streak_before": self.streak_before,
            "next_streak": self.next_streak,
        }


def evaluate_dice_roll(
    *,
    now: datetime,
    idle_minutes: float,
    miss_streak: int,
    config: dict[str, Any] | None = None,
    rng: Callable[[], float] = random.random,
) -> DiceRollResult:
    """纯判定：给定时钟/空窗/连没中与配置，返回本轮是否命中（可开口）。

    命中（``hit=True``）→ 连没中清零；未中 → 连没中 +1（下一轮加成）。
    """
    cfg = normalize_dice_gate_config(config) if config is not None else dict(DICE_GATE_DEFAULTS)
    streak = max(0, int(miss_streak or 0))
    peak = is_peak_time(now)
    base = dice_base_line(
        now, peak_line=cfg["peak_line"], offpeak_line=cfg["offpeak_line"],
    )
    factor = dice_timing_factor(
        idle_minutes,
        idle_short_minutes=cfg["idle_short_minutes"],
        idle_long_minutes=cfg["idle_long_minutes"],
        idle_short_factor=cfg["idle_short_factor"],
        idle_long_factor=cfg["idle_long_factor"],
    )
    line = dice_hit_line(
        base_line=base,
        timing_factor=factor,
        miss_streak=streak,
        miss_bonus=cfg["miss_bonus"],
        miss_bonus_cap=cfg["miss_bonus_cap"],
    )
    roll = roll_percent(rng)
    hit = roll < line
    return DiceRollResult(
        hit=hit,
        roll=roll,
        line=line,
        peak=peak,
        base_line=base,
        timing_factor=factor,
        idle_minutes=float(idle_minutes or 0.0),
        streak_before=streak,
        next_streak=0 if hit else streak + 1,
    )


def normalize_dice_gate_config(raw: Any) -> dict[str, Any]:
    """逐字段容错归一：类型不符/越界回落默认；跨字段倒置两侧一并回落。

    读侧**不抛异常**（坏配置不能拖垮主动链），写侧另行严格校验（见
    :func:`write_dice_gate_config`）。
    """
    block = raw if isinstance(raw, dict) else {}
    cfg: dict[str, Any] = {}
    for key, default in DICE_GATE_DEFAULTS.items():
        value = block.get(key)
        if isinstance(default, bool):
            # 布尔键（enabled/dry_run）在 DICE_GATE_BOUNDS 中没有条目——
            # 旧实现无差别查表直接 KeyError: 'enabled'，异常被
            # _read_dice_gate_config_safe / evaluate_dice_gate 的 fail-open
            # 双层吞掉，骰子闸在生产完全惰性（永远放行且 dry 桶不计数）。
            # 布尔只需类型校验，无边界可查。
            cfg[key] = value if isinstance(value, bool) else default
            continue
        if isinstance(default, int):
            cfg[key] = value if isinstance(value, int) and not isinstance(value, bool) else default
        else:
            cfg[key] = (
                float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else default
            )
        lo, hi = DICE_GATE_BOUNDS[key]
        if not (lo <= float(cfg[key]) <= hi):
            cfg[key] = default
    if cfg["idle_long_minutes"] <= cfg["idle_short_minutes"]:
        logger.warning("[dice_gate][step=config] idle_window_inversion fallback")
        cfg["idle_short_minutes"] = DICE_GATE_DEFAULTS["idle_short_minutes"]
        cfg["idle_long_minutes"] = DICE_GATE_DEFAULTS["idle_long_minutes"]
    return cfg


def read_dice_gate_config() -> dict[str, Any]:
    """热读 ``scheduler_config.json`` 的 ``dice_gate`` 块（无进程缓存）。"""
    raw = json_state.read_json(_config_path(), default={})
    block = raw.get("dice_gate") if isinstance(raw, dict) else None
    return normalize_dice_gate_config(block)


def write_dice_gate_config(**partial: Any) -> dict[str, Any]:
    """partial merge 写入 ``dice_gate`` 块；未知字段/类型非法/越界 → ``ValueError``。

    只触碰 ``dice_gate`` 键，其余顶层键（reply_mode / quiet_hours /
    llm_proactive / humanize / topic_state …）逐字保留。读改写全程在
    ``utils.json_state.update_json`` 的锁内完成。
    """
    payload = {k: v for k, v in partial.items() if v is not None}
    unknown = sorted(set(payload) - set(DICE_GATE_DEFAULTS))
    if unknown:
        raise ValueError(f"未知的 dice_gate 字段: {unknown}")
    for key, value in payload.items():
        default = DICE_GATE_DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须是 bool，收到 {type(value).__name__}")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数字，收到 {type(value).__name__}")
        if isinstance(default, int):
            if not isinstance(value, int):
                raise ValueError(f"{key} 必须是整数，收到 {type(value).__name__}")
        else:
            payload[key] = float(value)
        lo, hi = DICE_GATE_BOUNDS[key]
        if not (lo <= float(value) <= hi):
            raise ValueError(f"{key} 越界: {value!r}（允许 {lo}..{hi}）")
    merged = {**read_dice_gate_config(), **payload}
    if merged["idle_long_minutes"] <= merged["idle_short_minutes"]:
        raise ValueError(
            "idle_long_minutes 必须大于 idle_short_minutes"
            f"（当前 {merged['idle_long_minutes']} <= {merged['idle_short_minutes']}）"
        )

    def _mutate(data: dict[str, Any]) -> None:
        data["dice_gate"] = {k: merged[k] for k in DICE_GATE_DEFAULTS}

    json_state.update_json(_config_path(), _mutate)
    return read_dice_gate_config()
