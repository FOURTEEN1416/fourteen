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


# ══════════════════════════════════════════════════════════
#  ④ 去重取样面扩同 owner 组（2026-10-12 聚合根因批）
#
#  比对集此前只取本引擎 _recent_messages —— 同账号 B 通道在 A 通道发过同义
#  换皮后内容闸不可见（生产 duplicate_suppressed 30 天=0 的取样盲区成分）。
#  🔴 挤出陷阱（对抗评审修订）：is_duplicate_proactive 的 [-window:] 切片在
#  固定 window 下会把合并集较早条目整段挤出 —— 恰在目标场景（活跃通道+即将
#  发言的兄弟通道）退化为现状。接线必须 window=max(PROACTIVE_DEDUP_WINDOW,
#  len(merged))；TestSqueezeTrap 锁死。
# ══════════════════════════════════════════════════════════

DUP_MSG = "早安呀今天也要加油"
NIGHT_MSG = "晚安，梦里见"


def _varied(prefix: str, n: int) -> list[str]:
    """n 条互异文案（去重闸的「背景板」，互不同前缀）。"""
    return [f"{prefix}{i}号风好大记得多穿衣服呀" for i in range(n)]


class _GroupHub:
    def __init__(self, engines: dict):
        self._engines = engines

    def get(self, key):
        return self._engines.get(key)

    def known_user_keys(self):
        return list(self._engines)


def _run_hub(sched, hub, key):
    sched._llm_proactive_one_user(hub, key)


class TestDedupSampleHelper:
    def test_same_owner_messages_merged(self):
        """① 同 owner 兄弟通道的消息进比对集（现状无此函数 → 红）。"""
        from proactive.llm_proactive import recent_messages_for_dedup

        eng_b = _FakeEngine(recent=["我自己刚说过的话"])
        eng_a = _FakeEngine(recent=[DUP_MSG])
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "7:wx_a@im.wechat": eng_a,
        })
        merged = recent_messages_for_dedup(hub, "7:wx_b@im.wechat", eng_b)
        assert DUP_MSG in merged, "同 owner 兄弟通道消息必须进比对集"

    def test_other_owner_not_included(self):
        """② 不同 owner 引擎的消息不进比对集（隔离不串扰）。"""
        from proactive.llm_proactive import recent_messages_for_dedup

        eng_b = _FakeEngine(recent=["本通道消息"])
        eng_other = _FakeEngine(recent=[DUP_MSG])
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "9:wx_other@im.wechat": eng_other,
        })
        merged = recent_messages_for_dedup(hub, "7:wx_b@im.wechat", eng_b)
        assert DUP_MSG not in merged, "其他账号的消息不得混入比对集"

    def test_hub_without_known_keys_equivalent_to_legacy(self):
        """③a 无 known_user_keys 的 hub → 返回集与旧单引擎口径逐字节等价。"""
        from proactive.llm_proactive import (
            PROACTIVE_DEDUP_WINDOW,
            recent_messages_for_dedup,
        )

        eng = _FakeEngine(recent=[f"消息{i}" for i in range(12)])
        hub = SimpleNamespace(get=lambda k: eng)  # 无 known_user_keys
        merged = recent_messages_for_dedup(hub, "7:wx_b@im.wechat", eng)
        assert merged == [
            str(m) for m in list(eng._recent_messages)[-PROACTIVE_DEDUP_WINDOW:]
        ]

    def test_single_key_equivalent_to_legacy(self):
        """③b 单键账号 → 与旧口径逐字节等价。"""
        from proactive.llm_proactive import (
            PROACTIVE_DEDUP_WINDOW,
            recent_messages_for_dedup,
        )

        eng = _FakeEngine(recent=[f"消息{i}" for i in range(12)])
        hub = _GroupHub({"7:wx_b@im.wechat": eng})
        merged = recent_messages_for_dedup(hub, "7:wx_b@im.wechat", eng)
        assert merged == [
            str(m) for m in list(eng._recent_messages)[-PROACTIVE_DEDUP_WINDOW:]
        ]

    def test_each_engine_capped_at_window(self):
        """单引擎取样仍截尾窗（合并集有界：≤窗口×通道数）。"""
        from proactive.llm_proactive import (
            PROACTIVE_DEDUP_WINDOW,
            recent_messages_for_dedup,
        )

        eng_b = _FakeEngine(recent=[f"b{i}" for i in range(30)])
        eng_a = _FakeEngine(recent=[f"a{i}" for i in range(30)])
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "7:wx_a@im.wechat": eng_a,
        })
        merged = recent_messages_for_dedup(hub, "7:wx_b@im.wechat", eng_b)
        assert len(merged) == PROACTIVE_DEDUP_WINDOW * 2


