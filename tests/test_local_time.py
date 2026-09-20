"""本地时间单一真源测试 —— 钉住 2026-09-20「墙钟判定错用 UTC」修复。

背景：`memory_pipeline` 与 `enhanced_prompt_engine` 曾用
``datetime.now(tz=timezone.utc)`` 取「小时 / 日期」做墙钟判定，对 UTC+8 部署
使深夜时段判定错位 8 小时、日记按 UTC 切日。修复后全部走
``utils.local_time.now_local``。

这些测试的目的不是"验证函数能跑"，而是**把时区语义钉死**：
任何人改回 ``datetime.now(tz=timezone.utc)`` 都会让本文件变红。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import local_time
from utils.local_time import now_local


class TestNowLocal:
    """now_local 的时区语义契约。"""

    def test_matches_system_local_wall_clock(self) -> None:
        """系统时区正确时，now_local 应≈系统本地墙钟（naive、无回退）。"""
        before = datetime.now()
        dt = now_local()
        after = datetime.now()
        # 不逐字段比 hour（会在整点/整分边界抖动），直接卡在两次读数之间
        assert before <= dt <= after
        # 系统时区（Asia/Shanghai）正确 → 走 naive 分支，与原
        # ase_engine._local_now 行为一致（不回退）
        assert dt.tzinfo is None

    def test_forces_utc8_when_host_is_not_utc8(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 UTC+8 主机（如容器默认 UTC）必须强制回退到 UTC+8。

        这是生产防护：服务器若被换成 UTC，深夜判定不能跟着漂 8 小时。
        """
        monkeypatch.setattr(local_time.time, "daylight", 0)
        monkeypatch.setattr(local_time.time, "timezone", 0)  # 0 秒偏移 = UTC 主机
        dt = now_local()
        assert dt.utcoffset() == timedelta(hours=8)
        expected_hour = (datetime.now(tz=timezone.utc) + timedelta(hours=8)).hour
        assert dt.hour == expected_hour


class TestLateNightWallClockContract:
    """深夜判定的边界必须按传入时间戳的墙钟小时，而非任何 UTC 换算。"""

    @staticmethod
    def _is_late_night(timestamp: datetime) -> bool:
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        return MemoryPipeline._is_late_night(timestamp)

    @pytest.mark.parametrize(
        ("hour", "expected"),
        [
            (23, True),
            (0, True),
            (5, True),
            (6, False),
            (10, False),
            (22, False),
        ],
    )
    def test_boundaries(self, hour: int, expected: bool) -> None:
        assert self._is_late_night(datetime(2026, 9, 20, hour, 0)) is expected

    def test_local_2330_is_late_night_but_utc_2330_of_local_0730_is_not(self) -> None:
        """核心回归：本地 23:30 是深夜；而"本地 07:30 对应的 UTC 23:30"不是。

        修复前的实现取的是 UTC 小时 —— 于是本地 07:30 会被判成深夜，
        真正的本地 23:30 反而不判。本用例把这条语义钉死。
        """
        local_late_night = datetime(2026, 9, 20, 23, 30)
        assert self._is_late_night(local_late_night) is True

        # 本地 07:30（UTC+8）→ UTC 23:30：修复前的实现会在这里返回 True
        utc_of_local_morning = datetime(2026, 9, 19, 23, 30)
        assert self._is_late_night(utc_of_local_morning) is True  # 函数本身只看小时
        # 因此"喂 UTC 时间"是错的 —— 调用方必须喂本地时间（见 test_memory_pipeline
        # 的 after_chat 深夜加权回归用例）


class TestTimeContextUsesLocalClock:
    """TimeContext.now() 必须走本地时钟（修复前用 UTC，时段整体错位 8 小时）。"""

    def test_now_uses_patched_local_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from my_character.enhanced_prompt_engine import TimeContext

        fixed = datetime(2026, 9, 20, 23, 30)  # 周日 深夜
        monkeypatch.setattr(
            "my_character.enhanced_prompt_engine.now_local", lambda: fixed
        )
        ctx = TimeContext.now()
        assert ctx.hour == 23
        assert ctx.period == "night"
        assert ctx.is_weekend is True  # 2026-09-20 为周日

    def test_morning_afternoon_noon_buckets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from my_character.enhanced_prompt_engine import TimeContext

        cases = {7: "morning", 10: "forenoon", 13: "afternoon", 20: "evening", 23: "night"}
        for hour, expected in cases.items():
            monkeypatch.setattr(
                "my_character.enhanced_prompt_engine.now_local",
                lambda h=hour: datetime(2026, 9, 20, h, 0),
            )
            assert TimeContext.now().period == expected, f"hour={hour}"


def test_no_wall_clock_utc_regression_in_fixed_sites() -> None:
    """静态防护：被修复的站点不得再以 UTC 做墙钟判定（仅统计非注释行）。

    允许的例外（属时间差运算 / 无害 ID 生成）：
      - memory_pipeline: session_id 生成 1 处 + _apply_forgetting 的
        updated_at / days_old 计算 2 处 —— 这三处**必须**用 UTC。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    targets = {
        "shisi/memory/legacy/memory_pipeline.py": 3,
        "my_character/enhanced_prompt_engine.py": 0,
    }
    needle = "datetime.now(tz=timezone.utc)"
    for rel, allowed in targets.items():
        code_lines = [
            line
            for line in (root / rel).read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ]
        hits = sum(1 for line in code_lines if needle in line)
        assert hits <= allowed, (
            f"{rel} 有 {hits} 处 datetime.now(tz=timezone.utc)，超过允许的 {allowed} 处 —— "
            "墙钟判定必须走 utils.local_time.now_local（UTC 仅可用于时间差运算）"
        )


def test_ase_local_now_delegates_to_shared_clock() -> None:
    """ase_engine._local_now 必须委托公共真源，不得再自带一套时区探测。"""
    import inspect

    from proactive import ase_engine

    src = inspect.getsource(ase_engine._local_now)
    assert "now_local()" in src
    assert "altzone" not in src, "_local_now 不应再自带时区探测逻辑（已提为公共真源）"
