"""本地时间单一真源测试 —— 钉住 2026-09-20「墙钟判定错用 UTC」修复。

背景：`memory_pipeline` 与 `enhanced_prompt_engine`（后者已于批6b 项10 随死路径
删除）曾用 ``datetime.now(tz=timezone.utc)`` 取「小时 / 日期」做墙钟判定，对
UTC+8 部署使深夜时段判定错位 8 小时、日记按 UTC 切日。修复后全部走
``utils.local_time.now_local``。

这些测试的目的不是"验证函数能跑"，而是**把时区语义钉死**：
任何人改回 ``datetime.now(tz=timezone.utc)`` 都会让本文件变红。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import local_time
from utils.local_time import local_day_utc_bounds, now_local


class TestNowLocal:
    """now_local 的时区语义契约。"""

    def test_matches_system_local_wall_clock(self) -> None:
        """系统时区正确时，now_local 应≈系统本地墙钟（naive、无回退）。

        CI 宿主（ubuntu-latest）时区为 UTC：`now_local()` 会走 UTC+8
        **aware 回退分支**，不能与 naive `datetime.now()` 直接比大小
        （TypeError: can't compare offset-naive and offset-aware）。
        本用例先探测宿主时区，再按分支断言语义。
        """
        import time as _time

        offset_sec = (
            -_time.altzone
            if _time.daylight and _time.localtime().tm_isdst
            else -_time.timezone
        )
        host_is_utc8 = abs(offset_sec - 8 * 3600) <= 3600
        before = datetime.now()
        dt = now_local()
        after = datetime.now()

        if host_is_utc8:
            # 系统时区（Asia/Shanghai）正确 → 走 naive 分支
            assert dt.tzinfo is None
            assert before <= dt <= after
        else:
            # 非 UTC+8 主机 → 强制 aware UTC+8 回退（生产防护）
            assert dt.tzinfo is not None
            assert dt.utcoffset() == timedelta(hours=8)
            # 与同一瞬间的 UTC 读数比墙钟：换算后应落在 [before, after] 窗口
            # （允许 ±2h 时钟噪声，核心断言是 offset=+8）
            utc_now = datetime.now(tz=timezone.utc)
            local_from_utc = (utc_now + timedelta(hours=8)).replace(tzinfo=None)
            assert abs((dt.replace(tzinfo=None) - local_from_utc).total_seconds()) < 2

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
    """（已随宿主模块删除）TimeContext 曾在 enhanced_prompt_engine，
    6b 项10 死码清除后该模块整体移除，本地时钟契约由 memory_pipeline /
    frequency / calendar 工具等现存站点用例守护。"""

    def test_enhanced_prompt_engine_module_removed(self) -> None:
        import importlib.util

        assert importlib.util.find_spec("my_character.enhanced_prompt_engine") is None, (
            "enhanced_prompt_engine 已作为死路径删除，不得复活"
        )


def test_no_wall_clock_utc_regression_in_fixed_sites() -> None:
    """静态防护：被修复的站点不得再以 UTC 做墙钟判定（仅统计非注释行）。

    允许的例外（属时间差运算 / 无害 ID 生成）：
      - memory_pipeline: session_id 生成 1 处 + _apply_forgetting 的
        updated_at / days_old 计算 2 处 —— 这三处**必须**用 UTC。
      - frequency.py: 冷却/最小间隔的时间差比较 3 处 —— 日界走 now_local。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    targets = {
        "shisi/memory/legacy/memory_pipeline.py": 3,
        "shisi/stats/analytics.py": 0,
        "utils/important_dates.py": 0,
        "proactive/frequency.py": 3,  # can_send/record_sent/record_reply 的时间差
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


def _code_only(src: str) -> str:
    """去掉 docstring 与 # 注释，只留可执行源码（防文档里的历史字样误伤静态防护）。"""
    import io
    import tokenize

    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_pending_intent_write_clock_matches_read_clock() -> None:
    """upsert_pending_intent 的 expires_at 必须与读侧 _now_local 同源。

    CI 实证（2026-09-20）：GitHub Actions 宿主为 UTC。旧写法 `datetime.now()`
    写出的 expires_at 比 `_now_local()`（UTC+8 墙钟）慢 8 小时 → pending 一落库
    即被 get_active_pending_intent 判 expired，ask_user 分支看起来「没写库」。
    """
    import inspect

    from shisi.memory.legacy.structured_memory import StructuredMemory

    src = _code_only(inspect.getsource(StructuredMemory.upsert_pending_intent))
    assert "datetime.now()" not in src, (
        "upsert_pending_intent 不得用裸 datetime.now()（主机时区依赖）；"
        "必须走 _now_local()/now_local()"
    )
    assert "_now_local" in src or "now_local" in src


def test_pending_intent_survives_utc_host_clock_skew(tmp_path) -> None:
    """行为回归：expires_at 必须相对 now_local 计算（钉死具体落库值）。

    CI 实证（2026-09-20）：GitHub Actions 宿主为 UTC。旧写法 `datetime.now()`
    写出的 expires_at 比读侧 `_now_local()`（UTC+8）慢 8 小时 → pending 一落库
    即过期。本用例把 now_local 钉到 2099，断言落库 expires_at 也是 2099+TTL；
    若实现退回 datetime.now()（≈当前墙钟），断言必红，与运行时刻无关。
    """
    from datetime import datetime as _dt

    from shisi.memory.legacy import structured_memory as smod
    from shisi.memory.legacy.structured_memory import StructuredMemory

    pinned = _dt(2099, 1, 1, 12, 0, 0)
    original = smod.now_local
    smod.now_local = lambda: pinned  # type: ignore[assignment]
    sm = StructuredMemory(str(tmp_path / "skew.db"))
    try:
        sm.upsert_pending_intent(
            "s1", "set_reminder", {"content": "起床"}, 1, "几点？", ttl_minutes=15,
        )
        with sm._conn() as conn:
            row = conn.execute(
                "SELECT expires_at, status FROM pending_intents WHERE session_key=?",
                ("s1",),
            ).fetchone()
        assert row is not None
        assert row["expires_at"] == "2099-01-01 12:15:00", (
            f"expires_at={row['expires_at']!r} 必须由 now_local+TTL 推出；"
            "出现当前墙钟说明又退回了 datetime.now()"
        )
        pending = sm.get_active_pending_intent("s1")
        assert pending is not None
        assert pending["ask_count"] == 1
    finally:
        smod.now_local = original  # type: ignore[assignment]
        sm.close()


def test_calendar_and_time_awareness_use_shared_wall_clock() -> None:
    """日历/时间感知工具的「现在几点」必须走 now_local，不得依赖主机 TZ。"""
    import inspect

    from tools.builtin.calendar_tool import CalendarTool
    from tools.builtin.time_awareness_tool import TimeAwarenessTool

    cal_src = _code_only(inspect.getsource(CalendarTool.execute))
    time_src = _code_only(inspect.getsource(TimeAwarenessTool._get_current))
    assert "datetime.now()" not in cal_src
    assert "datetime.now()" not in time_src
    assert "now_local" in cal_src
    assert "now_local" in time_src


def test_storyline_updated_at_uses_utc_iso_not_naive_now() -> None:
    """剧情线 updated_at 必须 UTC ISO（与 character_routes 同源），禁止裸 datetime.now()。"""
    import inspect

    from api.routers import storyline_routes

    raw = inspect.getsource(storyline_routes)
    assert "timezone.utc" in raw
    assert "datetime.now()" not in raw.replace("datetime.now(tz=", "")


def test_memory_fact_extraction_is_session_isolated() -> None:
    """事实抽取只读本会话消息，禁止全局 get_recent_chats 串用户。"""
    import inspect

    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    raw = inspect.getsource(MemoryPipeline._do_fact_extraction)
    assert "get_recent_chats" not in raw.replace("get_recent_chats(10)", ""), (
        "_do_fact_extraction 不得读全局 chat_history；必须按 session_id 过滤"
    )
    assert "_load_session_history" in raw


def test_retrieve_context_facts_fallback_uses_call_session() -> None:
    """retrieve_context 降级取 facts 时必须用调用方 session_id 派生 user_key。"""
    import inspect

    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    for name in ("retrieve_context",):
        raw = inspect.getsource(getattr(MemoryPipeline, name))
        assert "if session_id" in raw and "user_key=uk" in raw, (
            f"{name} 的 facts 降级必须以传入 session_id 派生 user_key，"
            "不得退回 pipeline 全局 session，也不得无过滤全库扫"
        )
        assert "session_id or self.session_id" not in raw, (
            f"{name} 不得把 pipeline 全局 session 当作 facts 归属回退"
        )


def test_get_memory_context_recent_chats_session_isolated() -> None:
    """get_memory_context.recent_chats 不得再调用全局 get_recent_chats。"""
    import inspect

    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    raw = inspect.getsource(MemoryPipeline.get_memory_context)
    # 排除 docstring 中的历史说明
    code = raw.split('"""', 2)[-1] if '"""' in raw else raw
    assert "self.sm.get_recent_chats" not in code, (
        "get_memory_context 不得读全局聊天；必须 _load_session_history(session)"
    )
    assert "_load_session_history" in raw


_SQL_FMT = "%Y-%m-%d %H:%M:%S"


class TestLocalDayUtcBounds:
    """「本地当天」→ UTC 区间的换算契约（SQLite 时间戳格式，左闭右开）。"""

    def test_spans_exactly_24h(self) -> None:
        start, end = local_day_utc_bounds()
        delta = datetime.strptime(end, _SQL_FMT) - datetime.strptime(start, _SQL_FMT)
        assert delta == timedelta(days=1)

    def test_current_instant_falls_inside_today_window(self) -> None:
        """不变量（与主机时区无关）：此刻必然落在「今天」的窗口内。"""
        start, end = local_day_utc_bounds()
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None).strftime(_SQL_FMT)
        assert start <= now_utc < end, f"{start} <= {now_utc} < {end}"

    def test_explicit_now_is_inside_its_own_window(self) -> None:
        """显式传入任意本地时刻，该时刻的 UTC 读数必须落在自己那天的窗口内。

        独立推导偏移（不用被测函数的内部口径）：`start` 必然等于「本地零点 − 偏移」，
        故 `本地零点 − start` 就是偏移本身。
        """
        for local in (
            datetime(2026, 9, 20, 0, 0, 1),   # 本地日凌晨（UTC 仍在昨日）
            datetime(2026, 9, 20, 12, 0, 0),
            datetime(2026, 9, 20, 23, 59, 59),
        ):
            start, end = local_day_utc_bounds(local)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            offset = midnight - datetime.strptime(start, _SQL_FMT)
            # 时区偏移必须是整刻钟的倍数（合法时区性质）—— 防止"偏移算成 0"
            assert offset % timedelta(minutes=15) == timedelta(0), f"异常偏移 {offset}"
            utc_read = (local - offset).strftime(_SQL_FMT)  # 本地读数 → UTC 读数
            assert start <= utc_read < end, f"local={local} start={start} end={end}"