class TestSchedulerCrossChannelDedup:
    def test_sibling_channel_message_suppresses(self, monkeypatch):
        """①(决策线) 兄弟通道发过该文案、本通道生成同文案 → duplicate_suppressed
        不投递（现状只查自身 → 投递 → 红）。"""
        eng_b = _FakeEngine(recent=["本通道别的消息"])
        eng_a = _FakeEngine(recent=[DUP_MSG])
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "7:wx_a@im.wechat": eng_a,
        })
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng_b, decision_message=DUP_MSG,
        )
        _run_hub(sched, hub, "7:wx_b@im.wechat")
        assert any(e.get("reason") == "duplicate_suppressed" for e in ledger), (
            "同 owner 兄弟通道已发过的文案必须被内容闸拦下"
        )
        assert delivered == []

    def test_sibling_full_window_oldest_dup_still_seen(self, monkeypatch):
        """兄弟 deque 满 8 条且重复文案在其最旧位 → 仍拦（跨组满窗版）。"""
        eng_b = _FakeEngine(recent=_varied("本通道", 8))
        sibling = _varied("兄弟通道", 8)
        sibling[0] = DUP_MSG
        eng_a = _FakeEngine(recent=sibling)
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "7:wx_a@im.wechat": eng_a,
        })
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng_b, decision_message=DUP_MSG,
        )
        _run_hub(sched, hub, "7:wx_b@im.wechat")
        assert any(e.get("reason") == "duplicate_suppressed" for e in ledger)
        assert delivered == []

    def test_other_owner_duplicate_not_suppressed(self, monkeypatch):
        """②(决策线) 其他账号通道的同文案不拦本通道（隔离）。"""
        eng_b = _FakeEngine(recent=["本通道别的消息"])
        eng_other = _FakeEngine(recent=[DUP_MSG])
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "9:wx_other@im.wechat": eng_other,
        })
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng_b, decision_message=DUP_MSG,
        )
        _run_hub(sched, hub, "7:wx_b@im.wechat")
        assert delivered == [DUP_MSG], "其他账号的消息不得成为本通道判重依据"


class TestSqueezeTrap:
    """挤出陷阱锁死例（突变目标：window 接线改回固定 8 → 本组红）。"""

    def test_merged_front_half_dup_not_squeezed_out(self, monkeypatch):
        """④ 本引擎 deque 恰 8 条且重复文案在其最旧位、兄弟另有 8 条 →
        合并集 16 条下该重复项位于合并集**前半段**；若接线偷懒用固定
        window=8，[-8:] 只剩后半段 → 重复项被挤出 → False → 红。"""
        own = _varied("本通道", 8)
        own[0] = NIGHT_MSG
        eng_b = _FakeEngine(recent=own)
        eng_a = _FakeEngine(recent=_varied("兄弟通道", 8))
        hub = _GroupHub({
            "7:wx_b@im.wechat": eng_b,
            "7:wx_a@im.wechat": eng_a,
        })
        sched, delivered, ledger, _ = _make_scheduler(
            monkeypatch, engine=eng_b, decision_message=NIGHT_MSG,
        )
        _run_hub(sched, hub, "7:wx_b@im.wechat")
        assert any(e.get("reason") == "duplicate_suppressed" for e in ledger), (
            "合并集前半段的重复项不得被固定窗口挤出（window 必须 ≥ len(merged)）"
        )
        assert delivered == []
