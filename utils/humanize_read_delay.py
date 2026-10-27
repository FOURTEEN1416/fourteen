"""阅读延迟纯函数（P0-B，2026-10-27）。

对齐 DSH（DeepSeek Harness 生态插件）send-delay 语义，见
``docs/reports/2026-10-27-DSH专项调研.md`` §2.2.3/§2.2.4 与本仓设计
``docs/reports/2026-10-27-仿真人化引入方案.md`` §3.2：

- **base（活跃响应曲线）**：``idle ≤ 60s`` → 下界；``60s–300s`` 线性过渡；
  ``≥ 300s`` → 完整区间随机（「隔久了才看到，回得慢一点」）；
- **reading（阅读耗时）**：``可见字数 / chars_per_second × 1000``（0 = 关闭）；
- ``result = min(base + reading, max_total, channel_cap)``；
- **无 typing 能力的渠道**：``channel_cap = min(配置值, 5000ms)`` —— 没有
  「正在输入」解释这段沉默时，长延迟会被感知为「变卡」。

纯函数：无 IO、无进程内状态；随机源可注入（``rng``），边界测试可完全确定。
任何异常都不得影响主链——调用方负责 try/except 放行。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

#: 活跃响应曲线拐点：60s 内视为「刚聊完」，300s 外视为「隔久了」。
ACTIVITY_FAST_IDLE_MS = 60_000
ACTIVITY_FULL_IDLE_MS = 300_000

#: 无 typing 指示器时的硬封顶（没有沉默解释，延迟超过 5s 会被感知为变卡）。
NO_TYPING_CAP_MS = 5_000

Rng = Callable[[float, float], float]


def activity_base_ms(
    idle_ms: int | None,
    min_ms: int,
    max_ms: int,
    *,
    rng: Rng = random.uniform,
) -> int | None:
    """活跃响应曲线的 base 延迟；``idle_ms is None``（首条消息）返回 ``None``。

    ``None`` 表示「没有上一条可参照」→ 调用方回落到完整区间随机
    （见 :func:`compute_read_delay_ms`）。
    """
    if idle_ms is None:
        return None
    lo, hi = (int(min_ms), int(max_ms))
    if lo > hi:
        lo, hi = hi, lo
    idle = max(0, int(idle_ms))
    if idle <= ACTIVITY_FAST_IDLE_MS:
        return lo
    if idle >= ACTIVITY_FULL_IDLE_MS:
        return int(round(rng(lo, hi)))
    ratio = (idle - ACTIVITY_FAST_IDLE_MS) / float(ACTIVITY_FULL_IDLE_MS - ACTIVITY_FAST_IDLE_MS)
    return int(round(lo + ratio * (hi - lo)))


def compute_read_delay_ms(
    idle_ms: int | None,
    text: str,
    cfg: Any,
    *,
    typing_available: bool = True,
    rng: Rng = random.uniform,
) -> int:
    """计算本轮生成前的阅读延迟毫秒数（纯函数，0 = 不延迟）。

    Args:
        idle_ms: 距会话上一条消息的毫秒数；``None`` = 无参照（首条/历史缺失）。
        text: 用户本轮消息（只用于按字数估算阅读耗时）。
        cfg: 生效中的 humanize 配置（``read_delay_*`` 字段）。
        typing_available: 该渠道是否有 typing 指示能力（无则 5s 硬封顶）。
        rng: 随机源（测试注入）。
    """
    if not getattr(cfg, "read_delay_enabled", False):
        return 0

    lo = int(getattr(cfg, "read_delay_min_ms", 1000))
    hi = int(getattr(cfg, "read_delay_max_ms", 6000))
    if lo > hi:
        lo, hi = hi, lo

    base = activity_base_ms(idle_ms, lo, hi, rng=rng)
    if base is None:
        base = int(round(rng(lo, hi)))

    visible = len(str(text or "").strip())
    cps = int(getattr(cfg, "read_delay_chars_per_second", 0))
    reading = int(round(visible / cps * 1000)) if cps > 0 else 0

    total = base + reading
    cap = int(getattr(cfg, "read_delay_max_total_ms", 30000))
    if not typing_available:
        channel_cap = int(getattr(cfg, "read_delay_channel_cap_ms", NO_TYPING_CAP_MS))
        if channel_cap <= 0:
            channel_cap = NO_TYPING_CAP_MS
        cap = min(cap, channel_cap, NO_TYPING_CAP_MS)
    return max(0, min(total, cap))
