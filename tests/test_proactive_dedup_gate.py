"""主动消息同义重复治理 — 出站硬闸 + 决策线接线 + 软引导。

生产实证：角色对同一用户连发三条几乎一样的「哼，你终于想起我了？」变体 ——
决策线（``scheduler._llm_proactive_one_user`` → ``decide_proactive``）生成的
message 没有硬去重闸（ase_engine 生成线的 avoid_block 管不到决策线）。
行业参照 scallopbot「出站前确定性硬闸」+ SillyTavern「已发内容喂回生成
上下文」防重复惯例。
"""

from __future__ import annotations

import json
import time
from collections import deque
from types import SimpleNamespace

# ══════════════════════════════════════════════════════════
#  ① 纯函数硬闸 is_duplicate_proactive
# ══════════════════════════════════════════════════════════

class TestIsDuplicateProactive:
    def _call(self, message: str, recent: list[str], window: int = 8) -> bool:
        from proactive.llm_proactive import is_duplicate_proactive

        return is_duplicate_proactive(message, recent, window=window)

    def test_exact_same_after_normalization(self):
        """归一化（去标点/空白/emoji、lower）后完全相同 → 拒发。"""
        assert self._call("哼，你终于想起我了？", ["哼！你终于想起我了～"]) is True

    def test_variant_same_prefix6_short(self):
        """同前 6 字 + 长度差 ≤3 + 都 ≤20 字 → 拒发（拦同义换皮）。"""
        assert self._call("终于想起我了呀", ["终于想起我了？"]) is True

    def test_production_spam_chain(self):
        """生产三连场景：首条已发后，原样/换标点/同前缀换皮全部拦截。"""
        recent = ["哼，你终于想起我了？"]
        assert self._call("哼，你终于想起我了？", recent) is True
        assert self._call("哼，你终于想起我了！", recent) is True
        assert self._call("哼，你终于想起我了呢", recent) is True

    def test_different_topic_passes(self):
        """不同话题正常放行。"""
        assert self._call("早，记得吃早饭", ["晚上降温了，多穿点"]) is False

    def test_long_sentences_not_killed(self):
        """长句（>20 字）不启用高相似规则 —— 不误杀正常话题延续。"""
        a = "今天天气不错我们一起去公园散步顺便买点好吃的吧"
        b = "今天天气不错我们要不要出门走走看看樱花开了没有呢"
        assert self._call(b, [a]) is False

    def test_prefix6_same_but_len_diff_gt3_passes(self):
        """前 6 字相同但长度差 >3 → 放行（钉住长度差条款）。"""
        a = "终于想起我了"
        b = "终于想起我了呀今天过得好不好呀跟我说说嘛"
        assert self._call(b, [a]) is False

    def test_window_boundary_inside(self):
        """尾部第 window 条仍在比对窗口内 → 命中。"""
        recent = [f"无关话题{i}" for i in range(7)] + ["晚上降温了"]
        assert self._call("晚上降温了", recent, window=8) is True

    def test_window_boundary_outside(self):
        """第 window+1 条（最旧）已滑出窗口 → 不再参与比对。"""
        recent = ["晚上降温了"] + [f"无关话题{i}" for i in range(8)]
        assert self._call("晚上降温了", recent, window=8) is False

    def test_empty_recent_passes(self):
        """无历史不拦（空消息由上游 sanitize 闸负责）。"""
        assert self._call("在吗", []) is False


# ══════════════════════════════════════════════════════════
#  ② 接线：_llm_proactive_one_user 投递前调硬闸
# ══════════════════════════════════════════════════════════

class _FakeLLM:
    """单次决策桩：恒返回 should_contact=True + 固定 message。"""

    def __init__(self, message: str):
        self._message = message
        self.calls = 0

    def chat_sync(self, user, system_prompt="", temperature=0.4):
        self.calls += 1
        return json.dumps(
            {
                "should_contact": True,
                "message": self._message,
                "wait_minutes": None,
                "reason": "测试决策",
            },
            ensure_ascii=False,
        )


class _FakeEngine:
    """ASE 引擎桩：只暴露 _llm_proactive_one_user 读到的属性。"""

    def __init__(self, recent: list[str]):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        self._recent_messages = deque(recent)
        self.commits: list[dict] = []

    def _hours_since_last_chat(self) -> float:
        return 5.0

    def tick(self, hours, dry_run=True):
        return None

    def commit_sent(self, result):
        self.commits.append(result)


