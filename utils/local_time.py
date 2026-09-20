"""本地时间单一真源 —— 凡按「墙钟语义」（现在几点 / 今天是哪天）判定的一律走这里。

**背景（2026-09-20 修复）**：`shisi/memory/legacy/memory_pipeline.py` 与
`my_character/enhanced_prompt_engine.py` 曾用 ``datetime.now(tz=timezone.utc)``
取「小时 / 日期」做墙钟判定。对 UTC+8 部署（生产服务器 TZ=Asia/Beijing）后果：

- 深夜时段判定错位 8 小时 —— ``_is_late_night``（23:00–05:00）实际在
  **本地 07:00–13:59** 触发，真正的本地深夜反而不触发，
  "深夜情感词 → 强制存事实 + 重要性 +0.3"完全落到上午/中午；
- 日记 / 维护按 UTC 切日 —— 本地 00:00–08:00 的消息被归入前一天。

本实现的逻辑原在 ``proactive/ase_engine._local_now``（全项目唯一正确的一处，
``proactive/scheduler.py`` 已明确与之共用）。本次提为公共真源，避免"多套时钟"。

**使用纪律**：只用于墙钟字段（``hour`` / ``date()`` / ``weekday()`` /
``strftime``）。**不要**拿它做跨时区的时间差运算 —— 历史时间戳的
``updated_at`` 等落库/比较路径仍应统一用 UTC，混用会算错经过时长。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# UTC+8 的秒级偏移（北京时区）
_UTC8_OFFSET_SECONDS = 8 * 3600
# 偏差容忍：超过 1 小时即认为系统时区不是北京时区
_UTC8_TOLERANCE_SECONDS = 3600


def now_local() -> datetime:
    """返回当前本地时间（北京时区语义）。

    优先用系统本地时间（服务器应配置 Asia/Shanghai）；若系统时区非 UTC+8
    （如容器内默认 UTC），强制回退到 UTC+8，使墙钟判定不随部署环境漂移。

    返回值形态与原 ``ase_engine._local_now`` 完全一致：系统时区正确时返回
    **naive** datetime，回退分支返回 **aware**（UTC+8）datetime —— 调用方因此
    必须只读墙钟字段，不得与历史时间戳做跨时区算术。
    """
    # 系统时区偏移（秒）：夏令时生效时用 altzone
    offset_sec = (
        -time.altzone if time.daylight and time.localtime().tm_isdst else -time.timezone
    )
    if abs(offset_sec - _UTC8_OFFSET_SECONDS) > _UTC8_TOLERANCE_SECONDS:
        return datetime.now(tz=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    return datetime.now()
