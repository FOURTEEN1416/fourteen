"""主动消息迟到容忍窗 红测（2026-10-16 爱语语义对齐批）。

对齐爱语 2.8.8 反编译 ``ILinkService.j():710-740`` 的
「[目标, 目标+10min] 内触发；迟到 >10min 放弃（过期不补）」语义：

1. **窗口内触发**：pending 目标 `_next_ok` 已到期但迟到 ≤ 窗口 → 照常走决策/投递
   （与现状一致，账本零扰动）；
2. **窗口外放弃**：迟到 > 窗口（机器休眠/重启错过 tick）→ 不投递、不烧决策调用，
   记 ``reason=window_expired`` 账本事件（sent=False，wait_minutes 字段记录
   迟到分钟数），并出账 ``_llm_proactive_next_ok`` + 触发账本落盘；出账后下一拍
   走全新 LLM 决策（过期不补 ≠ 永久静音，同一条 pending 至多一次 window_expired）；
3. **窗口关闭回退现状**：默认配置（无 ``humanize`` 块）或显式
   ``proactive_window_enable=false`` → 迟到多久都照常触发（零行为变更）。

配置热读自 ``data/scheduler_config.json`` 的 ``humanize`` 块
（``proactive_window_enable`` / ``proactive_window_minutes``，默认 10）；
写侧复用 ``utils.json_state.update_json`` 原子写。测试以 monkeypatch 把
``ProactiveScheduler._CONFIG_PATH`` 指到 tmp 文件，不碰宿主机真源。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from utils import json_state
from utils.local_time import now_local

KEY = "u1"


# ── 测试夹具（__new__ stub 模式，沿用 test_proactive_causality_batch.py）──


def _make_harness(monkeypatch, tmp_path, *, next_ok: float | None):
    import proactive.ase_engine as ase_mod
    import proactive.llm_proactive as lp_mod
    import proactive.proactive_context as pctx_mod
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    cfg_path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg_path)

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {} if next_ok is None else {KEY: float(next_ok)}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    sched._wait_window_event_day = {}
    sched._quiet_event_day = {}
    sched._model_degraded_event_day = {}
    sched._important_dates_sent = set()
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    sched._resolve_proactive_llm = lambda eng: object()
    delivered: list[str] = []
    sched._deliver = lambda message, session_key, character_id="": (
        delivered.append(message) or True
    )
    persists: list[bool] = []
    sched._persist_throttle_ledger = lambda: persists.append(True)

    ledger: list[dict] = []
    monkeypatch.setattr(runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw))
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    monkeypatch.setattr(ase_mod, "_local_now", lambda: now_local())
    # scheduler 在函数内从 proactive_context 局部导入 load_full_persona_hint，
    # 函数局部导入在**调用时**才绑定模块命名空间，monkeypatch 生效。
    # 2026-10-21 R7：签名加 user_key（per-user 模式）——mock 需接受该实参
    monkeypatch.setattr(
        pctx_mod, "load_full_persona_hint",
        lambda character_id="", user_key=None: "",
    )
    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: dict(DEFAULT_WEB_CONFIG))
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")

    decide_state = {"wait_minutes": 0}
    decisions: list[dict] = []

    def _fake_decide(llm, ctx):  # noqa: ANN001 - 与 decide_proactive 同形
        decisions.append({"llm": llm, "ctx": ctx})
        return {
            "should_contact": True,
            "message": "记得吃早饭",
            "reason": "t",
            "wait_minutes": decide_state["wait_minutes"],
        }

    monkeypatch.setattr(lp_mod, "decide_proactive", _fake_decide)

    def _run() -> None:
        hub = SimpleNamespace(get=lambda k: None)
        sched._llm_proactive_one_user(hub, KEY)

    return SimpleNamespace(
        sched=sched,
        ledger=ledger,
        delivered=delivered,
        persists=persists,
        decisions=decisions,
        decide_state=decide_state,
        cfg_path=cfg_path,
        run=_run,
    )


def _write_window_config(cfg_path, *, enable=None, minutes=None) -> None:
    """写 humanize.proactive_window_*（复用 utils.json_state 原子写真源路径）。"""

    def _mutate(data: dict) -> None:
        block = data.get("humanize")
        if not isinstance(block, dict):
            block = {}
        if enable is not None:
            block["proactive_window_enable"] = enable
        if minutes is not None:
            block["proactive_window_minutes"] = minutes
        data["humanize"] = block

    json_state.update_json(cfg_path, _mutate)


def _window_expired_rows(ledger: list[dict]) -> list[dict]:
    return [e for e in ledger if e.get("reason") == "window_expired"]


# ── 1. 窗口内触发 ─────────────────────────────────────────────


class TestWithinWindowTriggers:
    def test_five_minutes_late_within_default_window_triggers(self, monkeypatch, tmp_path):
        """迟到 5 分钟 ≤ 默认窗口 10 分钟 → 照常投递，账本零扰动。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 5 * 60)
        _write_window_config(h.cfg_path, enable=True)

        h.run()

        assert h.delivered == ["记得吃早饭"], "窗口内到点必须正常触发投递"
        assert len(h.decisions) == 1, "窗口内应正常烧一次决策调用"
        assert not _window_expired_rows(h.ledger), "窗口内不得落 window_expired"
        sent_rows = [e for e in h.ledger if e.get("sent") is True]
        assert len(sent_rows) == 1, "成功投递必须落 sent=True 账本行"
        # 窗内触发不扰动 pending 账本（保持现状：投递不清/不重排 _next_ok）
        assert h.sched._llm_proactive_next_ok.get(KEY) is not None

    def test_custom_minutes_widens_window(self, monkeypatch, tmp_path):
        """minutes=60：迟到 30 分钟仍在窗内 → 触发。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable=True, minutes=60)

        h.run()

        assert h.delivered == ["记得吃早饭"]
        assert not _window_expired_rows(h.ledger)


# ── 2. 窗口外放弃 ─────────────────────────────────────────────


class TestExpiredWindowAbandons:
    def test_thirty_minutes_late_beyond_default_window_abandons(self, monkeypatch, tmp_path):
        """迟到 30 分钟 > 默认窗口 10 分钟 → 放弃：不投递、零决策调用、记事件、出账。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable=True)

        h.run()

        assert h.delivered == [], "过期不补：窗口外不得投递"
        assert h.decisions == [], "过期不补：不得为一条已过期的 pending 烧决策调用"
        rows = _window_expired_rows(h.ledger)
        assert len(rows) == 1, f"window_expired 应恰 1 行，实得 {len(rows)} 行"
        row = rows[0]
        assert row.get("sent") is False
        assert row.get("session_key") == KEY
        assert row.get("character_id") == "测试角色"
        assert row.get("wait_minutes") == 30, (
            "事件 wait_minutes 字段应记录迟到分钟数（30），"
            f"实得 {row.get('wait_minutes')!r}"
        )
        assert KEY not in h.sched._llm_proactive_next_ok, "放弃必须出账（pending 不再复活）"
        assert h.persists, "出账属账本变更，必须触发 _persist_throttle_ledger 落盘"

    def test_custom_minutes_narrows_window(self, monkeypatch, tmp_path):
        """minutes=5：迟到 30 分钟 > 窗口 → 放弃。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable=True, minutes=5)

        h.run()

        assert h.delivered == []
        rows = _window_expired_rows(h.ledger)
        assert len(rows) == 1, "minutes=5 时 30 分钟迟到必须判过期"
        assert rows[0].get("wait_minutes") == 30

    def test_expired_then_fresh_decision_next_tick(self, monkeypatch, tmp_path):
        """过期出账后下一拍走全新 LLM 决策：过期不补 ≠ 永久静音；同一条
        pending 至多一次 window_expired（出账即噪声上限）。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        # T-4：本用例断言精确等待窗 → 关闭抖动（抖动语义由 test_proactive_jitter 覆盖）
        monkeypatch.setattr(
            type(h.sched), "get_proactive_jitter_seconds", lambda self: 0.0,
        )
        _write_window_config(h.cfg_path, enable=True)

        h.run()
        assert len(_window_expired_rows(h.ledger)) == 1
        assert KEY not in h.sched._llm_proactive_next_ok

        # 下一拍：LLM 重新决策（wait_minutes=60 → 账本刷新到未来）
        h.decide_state["wait_minutes"] = 60
        h.run()

        assert h.delivered == ["记得吃早饭"], "出账后下一拍应恢复全新决策并投递"
        assert len(_window_expired_rows(h.ledger)) == 1, "同一条 pending 不得二次 window_expired"
        refreshed = h.sched._llm_proactive_next_ok.get(KEY)
        assert refreshed is not None, "wait_minutes=60 的决策必须把 pending 刷新到未来"
        assert abs(refreshed - (time.time() + 60 * 60)) < 60, (
            f"刷新后的目标应≈现在+60 分钟，实得偏移 {refreshed - time.time():.0f}s"
        )