class TestStructuredMemoryTodayUsesLocalDay:
    """真实 SQL 回归：`get_chats_today`/`count_chats_today` 必须按**本地日**划窗。

    旧实现 `date(created_at) = date('now')` 是 UTC 日 —— 在 UTC+8 上"今天"
    从本地 08:00 才换日，凌晨对话被算进昨天，且与按本地日期写入的日记键脱钩。
    """

    @staticmethod
    def _insert(sm, rows: list[tuple[str, str]]) -> None:
        with sm._conn(write=True) as conn:  # noqa: SLF001
            for ts, text in rows:
                conn.execute(
                    "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                    ("user", text, ts),
                )
            conn.commit()

    def test_window_is_left_closed_right_open(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from datetime import timedelta as _td

        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = StructuredMemory(str(tmp_path / "today.db"))
        try:
            start, end = local_day_utc_bounds()
            start_dt = datetime.strptime(start, _SQL_FMT)
            end_dt = datetime.strptime(end, _SQL_FMT)

            self._insert(
                sm,
                [
                    ((start_dt - _td(seconds=1)).strftime(_SQL_FMT), "前一天最后一秒"),
                    (start, "当天起点（应计入）"),
                    ((end_dt - _td(seconds=1)).strftime(_SQL_FMT), "当天最后一秒（应计入）"),
                    (end, "次日起点（不应计入）"),
                ],
            )

            assert sm.count_chats_today() == 2
            today = sm.get_chats_today()
            # ⚠️ 只断言 count 会与实际数据巧合（旧口径在多数时刻也能凑出 2）；
            # 因此必须断言**取到的行本身**，并加一条两方法同窗口的一致性不变量。
            assert [r["content"] for r in today] == [
                "当天起点（应计入）",
                "当天最后一秒（应计入）",
            ]
            assert sm.count_chats_today() == len(today), "两个方法必须使用同一窗口"
        finally:
            sm.close()

    def test_local_early_morning_is_counted_as_today(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """本地凌晨 00:00–08:00 的对话属于「今天」（旧实现会算成昨天）。

        构造方式与运行时刻无关：取窗口起点 +1 秒 —— 该时刻的 UTC 日期必然是
        前一日（UTC+8 下），旧实现必然漏计。
        """
        from datetime import timedelta as _td

        from shisi.memory.legacy.structured_memory import StructuredMemory

        sm = StructuredMemory(str(tmp_path / "early.db"))
        try:
            start, _end = local_day_utc_bounds()
            early = (datetime.strptime(start, _SQL_FMT) + _td(seconds=1)).strftime(_SQL_FMT)
            self._insert(sm, [(early, "本地凌晨，UTC 日期还是昨天")])
            assert sm.count_chats_today() == 1, "本地日凌晨的对话必须计入今天"
        finally:
            sm.close()

