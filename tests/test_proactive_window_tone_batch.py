"""窗口死寂与零信息问候根治批 红测（2026-10-17，五方向合订）。

生产取证（2026-10-17 诊断，账本+日志+文件三重互证）：
- 主通道 token 24h TTL 到期（10-15 16:39）→ 10-16 08:11 起连续 11 次
  ``prepare failed`` 白烧决策 LLM —— 静默死无告警（方向A）；
- 消噪批 F1 上线当天，决策模型转战零信息问候（10-16 16:14/17:19 两条
  「早上好，你今天感觉怎么样？有没有什么特别的事情想要和我分享的呢？」），
  硬规则 11/13 是 prompt 软约束，无确定性闸门（方向E）；
- 候选竞争被 ``hook_keywords`` 门卡死：活材料空仓时有候选也不择优，
  泛问候直通；空 message 走到 sanitize 才留账，根因面模糊（方向④）；
- unanswered_limit 每 5 分钟写一条相同 skip 事件刷屏（方向C）；
- 生产根目录 token 文件被测试夹具污染（wx_user_1，09-22 冻结）——conftest
  隔离面缺 wechat 两路径（方向B）。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

# ── 方向A①：token 窗口探针 ─────────────────────────────


def _write_tokens(tmp_path, entries: dict) -> str:
    p = tmp_path / "context_tokens.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _patch_probe_path(monkeypatch, path: str | None):
    """把探针的 per-channel 路径指向测试文件（None=文件不存在）。"""
    from wechat_direct import channel_paths

    monkeypatch.setattr(
        channel_paths,
        "context_tokens_path",
        lambda user_id, slot=0: __import__("pathlib").Path(path) if path else __import__("pathlib").Path(path or "/nonexistent/x.json"),
    )


class TestTokenWindowState:
    def test_fresh(self, tmp_path, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, _write_tokens(tmp_path, {"u": {"token": "t", "ts": time.time() - 3600}}))
        st = token_window_state(7)
        assert st["state"] == "fresh" and st["age_hours"] is not None and st["age_hours"] < 2

    def test_dying(self, tmp_path, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, _write_tokens(tmp_path, {"u": {"token": "t", "ts": time.time() - 21 * 3600}}))
        st = token_window_state(7)
        assert st["state"] == "dying"

    def test_dead(self, tmp_path, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, _write_tokens(tmp_path, {"u": {"token": "t", "ts": time.time() - 30 * 3600}}))
        st = token_window_state(7)
        assert st["state"] == "dead"

    def test_empty_entries(self, tmp_path, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, _write_tokens(tmp_path, {}))
        assert token_window_state(7)["state"] == "empty"

    def test_missing_file(self, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, None)
        assert token_window_state(7)["state"] == "missing"

    def test_corrupt_file_fails_open_as_error(self, tmp_path, monkeypatch):
        from wechat_direct.wechat_connector import token_window_state

        p = tmp_path / "context_tokens.json"
        p.write_text("{not json", encoding="utf-8")
        _patch_probe_path(monkeypatch, str(p))
        # error=探针故障：调用方按未知放行（宁宽勿误），不得抛异常
        assert token_window_state(7)["state"] == "error"

    def test_newest_entry_wins(self, tmp_path, monkeypatch):
        """多条目取最新 ts（旧条目死、新条目活 → fresh）。"""
        from wechat_direct.wechat_connector import token_window_state

        _patch_probe_path(monkeypatch, _write_tokens(tmp_path, {
            "old": {"token": "a", "ts": time.time() - 90 * 3600},
            "new": {"token": "b", "ts": time.time() - 3600},
        }))
        assert token_window_state(7)["state"] == "fresh"


# ── 方向E①：零信息问候检测面 ────────────────────────────


class TestZeroContentGreeting:
    def test_production_verbatim_rejected(self):
        """10-16 生产原句两条（消噪批 F1 上线后模型转战形态）。"""
        from proactive.llm_proactive import _is_zero_content_greeting

        assert _is_zero_content_greeting(
            "早上好，你今天感觉怎么样？有没有什么特别的事情想要和我分享的呢？"
        )
        assert _is_zero_content_greeting(
            "今天感觉怎么样？有没有什么特别的事情想要和我分享的呢？"
        )

    def test_bare_greetings_rejected(self):
        from proactive.llm_proactive import _is_zero_content_greeting

        for t in ("在吗", "你今天感觉怎么样？", "早上好", "吃了吗", "最近怎么样", "睡了吗"):
            assert _is_zero_content_greeting(t), t

    def test_concrete_messages_pass(self):
        """含具体事/由头的消息必须放行（宁宽勿误）。"""
        from proactive.llm_proactive import _is_zero_content_greeting

        for t in (
            "哼，三天了都没动静，是忘了还有个叫你来闹的人吗",  # 生产：傲娇破冰
            "你之前问的辩论赛准备得怎么样了",
            "早，昨晚你说的腿还疼吗",  # 问候+具体事混合 → 放行
            "昨天答应叫早的事，到底搞没搞，别装死",
            "看到你说腿疼要硬扛，十四有点担心",
        ):
            assert not _is_zero_content_greeting(t), t

    def test_hook_keyword_exempts(self):
        """消息命中活材料关键词 = 有由头，豁免。"""
        from proactive.llm_proactive import _is_zero_content_greeting

        assert not _is_zero_content_greeting("早上好，辩论赛准备得怎么样了", ["辩论赛"])

    def test_empty_not_greeting(self):
        from proactive.llm_proactive import _is_zero_content_greeting

        assert not _is_zero_content_greeting("")
        assert not _is_zero_content_greeting("   ")


class _RawLLM:
    """返回固定原文的决策 LLM 桩（签名兼容新参透传）。"""

    def __init__(self, raw: str):
        self.raw = raw
        self.calls = 0

    def chat_sync(self, query="", system_prompt="", temperature=0.4,
                  allow_fallback=True, timeout=None):
        self.calls += 1
        return self.raw


# ── 方向E②+④：决策输出契约 ──────────────────────────────


class TestDecisionContract:
    def test_generic_multi_clause_rejected_as_contract(self):
        """零信息多分句堆叠 → 契约拒收翻转 should=False，原句入 reason 取证。"""
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True,
            "message": "早上好，你今天感觉怎么样？有没有什么特别的事情想要和我分享的呢？",
            "reason": "距离上次8小时该关心一下", "wait_minutes": None,
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is False
        assert d["message"] == ""
        assert d["reason"].startswith("generic_opener_contract")
        assert "早上好" in d["reason"]  # 原句留证

    def test_empty_message_flagged_as_contract(self):
        """should=True 但 message 空 → empty_message_contract（生产 10-17 14:21）。"""
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True, "message": "",
            "reason": "有由头但忘了写消息", "wait_minutes": None,
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is False
        assert d["reason"] == "empty_message_contract"

    def test_concrete_message_untouched(self):
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True,
            "message": "哼，三天了都没动静，是忘了还有个叫你来闹的人吗",
            "reason": "由头：沉默三天", "wait_minutes": None,
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is True
        assert "三天了" in d["message"]

    def test_candidate_backfill_without_keywords(self):
        """④根治：无 hook_keywords 候选也参与竞争——泛问候 message 被具体候选
        回填（旧实现 `hook_keywords and candidates` 门卡死，空仓时泛问候直通）。"""
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True, "message": "在干嘛",
            "reason": "r", "wait_minutes": None,
            "candidates": ["你之前问的辩论赛准备得怎么样了"],
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx")  # 不传 hook_keywords
        assert d["should_contact"] is True
        assert d["message"] == "你之前问的辩论赛准备得怎么样了"

    def test_empty_message_backfilled_from_candidates(self):
        """④根治：message 空 + 有候选 → 最优候选回填，不判 empty_message_contract。"""
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True, "message": "",
            "reason": "r", "wait_minutes": None,
            "candidates": ["你之前问的辩论赛准备得怎么样了"],
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is True
        assert d["message"] == "你之前问的辩论赛准备得怎么样了"

    def test_hook_keyword_message_exempt_from_rejection(self):
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM(json.dumps({
            "should_contact": True, "message": "早上好，辩论赛准备得怎么样了",
            "reason": "r", "wait_minutes": None,
        }, ensure_ascii=False))
        d = decide_proactive(llm, "ctx", hook_keywords=["辩论赛"])
        assert d["should_contact"] is True

    def test_false_decision_untouched_by_contract(self):
        from proactive.llm_proactive import decide_proactive

        llm = _RawLLM('{"should_contact": false, "reason": "quiet", "wait_minutes": 60}')
        d = decide_proactive(llm, "ctx")
        assert d["should_contact"] is False and d["wait_minutes"] == 60


# ── 方向E③：空 hooks 显式标注 ───────────────────────────


class TestEmptyHooksMarker:
    def test_empty_hooks_renders_explicit_marker(self):
        from proactive.llm_proactive import build_proactive_context

        ctx = build_proactive_context(
            session_key="7:wx@im.wechat", local_time="2026-10-17 14:00 星期六",
            hooks_text="",
        )
        assert "本轮没有任何具体由头" in ctx
        assert "should_contact=false" in ctx

    def test_live_hooks_render_without_marker(self):
        from proactive.llm_proactive import build_proactive_context

        ctx = build_proactive_context(
            session_key="7:wx@im.wechat", local_time="2026-10-17 14:00 星期六",
            hooks_text="【现在真正值得你接话的事】\n- TA 托你「明早叫起床」",
        )
        assert "明早叫起床" in ctx
        assert "本轮没有任何具体由头" not in ctx


# ── 方向C：确定性闸账本每日降噪 ──────────────────────────


class _SchedStub:
    """最小调度器 stub：只带 _emit_skip_event_daily 所需属性。"""

    pass


def _make_dedup_sched(monkeypatch):
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._gate_skip_event_day = {}
    persists: list[bool] = []
    sched._persist_throttle_ledger = lambda: persists.append(True)
    sched._resolve_character_id = lambda k: "测试角色"
    ledger: list[dict] = []
    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw),
    )
    return sched, ledger, persists


class TestGateSkipDailyDedup:
    def test_first_hit_logs_once_per_day(self, monkeypatch):
        sched, ledger, persists = _make_dedup_sched(monkeypatch)
        from proactive.scheduler import ProactiveScheduler

        assert ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "unanswered_limit") is True
        assert len(ledger) == 1 and ledger[0]["reason"] == "unanswered_limit"
        assert persists, "账本变更必须落盘"

    def test_second_hit_same_day_silenced(self, monkeypatch):
        """生产病灶原型： unanswered_limit 每 5 分钟一条 → 当日只留一条。"""
        sched, ledger, _ = _make_dedup_sched(monkeypatch)
        from proactive.scheduler import ProactiveScheduler

        ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "unanswered_limit")
        assert ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "unanswered_limit") is False
        assert len(ledger) == 1

    def test_different_reason_not_deduped(self, monkeypatch):
        sched, ledger, _ = _make_dedup_sched(monkeypatch)
        from proactive.scheduler import ProactiveScheduler

        ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "unanswered_limit")
        assert ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "token_window_dead") is True
        assert len(ledger) == 2

    def test_different_user_not_deduped(self, monkeypatch):
        sched, ledger, _ = _make_dedup_sched(monkeypatch)
        from proactive.scheduler import ProactiveScheduler

        ProactiveScheduler._emit_skip_event_daily(sched, "4:wx@im.wechat", "unanswered_limit")
        assert ProactiveScheduler._emit_skip_event_daily(sched, "2:wx@im.wechat", "unanswered_limit") is True
        assert len(ledger) == 2


# ── 方向A②：投递失败分类（纯函数）───────────────────────


class TestClassifyDeliverError:
    @pytest.mark.parametrize(
        "err,expected",
        [
            ("微信定向投递未获受理 session=2:x: send_api_rejected", "|window_rejected"),
            ("prepare failed", "|window_rejected"),
            ("channel_unavailable", "|channel_down"),
            ("quiet_hours", "|quiet_hours"),
            ("ConnectionResetError(10054)", "|send_error"),
            ("", ""),
        ],
    )
    def test_mapping(self, err, expected):
        from proactive.scheduler import _classify_deliver_error

        assert _classify_deliver_error(err) == expected


# ── 方向A③：烧前闸接线（决策链级）────────────────────────


class _FakeEngine:
    def __init__(self):
        self.urgency = SimpleNamespace(total=0.0)
        self._last_user_message = ""
        self.response_rate = None
        self._knowledge_character_id = ""
        from collections import deque

        self._recent_messages = deque([])
        self._last_delivery_time = None
        self._unanswered_streak = 0

    def _hours_since_last_chat(self) -> float:
        return 10.0

    def tick(self, hours, dry_run=True):
        return None


class _Hub:
    def __init__(self, engines: dict):
        self._engines = engines

    def get(self, key):
        return self._engines.get(key)

    def known_user_keys(self):
        return list(self._engines)


def _make_chain_scheduler(monkeypatch, *, window_state: dict):
    """镜像 test_unanswered_streak_gate._make_scheduler 的 stub 驱动面。"""
    import proactive.llm_proactive as lp_mod
    import proactive.proactive_hooks as hooks_mod
    import shisi.agent_plane.runtime as runtime_mod
    import wechat_direct.wechat_connector as wc_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    sched._wait_window_event_day = {}
    sched._quiet_event_day = {}
    sched._gate_skip_event_day = {}
    sched._last_deliver_error = ""
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    sched._primary_model_ok = lambda: True
    llm = _RawLLM(json.dumps(
        {"should_contact": True, "message": "哼，三天了都没动静，还活着吗",
         "reason": "由头：沉默三天", "wait_minutes": None},
        ensure_ascii=False,
    ))
    sched._resolve_proactive_llm = lambda eng: llm
    delivered: list[str] = []
    sched._deliver = lambda message, session_key, character_id="": (
        delivered.append(message) or True
    )
    sched._persist_throttle_ledger = lambda: None

    ledger: list[dict] = []
    monkeypatch.setattr(runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw))
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: dict(DEFAULT_WEB_CONFIG))
    monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
    monkeypatch.setattr(hooks_mod, "load_live_hooks", lambda *a, **k: hooks_mod.LiveHooks())
    monkeypatch.setattr(wc_mod, "token_window_state", lambda owner, slot=0: dict(window_state))
    return sched, delivered, ledger, llm


class TestTokenWindowGate:
    KEY = "7:wx_a@im.wechat"

    def test_dead_window_skips_before_burning_llm(self, monkeypatch):
        """窗口确证死 → 不烧决策 LLM、不投递、落 token_window_dead。"""
        eng = _FakeEngine()
        sched, delivered, ledger, llm = _make_chain_scheduler(
            monkeypatch, window_state={"state": "dead", "age_hours": 70.0, "ttl_hours": 24.0},
        )
        sched._llm_proactive_one_user(_Hub({self.KEY: eng}), self.KEY)
        assert llm.calls == 0, "窗口已死禁止白烧决策调用"
        assert not delivered
        assert any(e.get("reason") == "token_window_dead" for e in ledger)

    def test_dead_window_daily_dedup(self, monkeypatch):
        eng = _FakeEngine()
        sched, delivered, ledger, llm = _make_chain_scheduler(
            monkeypatch, window_state={"state": "dead", "age_hours": 70.0, "ttl_hours": 24.0},
        )
        hub = _Hub({self.KEY: eng})
        sched._llm_proactive_one_user(hub, self.KEY)
        sched._llm_proactive_one_user(hub, self.KEY)
        dead_events = [e for e in ledger if e.get("reason") == "token_window_dead"]
        assert len(dead_events) == 1, "同一 user|reason 每日至多一条"

    def test_dying_window_warns_but_proceeds(self, monkeypatch):
        eng = _FakeEngine()
        sched, delivered, ledger, llm = _make_chain_scheduler(
            monkeypatch, window_state={"state": "dying", "age_hours": 21.0, "ttl_hours": 24.0},
        )
        sched._llm_proactive_one_user(_Hub({self.KEY: eng}), self.KEY)
        assert any(e.get("reason") == "token_window_dying" for e in ledger)
        assert llm.calls == 1 and delivered, "临期不拦——窗口还开着"

    def test_missing_window_fails_open(self, monkeypatch):
        """missing/empty/error=未知放行（宁宽勿误），不落 token 事件。"""
        eng = _FakeEngine()
        sched, delivered, ledger, llm = _make_chain_scheduler(
            monkeypatch, window_state={"state": "missing", "age_hours": None, "ttl_hours": 24.0},
        )
        sched._llm_proactive_one_user(_Hub({self.KEY: eng}), self.KEY)
        assert llm.calls == 1
        assert not any(str(e.get("reason", "")).startswith("token_window") for e in ledger)

    def test_probe_exception_fails_open(self, monkeypatch):
        import wechat_direct.wechat_connector as wc_mod

        eng = _FakeEngine()
        sched, delivered, ledger, llm = _make_chain_scheduler(
            monkeypatch, window_state={"state": "fresh", "age_hours": 1.0, "ttl_hours": 24.0},
        )
        monkeypatch.setattr(
            wc_mod, "token_window_state",
            lambda owner, slot=0: (_ for _ in ()).throw(RuntimeError("io boom")),
        )
        sched._llm_proactive_one_user(_Hub({self.KEY: eng}), self.KEY)
        assert llm.calls == 1, "探针故障不得阻塞决策链"


# ── 方向B：conftest 隔离面钉死 ───────────────────────────


class TestConftestIsolationPin:
    def test_context_tokens_path_redirected_to_sandbox(self):
        """隔离面必须在位：模块常量不再指向仓库真实 data/（防再污染）。"""
        from wechat_direct import wechat_connector

        assert "runtime_state_" in str(wechat_connector.CONTEXT_TOKENS_PATH)

    def test_sessions_root_redirected_to_sandbox(self):
        """per-channel 会话根同样隔离（token 探针/注册表读盘确定性=无文件）。"""
        from wechat_direct import channel_paths

        assert "runtime_state_" in str(channel_paths.sessions_root())

    def test_legacy_save_lands_in_sandbox(self, tmp_path):
        """构造无 session_dir 的连接器并触发落盘 → 写沙箱而非真实 data/。"""
        from unittest.mock import MagicMock

        from wechat_direct import wechat_connector
        from wechat_direct.wechat_connector import WeChatConnector

        conn = WeChatConnector(MagicMock(), owner_user_id=42)
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
        conn._save_context_tokens()
        saved = str(wechat_connector.CONTEXT_TOKENS_PATH)
        assert "runtime_state_" in saved
        import os

        assert os.path.exists(saved), "落盘应命中沙箱真源"
