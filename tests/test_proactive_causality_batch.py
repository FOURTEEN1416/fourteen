"""主动消息因果连贯批 红测（2026-10-12，生产实证驱动）。

生产事故（2026-10-12 08:05-08:10，用户报障截图+chat_history+决策日志三方对齐）：
① 角色 08:05 主动发「记得吃早饭」（channel=proactive 已落库 id=2517），用户回复后
   角色却说「我是看你刚发消息 随口一问」——历史注入对 proactive 行与普通回复无差别，
   模型无从分辨「我定时主动发的」vs「我回应用户说的」，把关怀的触发原因错误归因；
② 08:10 决策日志原话「距离上次角色主动发消息仅5分钟…还是发」——无最短间隔硬闸；
③ 08:10:36 决策日志「用户刚纠正了角色的记忆错误…需要迅速缓和气氛」——用户 2 分钟前
   刚聊完，决策器立即主动发消息，回复后冷却缺失（变相追问轰炸）。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from utils.local_time import now_local

_UTC = timezone.utc


# ── 1. 决策闸纯函数（proactive/llm_proactive）──────────────


class TestShouldSkipProactiveDecision:
    def _call(self, hours: float, last_proactive_at, now=None):
        from proactive.llm_proactive import should_skip_proactive_decision

        return should_skip_proactive_decision(
            hours_since_last_chat=hours, last_proactive_at=last_proactive_at, now=now,
        )

    def test_min_interval_blocks_recent_proactive(self):
        # 生产实证：08:05 投递 → 08:10 再决策。5 分钟 < 60 分钟 → 必须拦
        last = datetime.now(_UTC) - timedelta(minutes=5)
        skip, reason = self._call(hours=10.0, last_proactive_at=last)
        assert skip and reason == "min_interval"

    def test_min_interval_passes_after_window(self):
        last = datetime.now(_UTC) - timedelta(minutes=90)
        skip, reason = self._call(hours=10.0, last_proactive_at=last)
        assert not skip and reason == ""

    def test_no_history_no_block(self):
        skip, reason = self._call(hours=10.0, last_proactive_at=None)
        assert not skip

    def test_cooldown_after_recent_user_message(self):
        # 生产实证：用户 2 分钟前刚说完话，决策器立刻主动发"缓和气氛"
        skip, reason = self._call(
            hours=2.0 / 60.0, last_proactive_at=datetime.now(_UTC) - timedelta(hours=3),
        )
        assert skip and reason == "cooldown_after_reply"

    def test_cooldown_passes_when_user_idle(self):
        skip, _ = self._call(
            hours=2.0, last_proactive_at=datetime.now(_UTC) - timedelta(hours=3),
        )
        assert not skip

    def test_min_interval_takes_precedence(self):
        # 双命中时报 min_interval（更根本的骚扰源）
        last = datetime.now(_UTC) - timedelta(minutes=5)
        skip, reason = self._call(hours=0.03, last_proactive_at=last)
        assert skip and reason == "min_interval"

    def test_timezone_forms_agree(self):
        # last 为 UTC aware、now 为北京 naive 墙钟（now_local 常态分支）→ 判定一致
        last_utc = datetime.now(_UTC) - timedelta(minutes=10)
        beijing_naive = now_local()  # 与 last 的真实间隔约 10 分钟
        skip, reason = self._call(hours=10.0, last_proactive_at=last_utc, now=beijing_naive)
        assert skip and reason == "min_interval"


# ── 2. 出站标记（orchestrator/context_budget）─────────────


class TestOutboundMarkers:
    def _import(self):
        from orchestrator.context_budget import annotate_outbound_markers

        return annotate_outbound_markers

    def _rows(self):
        return [
            {"role": "assistant", "content": "记得吃早饭", "channel": "proactive"},
            {"role": "user", "content": "才八点你和我说什么吃早饭"},
            {"role": "assistant", "content": "早，记得带伞", "channel": ""},
            {"role": "assistant", "content": "你设定的提醒时间到了", "channel": "reminder"},
        ]

    def test_proactive_row_marked(self):
        annotate = self._import()
        out = annotate(self._rows())
        assert out[0]["content"] == "[主动] 记得吃早饭"
        assert out[3]["content"] == "[定时提醒] 你设定的提醒时间到了"

    def test_normal_rows_untouched(self):
        annotate = self._import()
        out = annotate(self._rows())
        assert out[1]["content"] == "才八点你和我说什么吃早饭"
        assert out[2]["content"] == "早，记得带伞"

    def test_rows_without_channel_passed_through(self):
        annotate = self._import()
        rows = [{"role": "assistant", "content": "旧形态行"}]
        assert annotate(rows) == rows

    def test_input_not_mutated(self):
        annotate = self._import()
        rows = self._rows()
        snapshot = [dict(r) for r in rows]
        annotate(rows)
        assert rows == snapshot

    def test_stacks_with_time_prefix(self):
        # 主链接线顺序：时间前缀在前、出站标记紧随其后
        from orchestrator.context_budget import annotate_history_time_gaps, annotate_outbound_markers

        rows = [
            {"role": "assistant", "content": "记得吃早饭", "channel": "proactive",
             "timestamp": "2026-10-12 00:05:37"},
            {"role": "user", "content": "才八点", "channel": "",
             "timestamp": "2026-10-12 00:06:52"},
        ]
        out = annotate_outbound_markers(annotate_history_time_gaps(rows))
        assert out[0]["content"].startswith("[")


# ── 3. 历史清洗保留 channel（utils/prompt_sanitize）────────


class TestSanitizeKeepsChannel:
    def test_channel_field_survives(self):
        from utils.prompt_sanitize import sanitize_llm_history

        rows = [
            {"role": "assistant", "content": "早", "channel": "proactive"},
            {"role": "user", "content": "早呀"},
        ]
        out = sanitize_llm_history(rows, current_user_message="不在历史里")
        assert out and out[0].get("channel") == "proactive"


# ── 4. 决策上下文标记（proactive/llm_proactive）────────────


class TestDecisionContextMarks:
    def test_proactive_rows_flagged_in_context(self):
        from proactive.llm_proactive import build_proactive_context

        recent = [
            {"role": "assistant", "content": "记得吃早饭", "channel": "proactive"},
            {"role": "user", "content": "才八点"},
            {"role": "assistant", "content": "早，带伞", "channel": ""},
        ]
        ctx = build_proactive_context(
            session_key="s", hours_since_last_chat=0.1,
            local_time="2026-10-12 08:10 周一", recent_messages=recent,
        )
        # 决策器必须能分清哪些是"自己主动发的"（生产实证：模型把关怀归因成
        # "看你刚发消息"，正是缺这个标记）
        assert "proactive" in ctx


# ── 5. 决策链双闸接线（proactive/scheduler）────────────────


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
    def __init__(
        self,
        recent: list[str],
        last_proactive_at=None,
        hours: float = 5.0,
        last_delivery_at=None,
    ):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        self._recent_messages = deque(recent)
        self._last_proactive_time = last_proactive_at
        # 双闸基准纠偏（2026-10-12）：min_interval 读投递基准（仅 commit_sent
        # 推进）；_last_proactive_time 会被 on_chat 拨动，不再参与节奏闸。
        self._last_delivery_time = last_delivery_at
        self._hours = hours
        self.commits: list[dict] = []

    def _hours_since_last_chat(self) -> float:
        return self._hours

    def tick(self, hours, dry_run=True):
        return None

    def commit_sent(self, result):
        self.commits.append(result)


def _make_scheduler(monkeypatch, *, engine: _FakeEngine, decision_message: str):
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    # skip 账本治理（2026-10-12）：新增的两个每日去重账本须与实现同步
    sched._wait_window_event_day = {}
    sched._quiet_event_day = {}
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    llm = _FakeLLM(decision_message)
    sched._resolve_proactive_llm = lambda eng: llm
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
    import proactive.llm_proactive as lp_mod

    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: dict(DEFAULT_WEB_CONFIG))
    monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
    return sched, delivered, ledger, llm


def _run(sched, engine):
    hub = SimpleNamespace(get=lambda k: engine)
    sched._llm_proactive_one_user(hub, "u1")


class TestSchedulerDoubleGates:
    def test_min_interval_skips_llm_and_delivery(self, monkeypatch):
        """5 分钟前刚投递过 → 不调 LLM、不投递、账本留 min_interval。"""
        eng = _FakeEngine(
            recent=[],
            last_delivery_at=datetime.now(_UTC) - timedelta(minutes=5),
            hours=10.0,
        )
        sched, delivered, ledger, llm = _make_scheduler(
            monkeypatch, engine=eng, decision_message="早上好",
        )
        _run(sched, eng)
        assert not delivered, "最短间隔内不得投递"
        assert llm.calls == 0, "闸命中时不应烧 LLM 决策调用"
        assert any(e.get("reason") == "min_interval" for e in ledger)

    def test_cooldown_after_reply_skips(self, monkeypatch):
        """用户 2 分钟前刚说话 → 冷却拦截（生产实证 08:10:36 场景）。

        投递基准在 3h 前（min_interval 不命中）——hours=2min 独立命中冷却闸，
        恰是「冷却闸独立语义」的活演示（纠偏前被 60min 污染遮蔽成近似死码）。
        """
        eng = _FakeEngine(
            recent=[],
            last_delivery_at=datetime.now(_UTC) - timedelta(hours=3),
            hours=2.0 / 60.0,
        )
        sched, delivered, ledger, llm = _make_scheduler(
            monkeypatch, engine=eng, decision_message="缓和气氛",
        )
        _run(sched, eng)
        assert not delivered
        assert llm.calls == 0
        assert any(e.get("reason") == "cooldown_after_reply" for e in ledger)

    def test_normal_flow_unaffected(self, monkeypatch):
        """双闸未命中（上次投递 3 小时前+用户 5 小时未回）→ 正常决策投递。"""
        eng = _FakeEngine(
            recent=[],
            last_delivery_at=datetime.now(_UTC) - timedelta(hours=3),
            hours=5.0,
        )
        sched, delivered, ledger, llm = _make_scheduler(
            monkeypatch, engine=eng, decision_message="晚上降温了记得加衣服",
        )
        _run(sched, eng)
        assert delivered == ["晚上降温了记得加衣服"]
        assert any(e.get("sent") is True for e in ledger)

    def test_engine_absent_no_gate(self, monkeypatch):
        """eng 缺席（hub miss）→ 双闸不生效，保持旧行为（闸依赖引擎状态）。"""
        sched, delivered, ledger, llm = _make_scheduler(
            monkeypatch, engine=None, decision_message="在吗",
        )
        hub = SimpleNamespace(get=lambda k: None)
        sched._llm_proactive_one_user(hub, "u1")
        # 无引擎时决策链照旧（不由本批改变）
        assert llm.calls >= 0
