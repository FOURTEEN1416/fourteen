"""零 token 骰子闸（dsh-speak-up，2026-10-28）— 纯函数 / 引擎 / 调度真闸。

三层各自的可证伪断言：

1. **纯函数**（``proactive/frequency.py``）：峰谷边界、时机因子边界、命中线
   公式（含 ``miss_bonus_cap`` 与总上限 ``DICE_LINE_MAX`` 两个不同概念）、
   掷骰边界与 fail-safe、连没中序列；配置默认/roundtrip/坏值回落/交叉字段。
2. **引擎**（``proactive/ase_engine.py``）：dry / live **两个独立桶**
   （干跑推进不污染真拦截的连没中）、账本 actor/payload/每日去重、
   ``tick(dry_run=True)`` 不掷骰（防同一轮双掷）、``tick(dry_run=False)``
   的 live 闸只在确定性闸之后才掷。
3. **调度真闸**（``proactive/scheduler.py`` 一次性例外 hook）：源码顺序
   ``should_skip_proactive_decision`` → ``evaluate_dice_gate`` →
   ``eng.tick(hours, dry_run=True)``；未中时设置等待窗、落盘、跳过决策。

隔离：配置与引擎状态全部落 ``tmp_path``（``ProactiveScheduler._CONFIG_PATH``
显式重定向），账本换成 tmp 里的 ``EventLedger``——绝不触碰开发机真实
``data/scheduler_config.json`` / ``data/agent_plane.db``。
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime
from pathlib import Path

import pytest

from proactive import frequency as fq

# 2026-10-26 是周一（今日 2026-10-28 周三），用作峰/谷判定基准
_MON = 0
_SAT = 5


def _at(hour: int, minute: int = 0, *, weekday_day: int = 26) -> datetime:
    return datetime(2026, 10, weekday_day, hour, minute, 0)


def _cfg(**over) -> dict:
    return {**fq.DICE_GATE_DEFAULTS, **over}


# ═══════════════════════════════════════════════════════════
#  1. 纯函数：峰谷 / 时机因子 / 命中线 / 掷骰 / 连没中
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 59, False),
        (9, 0, True),
        (11, 59, True),
        (12, 0, False),   # 左闭右开：12:00 已出峰时
        (13, 59, False),
        (14, 0, True),
        (17, 59, True),
        (18, 0, False),   # 18:00 出峰时
    ],
)
def test_is_peak_time_weekday_boundaries(hour, minute, expected):
    assert fq.is_peak_time(_at(hour, minute)) is expected


@pytest.mark.parametrize("day", [31, 1])  # 2026-10-31 周六 / 2026-11-01 周日
def test_is_peak_time_weekend_is_never_peak(day):
    now = datetime(2026, 10, day, 10, 0, 0) if day == 31 else datetime(2026, 11, day, 10, 0, 0)
    assert now.weekday() >= _SAT
    assert fq.is_peak_time(now) is False


@pytest.mark.parametrize(
    ("idle", "expected"),
    [
        (19.9, 0.15),
        (20.0, 1.0),    # 边界 = 1.0（「不足 20 分钟」才压）
        (359.9, 1.0),
        (360.0, 1.0),   # 边界 = 1.0（「超过 360」才抬）
        (360.1, 1.5),
    ],
)
def test_dice_timing_factor_boundaries(idle, expected):
    assert fq.dice_timing_factor(idle) == pytest.approx(expected)


def test_dice_timing_factor_non_numeric_is_neutral():
    assert fq.dice_timing_factor("oops") == 1.0  # type: ignore[arg-type]


def test_dice_hit_line_peak_short_idle():
    """峰时 20% × 短空闲 0.15 = 3%（刚聊完就极不主动）。"""
    line = fq.dice_hit_line(base_line=20, timing_factor=0.15, miss_streak=0)
    assert line == pytest.approx(3.0)


def test_dice_hit_line_offpeak_long_idle_with_streak_capped_by_total():
    """谷时 60 × 1.5 + min(40, 3×10=30) = 120 → 总上限 100。"""
    line = fq.dice_hit_line(
        base_line=60, timing_factor=1.5, miss_streak=3, miss_bonus=10, miss_bonus_cap=40,
    )
    assert line == pytest.approx(fq.DICE_LINE_MAX)


def test_dice_hit_line_miss_bonus_cap_is_separate_from_line_max():
    """加成上限 40 与命中线总上限 100 是两个概念：这里 10+40=50，不是 100。"""
    line = fq.dice_hit_line(
        base_line=10, timing_factor=1.0, miss_streak=10, miss_bonus=10, miss_bonus_cap=40,
    )
    assert line == pytest.approx(50.0)


@pytest.mark.parametrize("streak", [0, -5])
def test_dice_hit_line_negative_and_zero_streak_have_no_bonus(streak):
    line = fq.dice_hit_line(base_line=30, timing_factor=1.0, miss_streak=streak)
    assert line == pytest.approx(30.0)


@pytest.mark.parametrize(("raw", "expected"), [(0.0, 0.0), (0.5, 50.0), (0.999, 99.9)])
def test_roll_percent_scales_and_clamps(raw, expected):
    assert fq.roll_percent(lambda: raw) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_roll_percent_non_finite_fails_safe_to_100(bad):
    assert fq.roll_percent(lambda: bad) == 100.0


def test_roll_percent_rng_exception_fails_safe_to_100():
    def _boom():
        raise RuntimeError("rng broken")

    assert fq.roll_percent(_boom) == 100.0


def test_evaluate_dice_roll_line_zero_never_hits():
    res = fq.evaluate_dice_roll(
        now=_at(10), idle_minutes=100, miss_streak=0,
        config=_cfg(offpeak_line=0, peak_line=0), rng=lambda: 0.0,
    )
    assert res.line == 0.0
    assert res.hit is False
    assert res.next_streak == 1


def test_evaluate_dice_roll_line_100_hits_even_at_999():
    # 必须显式给谷时日（周六）：_at 默认的 10-26 是周一，周一 10:00 属峰时，
    # base_line 会取 peak_line(20) 而非本用例要验的 offpeak_line(100)。
    res = fq.evaluate_dice_roll(
        now=_at(10, weekday_day=31), idle_minutes=100, miss_streak=0,
        config=_cfg(offpeak_line=100), rng=lambda: 0.999,
    )
    assert res.line == 100.0
    assert res.hit is True
    assert res.next_streak == 0


def test_evaluate_dice_roll_streak_sequence_and_reset():
    """连没中每次 +10%，加成到 cap=40 封顶（线最高 10+40=50，不是 40——
    cap 管加成、线总上限是 DICE_LINE_MAX=100，两个概念，见
    test_dice_hit_line_miss_bonus_cap_is_separate_from_line_max）；命中即清零。"""
    cfg = _cfg(offpeak_line=10, miss_bonus=10, miss_bonus_cap=40)
    streak = 0
    lines = []
    for _ in range(6):
        res = fq.evaluate_dice_roll(
            now=_at(10, weekday_day=31), idle_minutes=100, miss_streak=streak,
            config=cfg, rng=lambda: 0.99,  # 99 > 任何 line → 必未中
        )
        lines.append(res.line)
        streak = res.next_streak
    assert lines == pytest.approx([10, 20, 30, 40, 50, 50])
    assert streak == 6

    hit = fq.evaluate_dice_roll(
        now=_at(10, weekday_day=31), idle_minutes=100, miss_streak=streak,
        config=cfg, rng=lambda: 0.0,
    )
    assert hit.hit is True
    assert hit.next_streak == 0


# ═══════════════════════════════════════════════════════════
#  2. 配置 owner：默认 / roundtrip / 坏值回落 / 写侧校验
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def dice_cfg_path(tmp_path, monkeypatch):
    """把配置真源重定向到 per-test 临时文件（conftest 的沙箱之上再钉死）。"""
    from proactive.scheduler import ProactiveScheduler

    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", path, raising=False)
    return path


def _seed(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_defaults_are_closed_and_dry_run(dice_cfg_path):
    cfg = fq.read_dice_gate_config()  # 文件不存在
    assert cfg == fq.DICE_GATE_DEFAULTS
    assert cfg["enabled"] is False
    assert cfg["dry_run"] is True


def test_config_path_follows_class_attribute_at_call_time(dice_cfg_path):
    from proactive.scheduler import ProactiveScheduler

    assert fq._config_path() == Path(ProactiveScheduler._CONFIG_PATH)


def test_frequency_module_has_no_module_level_scheduler_import():
    """`_config_path` 必须函数内 import，否则 scheduler→ase_engine→frequency 成环。"""
    tree = ast.parse(Path(fq.__file__).read_text(encoding="utf-8"))
    module_level = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("proactive")
    ]
    assert module_level == [], f"frequency.py 模块级出现 proactive 导入：{module_level}"

    src = inspect.getsource(fq._config_path)
    assert "from proactive.scheduler import ProactiveScheduler" in src


def test_write_roundtrip_preserves_other_blocks(dice_cfg_path):
    _seed(
        dice_cfg_path,
        {"llm_proactive": {"enabled": False}, "quiet_hours": {"start": 1, "end": 2}},
    )
    out = fq.write_dice_gate_config(enabled=True, peak_line=35, idle_short_factor=0.3)
    assert out["enabled"] is True
    assert out["peak_line"] == 35
    assert out["idle_short_factor"] == pytest.approx(0.3)
    # 未传的键保持默认（partial merge）
    assert out["offpeak_line"] == fq.DICE_GATE_DEFAULTS["offpeak_line"]

    raw = _read_raw(dice_cfg_path)
    assert raw["llm_proactive"] == {"enabled": False}
    assert raw["quiet_hours"] == {"start": 1, "end": 2}
    assert set(raw["dice_gate"]) == set(fq.DICE_GATE_DEFAULTS)


def test_read_falls_back_on_bad_types_and_out_of_range(dice_cfg_path):
    _seed(
        dice_cfg_path,
        {
            "dice_gate": {
                "enabled": "yes",       # 非 bool → 默认
                "dry_run": 0,           # 非 bool → 默认
                "peak_line": "abc",     # 非 int → 默认
                "offpeak_line": 500,    # 越界 → 默认
                "miss_bonus": 99,       # 越界 → 默认
                "idle_long_factor": 0.2,  # 越界（<1）→ 默认
            }
        },
    )
    cfg = fq.read_dice_gate_config()
    assert cfg["enabled"] is False
    assert cfg["dry_run"] is True
    assert cfg["peak_line"] == fq.DICE_GATE_DEFAULTS["peak_line"]
    assert cfg["offpeak_line"] == fq.DICE_GATE_DEFAULTS["offpeak_line"]
    assert cfg["miss_bonus"] == pytest.approx(fq.DICE_GATE_DEFAULTS["miss_bonus"])
    assert cfg["idle_long_factor"] == pytest.approx(fq.DICE_GATE_DEFAULTS["idle_long_factor"])


def test_read_falls_back_on_inverted_idle_window(dice_cfg_path):
    _seed(
        dice_cfg_path,
        {"dice_gate": {"idle_short_minutes": 600, "idle_long_minutes": 300}},
    )
    cfg = fq.read_dice_gate_config()
    assert cfg["idle_short_minutes"] == fq.DICE_GATE_DEFAULTS["idle_short_minutes"]
    assert cfg["idle_long_minutes"] == fq.DICE_GATE_DEFAULTS["idle_long_minutes"]


@pytest.mark.parametrize(
    "payload",
    [
        {"nope": 1},                       # 未知字段
        {"peak_line": 20.5},               # int 字段给 float
        {"enabled": 1},                    # bool 字段给 int
        {"dry_run": "true"},               # bool 字段给 str
        {"peak_line": 101},                # 越界
        {"idle_long_factor": 0.5},         # 越界（<1）
        {"miss_bonus": -1},                # 越界（<0）
    ],
)
def test_write_rejects_invalid_fields(dice_cfg_path, payload):
    with pytest.raises(ValueError):
        fq.write_dice_gate_config(**payload)


def test_write_rejects_inverted_idle_window(dice_cfg_path):
    with pytest.raises(ValueError, match="idle_long_minutes"):
        fq.write_dice_gate_config(idle_short_minutes=500)


def test_write_accepts_paired_idle_window(dice_cfg_path):
    out = fq.write_dice_gate_config(idle_short_minutes=500, idle_long_minutes=600)
    assert out["idle_short_minutes"] == 500
    assert out["idle_long_minutes"] == 600


# ═══════════════════════════════════════════════════════════
#  3. 引擎：dry / live 双桶 + 账本 + tick 闸位
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """把默认账本换成 tmp 里的 EventLedger（不写开发机 data/agent_plane.db）。"""
    from shisi.agent_plane import event_ledger as el

    led = el.EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(el, "_default_ledger", led)
    return led


def _enable(path: Path, **over) -> None:
    _seed(path, {"dice_gate": {**_cfg(**over)}})


def _engine(tmp_path, rng_value: float = 0.99):
    from proactive.ase_engine import ASEEngine

    eng = ASEEngine(state_path=str(tmp_path / "ase_state.json"), dice_rng=lambda: rng_value)
    eng.set_user_key("2:wxid@im.wechat")
    return eng


def _idle(monkeypatch, eng, minutes: float) -> None:
    monkeypatch.setattr(eng, "_hours_since_last_chat", lambda: minutes / 60.0, raising=False)


def test_disabled_gate_allows_and_clears_both_streaks(dice_cfg_path, tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    eng._dice_dry_miss_streak = 3
    eng._dice_live_miss_streak = 4
    _idle(monkeypatch, eng, 600)

    allow, result = eng.evaluate_dice_gate(requested_mode="live")

    assert allow is True and result is None
    assert eng._dice_dry_miss_streak == 0
    assert eng._dice_live_miss_streak == 0
    state = eng.get_dice_gate_state()
    assert state["dry"]["rolls"] == 0 and state["live"]["rolls"] == 0


def test_dry_run_requested_live_only_touches_dry_bucket(dice_cfg_path, tmp_path, monkeypatch, ledger):
    _enable(dice_cfg_path, enabled=True, dry_run=True)
    eng = _engine(tmp_path, rng_value=0.99)
    _idle(monkeypatch, eng, 600)

    allow, result = eng.evaluate_dice_gate(requested_mode="live")

    assert allow is True, "干跑绝不拦截"
    assert result is not None and result.hit is False
    state = eng.get_dice_gate_state()
    assert state["dry"]["rolls"] == 1
    assert state["dry"]["would_skip"] == 1
    assert state["dry"]["would_send"] == 0
    assert state["dry"]["miss_streak"] == 1
    assert state["live"]["rolls"] == 0
    assert state["live"]["miss_streak"] == 0


def test_live_miss_blocks_and_marks_skip_reason(dice_cfg_path, tmp_path, monkeypatch, ledger):
    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.99)
    _idle(monkeypatch, eng, 600)

    allow, result = eng.evaluate_dice_gate(requested_mode="live")

    assert allow is False
    assert result is not None and result.hit is False
    assert eng._last_skip_reason == "dice_gate"
    state = eng.get_dice_gate_state()
    assert state["live"]["rolls"] == 1
    assert state["live"]["skips"] == 1
    assert state["live"]["miss_streak"] == 1
    assert state["dry"]["rolls"] == 0


def test_live_hit_allows_and_resets_streak(dice_cfg_path, tmp_path, monkeypatch, ledger):
    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.0)
    eng._dice_live_miss_streak = 3
    _idle(monkeypatch, eng, 600)

    allow, result = eng.evaluate_dice_gate(requested_mode="live")

    assert allow is True
    assert result is not None and result.hit is True
    assert eng._dice_live_miss_streak == 0
    assert eng.get_dice_gate_state()["live"]["hits"] == 1


def test_dry_bucket_does_not_pollute_live_streak(dice_cfg_path, tmp_path, monkeypatch, ledger):
    """干跑连没中两次后切真拦截：live 的连没中必须从 0 起算。"""
    # 钉死引擎时钟到周一 10:00（峰时）：引擎走真实墙钟，谷时长空窗下
    # 第二掷线=60×1.5+10=100，roll 99 会命中、连没中归零——断言随运行
    # 时刻漂移（峰时过/谷时挂）。钉峰时后线=30/40，99 必未中，全天确定。
    monkeypatch.setattr("proactive.ase_engine._local_now", lambda: _at(10))
    _enable(dice_cfg_path, enabled=True, dry_run=True)
    eng = _engine(tmp_path, rng_value=0.99)
    _idle(monkeypatch, eng, 600)
    for _ in range(2):
        assert eng.evaluate_dice_gate(requested_mode="live")[0] is True
    assert eng._dice_dry_miss_streak == 2
    assert eng._dice_live_miss_streak == 0

    _enable(dice_cfg_path, enabled=True, dry_run=False)
    allow, _ = eng.evaluate_dice_gate(requested_mode="live")
    assert allow is False
    assert eng._dice_live_miss_streak == 1


def test_ledger_event_actor_payload_and_daily_dedup(dice_cfg_path, tmp_path, monkeypatch, ledger):
    from shisi.agent_plane.event_ledger import EVENT_PROACTIVE_SKIP

    # 同 test_dry_bucket_does_not_pollute_live_streak：钉峰时钟，防谷时
    # 第二掷（streak=1 线=100）命中导致 allow 翻 True、账本少一条。
    monkeypatch.setattr("proactive.ase_engine._local_now", lambda: _at(10))

    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.99)
    _idle(monkeypatch, eng, 600)

    assert eng.evaluate_dice_gate(requested_mode="live")[0] is False
    assert eng.evaluate_dice_gate(requested_mode="live")[0] is False  # 同日第二条不落账

    events = ledger.query(session_key="2:wxid@im.wechat", event_type=EVENT_PROACTIVE_SKIP)
    assert len(events) == 1, "每 user|reason 每日一条（与 scheduler._emit_skip_event_daily 同口径）"
    ev = events[0]
    assert ev.actor == "proactive_dice"
    payload = ev.payload
    assert payload["reason"] == "dice_gate"
    assert payload["mode"] == "live"
    assert payload["dry_run_config"] is False
    assert payload["hit"] is False
    assert {"roll", "line", "peak", "base_line", "timing_factor", "idle_minutes"} <= set(payload)


def test_dry_run_ledger_uses_separate_reason(dice_cfg_path, tmp_path, monkeypatch, ledger):
    from shisi.agent_plane.event_ledger import EVENT_PROACTIVE_SKIP

    _enable(dice_cfg_path, enabled=True, dry_run=True)
    eng = _engine(tmp_path, rng_value=0.99)
    _idle(monkeypatch, eng, 600)

    assert eng.evaluate_dice_gate(requested_mode="live")[0] is True

    events = ledger.query(session_key="2:wxid@im.wechat", event_type=EVENT_PROACTIVE_SKIP)
    assert len(events) == 1
    assert events[0].payload["reason"] == "dice_gate_dry_run"
    assert events[0].payload["dry_run_config"] is True


def test_gate_without_user_key_writes_no_ledger(dice_cfg_path, tmp_path, monkeypatch, ledger):
    """无会话归属（全局引擎）→ 不写账本（没有"谁"的维度，不拿匿名键充数）。"""
    from shisi.agent_plane.event_ledger import EVENT_PROACTIVE_SKIP

    _enable(dice_cfg_path, enabled=True, dry_run=True)
    eng = _engine(tmp_path, rng_value=0.99)
    eng._user_key = ""
    _idle(monkeypatch, eng, 600)

    eng.evaluate_dice_gate(requested_mode="live")

    assert ledger.query(event_type=EVENT_PROACTIVE_SKIP) == []
    assert eng.get_dice_gate_state()["dry"]["rolls"] == 1


def test_gate_exception_fails_open(dice_cfg_path, tmp_path, monkeypatch):
    """骰子内部炸了必须放行（观测组件不能成为主动链的单点故障）。"""
    eng = _engine(tmp_path)

    def _boom(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(fq, "read_dice_gate_config", _boom)
    allow, result = eng.evaluate_dice_gate(requested_mode="live")
    assert allow is True and result is None


def test_tick_dry_run_does_not_roll(dice_cfg_path, tmp_path, monkeypatch):
    """生产链路每轮只由 scheduler hook 掷一次：dry_run(dry) must not roll."""
    _enable(dice_cfg_path, enabled=True, dry_run=True)
    eng = _engine(tmp_path, rng_value=0.99)
    eng.set_quiet_hours(0, 0)  # 关闭静默，确保走到 dry_run 分支
    _idle(monkeypatch, eng, 600)

    assert eng.tick(600 / 60.0, dry_run=True) is None

    state = eng.get_dice_gate_state()
    assert state["dry"]["rolls"] == 0 and state["live"]["rolls"] == 0
    assert eng._last_skip_reason == "dry_run"


def test_tick_quiet_hours_short_circuits_before_gate(dice_cfg_path, tmp_path, monkeypatch):
    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.99)
    calls: list[str] = []
    monkeypatch.setattr(
        eng, "evaluate_dice_gate",
        lambda **kw: calls.append(kw.get("requested_mode")) or (True, None),
        raising=False,
    )
    h = datetime.now().hour
    eng.set_quiet_hours(h, (h + 1) % 24)

    assert eng.tick(1.0, dry_run=False) is None
    assert calls == [], "静默闸已短路，骰子不该被掷"
    assert eng._last_skip_reason == "quiet_hours"


def test_tick_daily_limit_short_circuits_before_gate(dice_cfg_path, tmp_path, monkeypatch):
    from utils.local_time import now_local

    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.99)
    calls: list[str] = []
    monkeypatch.setattr(
        eng, "evaluate_dice_gate",
        lambda **kw: calls.append(kw.get("requested_mode")) or (True, None),
        raising=False,
    )
    eng.set_quiet_hours(0, 0)
    eng._daily_message_count = 999
    # ⚠️ 必须同时把跨日惰性重置的基准钉到今日：fresh 引擎
    # ``_last_reset_date=None``，``tick()`` 一进 ``_rollover_if_new_day``
    # （ase_engine.py:2000）就把 ``_daily_message_count`` 清零——999 永远
    # 到不了配额闸，骰子会被错误放行。
    eng._last_reset_date = now_local().date()

    assert eng.tick(1.0, dry_run=False) is None
    assert calls == [], "配额闸已短路，骰子不该被掷"
    assert eng._last_skip_reason == "daily_limit"


def test_tick_live_miss_returns_none_with_skip_reason(dice_cfg_path, tmp_path, monkeypatch):
    _enable(dice_cfg_path, enabled=True, dry_run=False)
    eng = _engine(tmp_path, rng_value=0.99)
    eng.set_quiet_hours(0, 0)
    _idle(monkeypatch, eng, 600)

    assert eng.tick(600 / 60.0, dry_run=False) is None
    assert eng._last_skip_reason == "dice_gate"
    assert eng.get_dice_gate_state()["live"]["rolls"] == 1


def test_runtime_config_and_health_expose_dice_gate(dice_cfg_path, tmp_path):
    _enable(dice_cfg_path, enabled=True, peak_line=33)
    eng = _engine(tmp_path)

    cfg = eng.get_runtime_config()
    assert cfg["dice_gate"]["peak_line"] == 33
    assert cfg["dice_gate_effective"] is True
    assert set(cfg["dice_gate_state"]) == {"dry", "live"}

    health = eng.health_check()
    assert health["dice_gate"]["enabled"] is True
    assert health["dice_gate_effective"] is True
    assert "dice_gate_state" in health


# ═══════════════════════════════════════════════════════════
#  4. 调度真闸（proactive/scheduler.py 一次性例外 hook）
# ═══════════════════════════════════════════════════════════


class _DiceResult:
    line = 42.0
    roll = 88.0
    next_streak = 2


def _hub_sched(monkeypatch, tmp_path, *, dice_result=None):
    import proactive.ase_hub as hub_mod
    import proactive.llm_proactive as lp
    import shisi.agent_plane.runtime as rt
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    class _FakeASE:
        def __init__(self, user_key: str = "", state_path: str = ""):
            self.user_key = user_key
            self.state_path = state_path
            self._daily_message_count = 0
            self._paused = False
            self._last_skip_reason = ""
            self._min_interval_minutes = 30
            self.urgency = type("U", (), {"total": 8.5})()
            self.tick_calls: list[bool] = []
            self.dice_calls: list[str] = []
            self.commit_calls: list[dict] = []

        def _hours_since_last_chat(self):
            return 90.0

        def evaluate_dice_gate(self, *, requested_mode: str = "live"):
            self.dice_calls.append(requested_mode)
            if dice_result is None:
                return True, None
            return False, dice_result

        def tick(self, hours, emotion_state=None, dry_run=False):
            self.tick_calls.append(dry_run)
            return None

        def commit_sent(self, result):
            self.commit_calls.append(result)

    hub = ASEHub(_FakeASE)
    sched = ProactiveScheduler(ase_engine=hub)
    sched.reload_config = lambda: None
    key = "4:peer@im.wechat"
    sched._collect_ase_user_keys = lambda: [key]
    sched._resolve_proactive_llm = lambda eng=None: object()
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid="": "")
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: None)
    return sched, hub, key


def _no_quiet(sched) -> None:
    """把静默窗设为「下一个小时」这一小时窗口——恰好把当前小时排除在外。

    ⚠️ 不能写 ``(h, h)``：scheduler 版 ``_is_quiet_hours``（scheduler.py:1121）
    没有 ``start == end`` 特例——``start == end`` 时走
    ``local_hour >= start or local_hour < end``，恒为 True = 恒静默，
    前置闸（scheduler.py:1424）在骰子闸之前 return，测试永远摸不到 hook。
    取 ``((h+1) % 24, (h+2) % 24)``：覆盖**下一个小时**这一小时，
    当前小时必然不在其内。已对 24 个小时逐一穷举验证（均判非静默）。
    """
    h = datetime.now().hour
    sched._quiet_hours = ((h + 1) % 24, (h + 2) % 24)


def test_scheduler_source_order_decide_gate_then_tick():
    """闸位契约：确定性节奏闸 → 骰子闸 → eng.tick(dry_run=True)。

    ⚠️ 检索起点必须从 ``i_dice`` 起算：hook 块的注释里含有字面量
    ``eng.tick(hours, dry_run=True)``（解释「同一轮随后是…那条路径不掷骰」），
    从全文搜会命中注释（实测注释偏移 9545 < 真代码 10695）。
    """
    from proactive.scheduler import ProactiveScheduler

    src = inspect.getsource(ProactiveScheduler._llm_proactive_one_user)
    i_skip = src.index("should_skip_proactive_decision(")
    i_dice = src.index("evaluate_dice_gate(requested_mode=\"live\")")
    i_tick = src.index("eng.tick(hours, dry_run=True)", i_dice)
    assert i_skip < i_dice < i_tick


def test_scheduler_dice_miss_sets_wait_window_and_skips_decision(
    monkeypatch, tmp_path,
):
    import proactive.llm_proactive as lp

    decide_calls: list[int] = []
    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: decide_calls.append(1) or {})
    sched, hub, key = _hub_sched(monkeypatch, tmp_path, dice_result=_DiceResult())
    _no_quiet(sched)
    persisted: list[int] = []
    sched._persist_throttle_ledger = lambda: persisted.append(1)

    before = sched._llm_proactive_next_ok.get(key)
    sched._check_ase()

    eng = hub.get(key)
    assert eng.dice_calls == ["live"]
    assert eng.tick_calls == [], "骰子未中必须跳过本轮决策（不烧 LLM）"
    assert decide_calls == []
    after = sched._llm_proactive_next_ok.get(key)
    assert before is None and after is not None
    # 等待窗 = 引擎最小间隔（默认 30 分钟）→ 至少 29 分钟之后
    assert after - datetime.now().timestamp() > 29 * 60
    assert persisted, "账本变更必须落盘（重启不复活）"


def test_scheduler_dice_allow_continues_to_dry_tick(monkeypatch, tmp_path):
    import proactive.llm_proactive as lp

    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: {"should_contact": False, "reason": "x"})
    sched, hub, key = _hub_sched(monkeypatch, tmp_path, dice_result=None)
    _no_quiet(sched)

    sched._check_ase()

    eng = hub.get(key)
    assert eng.dice_calls == ["live"]
    assert eng.tick_calls == [True], "放行后照旧走 dry tick 取紧迫度"