# ── 3. 窗口关闭回退现状 ───────────────────────────────────────


class TestWindowDisabledFallsBack:
    def test_default_config_no_humanize_block_falls_back(self, monkeypatch, tmp_path):
        """默认配置（tmp 配置无任何 humanize 块）→ 窗口关闭，迟到 30 分钟照常触发。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        # 不写任何配置：_CONFIG_PATH 指向的 tmp 文件不存在

        h.run()

        assert h.delivered == ["记得吃早饭"], "默认配置必须零行为变更（到点即触发）"
        assert len(h.decisions) == 1
        assert not _window_expired_rows(h.ledger), "窗口关闭时不得落 window_expired"

    def test_explicit_enable_false_falls_back(self, monkeypatch, tmp_path):
        """显式 proactive_window_enable=false → 同默认，迟到 30 分钟照常触发。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable=False)

        h.run()

        assert h.delivered == ["记得吃早饭"]
        assert not _window_expired_rows(h.ledger)

    def test_non_bool_enable_treated_as_disabled(self, monkeypatch, tmp_path):
        """容错：enable 写成字符串 "true" → 按未启用处理（逐字段回落默认）。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable="true")  # type: ignore[arg-type]

        h.run()

        assert h.delivered == ["记得吃早饭"], "非布尔 enable 不得被误当开启"
        assert not _window_expired_rows(h.ledger)


# ── 4. 窗口时长读侧容错 ───────────────────────────────────────


class TestWindowMinutesFaultTolerance:
    def test_invalid_minutes_falls_back_to_default_ten(self, monkeypatch, tmp_path):
        """minutes 写成非法值（字符串）→ 回落默认 10：30 分钟迟到判过期。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 30 * 60)
        _write_window_config(h.cfg_path, enable=True, minutes="abc")  # type: ignore[arg-type]

        h.run()

        assert h.delivered == []
        rows = _window_expired_rows(h.ledger)
        assert len(rows) == 1, "非法 minutes 回落默认 10 后，30 分钟迟到必须判过期"
        assert rows[0].get("wait_minutes") == 30

    def test_invalid_minutes_default_window_still_allows_within_case(self, monkeypatch, tmp_path):
        """同一回落默认 10：迟到 5 分钟仍在窗内 → 触发（证明回落值确为 10 而非 0）。"""
        h = _make_harness(monkeypatch, tmp_path, next_ok=time.time() - 5 * 60)
        _write_window_config(h.cfg_path, enable=True, minutes="abc")  # type: ignore[arg-type]

        h.run()

        assert h.delivered == ["记得吃早饭"], "回落默认 10 分钟窗口内必须触发"
