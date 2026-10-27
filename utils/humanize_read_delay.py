"""阅读延迟纯函数（P0-B，2026-10-27）。

对齐 DSH（DeepSeek Harness 生态插件）send-delay 语义，见
``docs/reports/2026-10-27-DSH专项调研.md`` §2.2.3/§2.2.4 与本仓设计
``docs/reports/2026-10-27-仿真人化引入方案.md`` §3.2：

- **base（活跃响应曲线，按 DSH 源码四段实现）**：
  ``idle ≤ 60s`` → ``fastReplyMs``；``60s–120s`` → 从 ``fastReplyMs`` 线性升到
  ``min_ms``；``120s–300s`` → **保持 ``min_ms``（平台期）**；``≥300s`` →
  完整 ``uniform(min_ms, max_ms)``。加成后的 base **永不高于 ``min_ms``**
  （只缩短、不拉长；``fastReplyMs > min_ms`` 时截到 ``min_ms``）。
  来源：``docs/reports/2026-10-27-DSH专项调研.md`` §2.2.4
  （``send-delay.mjs:26-53,380-391``）；**2026-10-27 实施勘误**：初版误写成
  「60–300s 线性升到 max」，与源码不符，已按四段曲线修正。
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

#: 活跃响应曲线拐点（对齐 DSH 源码默认）：60s / 120s / 300s。
FAST_WINDOW_MS = 60_000
MIN_WINDOW_MS = 120_000
FULL_WINDOW_MS = 300_000

#: 刚聊完时的「秒回」基础值（仍受 min_ms 封顶——只缩短不拉长）。
FAST_REPLY_MS = 1_000

#: 无 typing 指示器时的硬封顶（没有沉默解释，延迟超过 5s 会被感知为变卡）。
NO_TYPING_CAP_MS = 5_000

Rng = Callable[[float, float], float]


def activity_base_ms(
    idle_ms: int | None,
    min_ms: int,
    max_ms: int,
    *,
    rng: Rng = random.uniform,
    fast_reply_ms: int = FAST_REPLY_MS,
) -> int | None:
    """活跃响应曲线的 base 延迟（四段；``idle_ms is None`` → ``None``）。

    - ``idle ≤ 60s`` → ``min(fast_reply_ms, min_ms)``
    - ``60–120s`` → 线性从 ``fast`` 升到 ``min_ms``
    - ``120–300s`` → 平台期 ``min_ms``
    - ``≥300s`` → ``uniform(min_ms, max_ms)``（「隔久了，回得慢一点」）

    ``None`` 表示「没有上一条可参照」→ 调用方回落完整区间随机
    （见 :func:`compute_read_delay_ms`）。
    """
    if idle_ms is None:
        return None
    lo, hi = (int(min_ms), int(max_ms))
    if lo > hi:
        lo, hi = hi, lo
    fast = min(int(fast_reply_ms), lo)  # 不变量：加成后永不高于 min_ms
    idle = max(0, int(idle_ms))
    if idle <= FAST_WINDOW_MS:
        return fast
    if idle >= FULL_WINDOW_MS:
        return int(round(rng(lo, hi)))
    if idle <= MIN_WINDOW_MS:
        ratio = (idle - FAST_WINDOW_MS) / float(MIN_WINDOW_MS - FAST_WINDOW_MS)
        return int(round(fast + ratio * (lo - fast)))
    return lo


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
