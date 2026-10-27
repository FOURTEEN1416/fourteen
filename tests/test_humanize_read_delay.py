"""阅读延迟纯函数（P0-B）边界回归。

覆盖设计《2026-10-27-仿真人化引入方案》§3.2 P0-B：

- 活跃响应曲线拐点：``idle≤60s`` → 下界；``60–300s`` 线性；``≥300s`` 区间随机；
- 首条消息（``idle=None``）→ 完整区间随机；
- 阅读耗时按字数/阅读速度累加（0 = 关闭）；
- ``max_total`` 总上限；**无 typing 渠道** 5s 硬封顶（取配置与 5000 的较小者）；
- ``read_delay_enabled=false`` → 恒 0（回滚开关）。

纯函数测试：随机源全部注入，结果完全确定，不依赖墙钟。
"""

from __future__ import annotations

from utils.humanize_config import HumanizeConfig
from utils.humanize_read_delay import (
    ACTIVITY_FAST_IDLE_MS,
    ACTIVITY_FULL_IDLE_MS,
    NO_TYPING_CAP_MS,
    activity_base_ms,
    compute_read_delay_ms,
)


def _cfg(**kw) -> HumanizeConfig:
    base = {
        "read_delay_enabled": True,
        "read_delay_min_ms": 1000,
        "read_delay_max_ms": 6000,
        "read_delay_chars_per_second": 0,
        "read_delay_max_total_ms": 30000,
        "read_delay_channel_cap_ms": 5000,
        "typing_enabled": True,
    }
    base.update(kw)
    return HumanizeConfig(**base)


def test_disabled_returns_zero():
    assert compute_read_delay_ms(0, "你好", _cfg(read_delay_enabled=False)) == 0


def test_activity_curve_boundaries():
    assert activity_base_ms(0, 1000, 6000) == 1000
    assert activity_base_ms(ACTIVITY_FAST_IDLE_MS, 1000, 6000) == 1000
    mid = activity_base_ms(180_000, 1000, 6000)
    assert 1000 < mid < 6000, "60–300s 应为线性过渡"
    assert activity_base_ms(ACTIVITY_FULL_IDLE_MS, 1000, 6000, rng=lambda a, b: b) == 6000
    assert activity_base_ms(ACTIVITY_FULL_IDLE_MS, 1000, 6000, rng=lambda a, b: a) == 1000
    assert activity_base_ms(None, 1000, 6000) is None


def test_first_message_uses_full_range():
    """无上一条可参照 → 完整区间随机（注入上界）。"""
    assert compute_read_delay_ms(None, "hi", _cfg(), rng=lambda a, b: b) == 6000
    assert compute_read_delay_ms(None, "hi", _cfg(), rng=lambda a, b: a) == 1000


def test_reading_time_scales_with_length():
    cfg = _cfg(read_delay_min_ms=1000, read_delay_max_ms=1000, read_delay_chars_per_second=10)
    # 10 字 / 10 字每秒 = 1000ms 阅读耗时
    assert compute_read_delay_ms(0, "一" * 10, cfg) == 2000
    assert compute_read_delay_ms(0, "", cfg) == 1000


def test_reading_time_disabled_by_zero():
    cfg = _cfg(read_delay_min_ms=1000, read_delay_max_ms=1000, read_delay_chars_per_second=0)
    assert compute_read_delay_ms(0, "一" * 500, cfg) == 1000


def test_max_total_cap():
    cfg = _cfg(
        read_delay_min_ms=1000,
        read_delay_max_ms=1000,
        read_delay_chars_per_second=10,
        read_delay_max_total_ms=1200,
    )
    assert compute_read_delay_ms(0, "一" * 500, cfg) == 1200


def test_no_typing_channel_capped_at_5s():
    cfg = _cfg(
        read_delay_min_ms=1000,
        read_delay_max_ms=1000,
        read_delay_chars_per_second=10,
        read_delay_max_total_ms=30000,
    )
    assert compute_read_delay_ms(0, "一" * 500, cfg, typing_available=False) == NO_TYPING_CAP_MS


def test_no_typing_cap_respects_smaller_config_cap():
    cfg = _cfg(
        read_delay_min_ms=1000,
        read_delay_max_ms=1000,
        read_delay_chars_per_second=10,
        read_delay_channel_cap_ms=2000,
    )
    assert compute_read_delay_ms(0, "一" * 500, cfg, typing_available=False) == 2000


def test_typing_available_has_no_5s_cap():
    cfg = _cfg(read_delay_min_ms=2000, read_delay_max_ms=2000)
    assert compute_read_delay_ms(0, "hi", cfg, typing_available=True) == 2000


def test_inverted_bounds_are_healed():
    cfg = _cfg(read_delay_min_ms=6000, read_delay_max_ms=1000)
    assert compute_read_delay_ms(0, "hi", cfg) == 1000