def _make_scheduler(monkeypatch, *, engine: _FakeEngine, decision_message: str):
    """构造最小 ProactiveScheduler（跳过 __init__），全部依赖打桩。

    返回 (sched, delivered, ledger, persists)：分别记录投递、账本事件、落盘。
    """
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    sched._resolve_proactive_llm = lambda eng: _FakeLLM(decision_message)
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
    return sched, delivered, ledger, persists


def _run(sched, engine):
    hub = SimpleNamespace(get=lambda k: engine)
    sched._llm_proactive_one_user(hub, "u1")


class TestSchedulerGate:
    def test_duplicate_message_not_delivered(self, monkeypatch):
        """命中硬闸：不投递、不记账 commit、留 duplicate_suppressed 事件。"""
        eng = _FakeEngine(recent=["哼，你终于想起我了？"])
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng, decision_message="哼，你终于想起我了！",
        )
        _run(sched, eng)
        assert delivered == [], "同义重复消息不得投递"
        assert eng.commits == [], "未投递不得 commit_sent（不进配额/去重窗口）"
        hits = [e for e in ledger if e.get("reason") == "duplicate_suppressed"]
        assert hits and hits[0]["sent"] is False, "必须留 duplicate_suppressed 账本事件"

    def test_duplicate_sets_next_ok_plus_5min(self, monkeypatch):
        """命中硬闸：llm_proactive_next_ok = now+5min，且账本落盘。"""
        eng = _FakeEngine(recent=["哼，你终于想起我了？"])
        sched, _, _, persists = _make_scheduler(
            monkeypatch, engine=eng, decision_message="哼，你终于想起我了！",
        )
        before = time.time()
        _run(sched, eng)
        after = time.time()
        ok = sched._llm_proactive_next_ok.get("u1")
        assert ok is not None, "命中后必须设等待窗"
        assert before + 290 <= ok <= after + 310, f"next_ok 应为 now+5min，实际 {ok - before:.0f}s"
        assert persists, "等待窗必须落盘（重启不重置）"

    def test_fresh_message_delivered_normally(self, monkeypatch):
        """未命中硬闸：正常投递并 commit_sent（回归守卫）。"""
        eng = _FakeEngine(recent=["晚上降温了，多穿点"])
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng, decision_message="早，记得吃早饭",
        )
        _run(sched, eng)
        assert delivered == ["早，记得吃早饭"], "不同话题必须正常放行"
        assert len(eng.commits) == 1
        assert not [e for e in ledger if e.get("reason") == "duplicate_suppressed"]

    def test_gate_reads_engine_recent_tail(self, monkeypatch):
        """硬闸数据源必须是 eng._recent_messages 尾部（而非空列表）。"""
        eng = _FakeEngine(recent=["早，记得吃早饭"])
        sched, delivered, _, _ = _make_scheduler(
            monkeypatch, engine=eng, decision_message="早，记得吃早饭",
        )
        _run(sched, eng)
        assert delivered == [], "决策消息与 eng._recent_messages 相同时必须被拦"


# ══════════════════════════════════════════════════════════
#  ③ 软引导：recent_messages 注入段追加防重复指令
# ══════════════════════════════════════════════════════════

class TestSoftGuidance:
    def test_context_carries_anti_repeat_instruction(self):
        from proactive.llm_proactive import build_proactive_context

        ctx = build_proactive_context(
            session_key="2:wx@im.wechat",
            hours_since_last_chat=5.0,
            local_time="2026-10-12 10:00 Monday",
            recent_messages=[
                {"role": "assistant", "content": "哼，你终于想起我了？", "timestamp": "2026-10-12T01:00:00"},
            ],
        )
        assert "同义换皮" in ctx, "注入段必须带「不重复/不同义换皮」指令"
        assert "完全不同的切入点" in ctx, "连续等待场景必须要求换切入点"

    def test_soft_guidance_absent_without_recent_messages(self):
        """无 recent_messages 时该指令随段落一起缺席（宁缺毋串）。"""
        from proactive.llm_proactive import build_proactive_context

        ctx = build_proactive_context(
            session_key="2:wx@im.wechat",
            hours_since_last_chat=5.0,
            local_time="2026-10-12 10:00 Monday",
        )
        assert "同义换皮" not in ctx
