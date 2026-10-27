"""沉浸式可选口误（Q4，2026-10-27 用户裁决：仅沉浸模式可选，默认关闭）。

设计取舍：**不追求高频错别字模拟**（研究反例：完整打字/错字模拟会损害信任，
见 ``docs/reports/2026-10-27-DSH专项调研.md`` §4.4 与设计 §4 第 5 条）。
只在用户显式开启（``typo_rate > 0``，单位 %）时，以极低概率对一条回复做
**一次相邻汉字换位**：

- 长度不变、只交换两个**汉字**，不碰数字/英文/标点（信息不丢）；
- 每条回复最多一处（避免整句失真）；
- 调用方保证只在 **immersive** 模式应用（小说式有画面感，不需要这种瑕疵）。

默认 ``typo_rate=0``（关闭）；用户裁决要求「可选」而非默认开启。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

Rng = Callable[[], float]


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def apply_typo_once(text: str, *, rng: Rng = random.random) -> str:
    """在可用位置中随机选一处做相邻汉字换位；无可用位置时原样返回。"""
    chars = list(text or "")
    positions = [
        i for i in range(1, len(chars))
        if _is_han(chars[i - 1]) and _is_han(chars[i]) and chars[i - 1] != chars[i]
    ]
    if not positions:
        return text
    idx = positions[int(rng() * len(positions)) % len(positions)]
    chars[idx - 1], chars[idx] = chars[idx], chars[idx - 1]
    return "".join(chars)


def maybe_apply_typo(text: str, cfg: Any, *, rng: Rng = random.random) -> str:
    """按 ``cfg.typo_rate``（百分比）决定是否应用一次口误；0 = 关闭。"""
    try:
        rate = int(getattr(cfg, "typo_rate", 0) or 0)
    except (TypeError, ValueError):
        return text
    if rate <= 0:
        return text
    if len(str(text or "")) < 8:
        return text
    if rng() * 100.0 >= rate:
        return text
    return apply_typo_once(text, rng=rng)
