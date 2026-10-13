"""skip 账本治理 红测（2026-10-12，聚合根因：前置闸账本策略不一致）。

生产实证（agent_plane 账本）：reason=llm_wait_window 4165/4348=96% 的账本噪声
（每次 tick 每 5 分钟一行）；静默时段闸（23-7 每 5 分钟 tick）现状只 logger.debug
连一行都不落 —— 前置闸要么刷屏、要么不可观测。本批口径：**每用户每日至多一条**
（套用同文件 web_disabled 的 P1-51 每日去重模式），并给静默闸补同款落账。

配套纪律：新增的两个日期账本必须四处同步（init 声明 / load 回填 / persist 落盘 /
purge 清理）—— 漏 purge 会让已删账号条目永久残留并随 _persist 复活。
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from utils.local_time import now_local


class _FakeEngine:
    def __init__(self, hours: float = 5.0):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        self._recent_messages = []
        self._last_delivery_time = None
        self._hours = hours

    def _hours_since_last_chat(self) -> float:
        return self._hours

    def tick(self, hours, dry_run=True):
        return None


def _make_scheduler(monkeypatch, *, quiet: bool = False, enabled: bool = True):
    """__new__ 手工构造（沿用 causality 批模式），手工补全部账本 dict。"""
    import proactive.ase_engine as ase_mod
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    sched._wait_window_event_day = {}
    sched._quiet_event_day = {}
    sched._important_dates_sent = set()
    sched._is_quiet_hours = lambda: quiet
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    sched._resolve_proactive_llm = lambda eng: None
    sched._deliver = lambda message, session_key, character_id="": True
    persists: list[bool] = []
    sched._persist_throttle_ledger = lambda: persists.append(True)

    ledger: list[dict] = []
    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw),
    )
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    monkeypatch.setattr(ase_mod, "_local_now", lambda: now_local())
    import proactive.llm_proactive as lp_mod

    cfg = dict(DEFAULT_WEB_CONFIG)
    cfg["enabled"] = enabled
    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: cfg)
    monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
    return sched, ledger, persists


def _run(sched, monkeypatch, key="7:wx_a@im.wechat"):
    from types import SimpleNamespace

    hub = SimpleNamespace(get=lambda k: None, known_user_keys=lambda: [key])
    sched._llm_proactive_one_user(hub, key)


# ── 1. llm_wait_window 每用户每日至多一条 ─────────────────


class TestWaitWindowDailyOnce:
    def test_same_day_two_ticks_one_event(self, monkeypatch):
        """① 等待窗内同日两次决策 → 账本仅 1 行 llm_wait_window（现状 2 行 → 红）。"""
        sched, ledger, persists = _make_scheduler(monkeypatch)
        import time as _t

        sched._llm_proactive_next_ok["7:wx_a@im.wechat"] = _t.time() + 30 * 60.0
        _run(sched, monkeypatch)
        _run(sched, monkeypatch)
        rows = [e for e in ledger if e.get("reason") == "llm_wait_window"]
        assert len(rows) == 1, f"等待窗账本应每日一条，实得 {len(rows)} 行"

    def test_next_day_new_event(self, monkeypatch):
        """② 次日再进等待窗 → 新 1 行（跨日去重键失效重置）。"""
        import proactive.ase_engine as ase_mod

        sched, ledger, _ = _make_scheduler(monkeypatch)
        import time as _t

        sched._llm_proactive_next_ok["7:wx_a@im.wechat"] = _t.time() + 30 * 60.0
        _run(sched, monkeypatch)
        monkeypatch.setattr(
            ase_mod, "_local_now", lambda: now_local() + timedelta(days=1),
        )
        _run(sched, monkeypatch)
        rows = [e for e in ledger if e.get("reason") == "llm_wait_window"]
        assert len(rows) == 2, "次日应重新落一行"

    def test_daily_bookkeeping_persisted(self, monkeypatch):
        """账本写入即落盘（块E 纪律：重启当日不重写）。"""
        sched, ledger, persists = _make_scheduler(monkeypatch)
        import time as _t

        sched._llm_proactive_next_ok["7:wx_a@im.wechat"] = _t.time() + 30 * 60.0
        _run(sched, monkeypatch)
        assert persists, "账本变更必须触发 _persist_throttle_ledger"


# ── 2. 静默时段闸补落账（每日一条 reason=quiet_hours）──────


class TestQuietHoursLedger:
    def test_same_day_two_ticks_one_event(self, monkeypatch):
        """③ 静默窗内同日两次决策 → 1 行 quiet_hours（现状 0 行 → 红）。"""
        sched, ledger, _ = _make_scheduler(monkeypatch, quiet=True)
        _run(sched, monkeypatch)
        _run(sched, monkeypatch)
        rows = [e for e in ledger if e.get("reason") == "quiet_hours"]
        assert len(rows) == 1, f"静默闸应每日落一条，实得 {len(rows)} 行"

    def test_next_day_new_event(self, monkeypatch):
        import proactive.ase_engine as ase_mod

        sched, ledger, _ = _make_scheduler(monkeypatch, quiet=True)
        _run(sched, monkeypatch)
        monkeypatch.setattr(
            ase_mod, "_local_now", lambda: now_local() + timedelta(days=1),
        )
        _run(sched, monkeypatch)
        rows = [e for e in ledger if e.get("reason") == "quiet_hours"]
        assert len(rows) == 2


# ── 3. web_disabled 既有口径回归对照（P1-51 不受影响）──────


class TestWebDisabledUnchanged:
    def test_web_disabled_daily_once_still_holds(self, monkeypatch):
        """④ enabled=false 同日两次 → 仍 1 行 web_disabled（P1-51 既有行为）。"""
        sched, ledger, _ = _make_scheduler(monkeypatch, enabled=False)
        _run(sched, monkeypatch)
        _run(sched, monkeypatch)
        rows = [e for e in ledger if e.get("reason") == "web_disabled"]
        assert len(rows) == 1


# ── 4. purge 四处同步（漏 purge 元组 → 此例红）─────────────


class TestPurgeCoversNewLedgers:
    def test_purge_clears_new_day_ledgers(self, monkeypatch):
        """⑤ purge_throttle_for 必须纳入两个新账本且计数包含其条目。"""
        from proactive.scheduler import ProactiveScheduler

        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._llm_proactive_next_ok = {}
        sched._deliver_fail_counts = {}
        sched._disabled_event_day = {}
        sched._important_dates_sent = set()
        sched._wait_window_event_day = {
            "7:wx_a@im.wechat": "2026-10-12",
            "9:wx_b@im.wechat": "2026-10-12",
        }
        sched._quiet_event_day = {
            "7:wx_a@im.wechat": "2026-10-12",
            "9:wx_b@im.wechat": "2026-10-12",
        }
        persists: list[bool] = []
        sched._persist_throttle_ledger = lambda: persists.append(True)

        removed = sched.purge_throttle_for(7)
        assert sched._wait_window_event_day.get("7:wx_a@im.wechat") is None, (
            "wait_window 日期账本的该 owner 条目必须被清（漏 purge 元组即残留并复活）"
        )
        assert sched._quiet_event_day.get("7:wx_a@im.wechat") is None
        # 其他账号条目保留
        assert sched._wait_window_event_day.get("9:wx_b@im.wechat") == "2026-10-12"
        assert sched._quiet_event_day.get("9:wx_b@im.wechat") == "2026-10-12"
        assert removed == 2, f"removed 计数须包含两个新账本各 1 条，实得 {removed}"
        assert persists, "有清理必须落盘（防 _persist 复活）"


# ── 5. 账本持久化 round-trip（load/persist 两处同步）───────


class TestLedgerRoundTrip:
    def test_new_ledgers_survive_persist_load(self, tmp_path, monkeypatch):
        """新账本必须进 _persist/_load 的 throttle 段（缺一处即 round-trip 断）。"""
        from proactive.scheduler import ProactiveScheduler

        cfg = tmp_path / "scheduler_config.json"
        monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg)
        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._llm_proactive_next_ok = {}
        sched._deliver_fail_counts = {}
        sched._disabled_event_day = {}
        sched._important_dates_sent = set()
        sched._wait_window_event_day = {"7:wx_a@im.wechat": "2026-10-12"}
        sched._quiet_event_day = {"9:wx_b@im.wechat": "2026-10-11"}
        sched._persist_throttle_ledger()

        data = json.loads(cfg.read_text(encoding="utf-8"))
        throttle = data.get("throttle") or {}
        assert throttle.get("wait_window_event_day") == {"7:wx_a@im.wechat": "2026-10-12"}
        assert throttle.get("quiet_event_day") == {"9:wx_b@im.wechat": "2026-10-11"}

        sched2 = ProactiveScheduler.__new__(ProactiveScheduler)
        sched2._llm_proactive_next_ok = {}
        sched2._deliver_fail_counts = {}
        sched2._disabled_event_day = {}
        sched2._important_dates_sent = set()
        sched2._wait_window_event_day = {}
        sched2._quiet_event_day = {}
        sched2._load_throttle_ledger()
        assert sched2._wait_window_event_day == {"7:wx_a@im.wechat": "2026-10-12"}
        assert sched2._quiet_event_day == {"9:wx_b@im.wechat": "2026-10-11"}
