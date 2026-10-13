"""未回复连击硬闸 红测（2026-10-12，聚合根因：未回复维无确定性硬闸）。

现状：``_replied_since_proactive`` 信号只在 adaptive 模式降档配额（无硬闸）；
未回复维只有 response_rate 软提示词。48h 关怀下限（proactive_floor_extra）与
无限容忍组合下「用户永久沉默仍每日关怀」无止损。本批对标 astrbot
max_unanswered_times=4（只取语义）：连续 4 条主动零回应 → 决策链确定性停发。

🔴 D14 合规边界：本闸为**停发闸**（更克制方向），不是「定时再补一句」的追问
机制——闸命中后不产生任何等待窗/退避/补发状态（用例 ⑤ 锁死）。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

_UTC = timezone.utc


class _FakeLLM:
    def __init__(self, message: str):
        self.message = message
        self.calls = 0

    def chat_sync(self, *a, **k):
        self.calls += 1
        import json

        return json.dumps(
            {"should_contact": True, "message": self.message,
             "reason": "t", "wait_minutes": 0},
            ensure_ascii=False,
        )


class _FakeEngine:
    """决策链桩：带连击字段与投递基准（闸只应被 unanswered 维命中）。"""

    def __init__(
        self,
        *,
        streak: int = 0,
        last_delivery_at=None,
        hours: float = 5.0,
        recent: list[str] | None = None,
    ):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        self._recent_messages = deque(recent or [])
        self._last_delivery_time = last_delivery_at
        self._unanswered_streak = streak
        self._hours = hours

    def _hours_since_last_chat(self) -> float:
        return self._hours

    def tick(self, hours, dry_run=True):
        return None


class _GroupHub:
    def __init__(self, engines: dict):
        self._engines = engines

    def get(self, key):
        return self._engines.get(key)

    def known_user_keys(self):
        return list(self._engines)


# ── 1. 引擎侧计数 ────────────────────────────────────────


class TestEngineStreakCounting:
    def _engine(self, tmp_path):
        from proactive.ase_engine import ASEEngine

        return ASEEngine(state_path=str(tmp_path / "ase_state.json"))

    def test_four_commits_without_reply_reach_four(self, tmp_path):
        """① commit_sent×4 无 on_chat → _unanswered_streak==4（现状恒缺字段 → 红）。"""
        eng = self._engine(tmp_path)
        for _ in range(4):
            eng.commit_sent({"message": "在忙什么", "type": "llm_proactive"})
        assert eng._unanswered_streak == 4

    def test_reply_resets_then_recount_from_one(self, tmp_path):
        """被回复后重新从 1 计（新一条尚未被回应），非清零后累计错位。"""
        eng = self._engine(tmp_path)
        eng.commit_sent({"message": "a", "type": "llm_proactive"})
        eng.commit_sent({"message": "b", "type": "llm_proactive"})
        assert eng._unanswered_streak == 2
        eng.on_chat("回来了", "欢迎")
        assert eng._unanswered_streak == 0
        eng.commit_sent({"message": "c", "type": "llm_proactive"})
        assert eng._unanswered_streak == 1

    def test_streak_survives_save_load(self, tmp_path):
        """③ save/load round-trip：连击计数不丢（防重启绕闸）。"""
        from proactive.ase_engine import ASEEngine

        p = str(tmp_path / "state.json")
        eng1 = ASEEngine(state_path=p)
        for _ in range(4):
            eng1.commit_sent({"message": "m", "type": "llm_proactive"})
        eng1.save_state()
        eng2 = ASEEngine(state_path=p)
        assert eng2._unanswered_streak == 4


# ── 2. 决策链第三闸（纯函数）──────────────────────────────


class TestGatePureFunction:
    def test_streak_at_limit_blocks(self):
        """①(续) streak=4 → (True,"unanswered_limit")（现状无此参 → 红）。"""
        from proactive.llm_proactive import should_skip_proactive_decision

        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=None, unanswered_streak=4,
        )
        assert skip and reason == "unanswered_limit"

    def test_below_limit_passes(self):
        from proactive.llm_proactive import should_skip_proactive_decision

        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=None, unanswered_streak=3,
        )
        assert not skip and reason == ""

    def test_reply_clears_gate(self):
        """② on_chat 清零 → 闸放行。"""
        from proactive.llm_proactive import should_skip_proactive_decision

        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=None, unanswered_streak=0,
        )
        assert not skip

    def test_default_param_unchanged(self):
        """新参默认 0：既有调用方（不含该参）行为逐字节不变。"""
        from proactive.llm_proactive import should_skip_proactive_decision

        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=None,
        )
        assert not skip

    def test_min_interval_still_precedes(self):
        """闸序：min_interval（更根本的骚扰源）优先于 unanswered_limit。"""
        from proactive.llm_proactive import should_skip_proactive_decision

        last = datetime.now(_UTC) - timedelta(minutes=5)
        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=last, unanswered_streak=4,
        )
        assert skip and reason == "min_interval"


# ── 3. 同 owner 组聚合（组内任一通道被回复即视为已回复）────


class TestGroupAggregation:
    def test_min_across_same_owner(self):
        """④ A 通道 streak=4、B 通道 streak=0 → 组值 0、A 不拦（D16 双通道防误拦）。"""
        from proactive.llm_proactive import (
            account_unanswered_streak,
            should_skip_proactive_decision,
        )

        hub = _GroupHub({
            "7:wx_a@im.wechat": _FakeEngine(streak=4),
            "7:wx_b@im.wechat": _FakeEngine(streak=0),
            "9:wx_other@im.wechat": _FakeEngine(streak=4),
        })
        eng_a = hub.get("7:wx_a@im.wechat")
        streak = account_unanswered_streak(hub, "7:wx_a@im.wechat", eng_a)
        assert streak == 0, "组内任一通道被回复 → 全组视为已回复"
        skip, _ = should_skip_proactive_decision(
            hours_since_last_chat=10.0, last_proactive_at=None, unanswered_streak=streak,
        )
        assert not skip

    def test_all_unreached_returns_min(self):
        hub = _GroupHub({
            "7:wx_a@im.wechat": _FakeEngine(streak=4),
            "7:wx_b@im.wechat": _FakeEngine(streak=2),
        })
        from proactive.llm_proactive import account_unanswered_streak

        streak = account_unanswered_streak(
            hub, "7:wx_a@im.wechat", hub.get("7:wx_a@im.wechat"),
        )
        assert streak == 2

    def test_other_owner_not_merged(self):
        """不同 owner 的连击不进本组（隔离）。"""
        hub = _GroupHub({
            "7:wx_a@im.wechat": _FakeEngine(streak=0),
            "9:wx_other@im.wechat": _FakeEngine(streak=4),
        })
        from proactive.llm_proactive import account_unanswered_streak

        streak = account_unanswered_streak(
            hub, "7:wx_a@im.wechat", hub.get("7:wx_a@im.wechat"),
        )
        assert streak == 0

    def test_missing_field_defaults_zero(self):
        class _Bare:
            pass

        from proactive.llm_proactive import account_unanswered_streak

        hub = _GroupHub({"7:wx_a@im.wechat": _Bare()})
        assert account_unanswered_streak(hub, "7:wx_a@im.wechat", hub.get("7:wx_a@im.wechat")) == 0


# ── 4. 决策链接线 + D14 合规锚 ───────────────────────────


def _make_scheduler(monkeypatch, *, engine):
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
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    llm = _FakeLLM("你又没回我，我还想说话")
    sched._resolve_proactive_llm = lambda eng: llm
    delivered: list[str] = []
    sched._deliver = lambda message, session_key, character_id="": (
        delivered.append(message) or True
    )
    persists: list[bool] = []
    sched._persist_throttle_ledger = lambda: persists.append(True)

    ledger: list[dict] = []
    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw),
    )
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    import proactive.llm_proactive as lp_mod

    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: dict(DEFAULT_WEB_CONFIG))
    monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
    return sched, delivered, ledger, llm, persists


class TestSchedulerGateWiring:
    def test_streak_at_limit_skips_llm_and_delivery(self, monkeypatch):
        """⑤ 连击到限 → 决策链确定性跳过：不调 LLM、不投递、留 unanswered_limit。"""
        eng = _FakeEngine(
            streak=4,
            last_delivery_at=datetime.now(_UTC) - timedelta(hours=3),
            hours=10.0,
        )
        sched, delivered, ledger, llm, _ = _make_scheduler(monkeypatch, engine=eng)
        hub = _GroupHub({"7:wx_a@im.wechat": eng})
        sched._llm_proactive_one_user(hub, "7:wx_a@im.wechat")
        assert any(e.get("reason") == "unanswered_limit" for e in ledger)
        assert not delivered
        assert llm.calls == 0

    def test_gate_hit_produces_no_future_scheduling_state(self, monkeypatch):
        """⑤ D14 合规锚：闸命中后不得产生任何「未来时刻补发」状态——
        无等待窗写入、无退避计数、无定时补发形态的持久化。"""
        eng = _FakeEngine(
            streak=4,
            last_delivery_at=datetime.now(_UTC) - timedelta(hours=3),
            hours=10.0,
        )
        sched, delivered, ledger, llm, persists = _make_scheduler(monkeypatch, engine=eng)
        hub = _GroupHub({"7:wx_a@im.wechat": eng})
        sched._llm_proactive_one_user(hub, "7:wx_a@im.wechat")
        assert sched._llm_proactive_next_ok == {}, "闸命中禁止写等待窗（那=定时再补）"
        assert sched._deliver_fail_counts == {}, "闸命中不属投递失败，禁止退避记账"
        assert not any(
            isinstance(t, (int, float)) and t > 0 for t in sched._llm_proactive_next_ok.values()
        )
        assert llm.calls == 0 and not delivered

    def test_streak_below_limit_delivers(self, monkeypatch):
        """未到限（streak=1）→ 正常决策投递（闸只在到限时生效）。"""
        eng = _FakeEngine(
            streak=1,
            last_delivery_at=datetime.now(_UTC) - timedelta(hours=3),
            hours=10.0,
        )
        sched, delivered, ledger, llm, _ = _make_scheduler(monkeypatch, engine=eng)
        hub = _GroupHub({"7:wx_a@im.wechat": eng})
        sched._llm_proactive_one_user(hub, "7:wx_a@im.wechat")
        assert delivered, "未到限不得拦截"
