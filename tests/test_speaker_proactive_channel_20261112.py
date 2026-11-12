"""B1 · 主动投递渠道归属（2026-11-12）。

契约（原子 B1）：
- ``_deliver`` 新增 keyword-only ``channel``，生效渠道 =
  ``channel or ("proactive_voice" if voice else "proactive")``；
- 日期线 ``important_date`` / 手动线 ``manual`` / 决策线缺省 ``proactive``，
  三线各只写一次 ``_record_outbound``；
- voice=True 且未显式传渠道 → ``proactive_voice``；
- 显式渠道优先于 voice 推导；
- 投递失败（含 judge 拒绝）零记账。

只替身外部依赖（LLM/通道/同意/记忆），不触真实 data/ 与网络。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from proactive.scheduler import ProactiveScheduler

SESSION = "4:o9x@im.wechat"
CHAR = "charA"


class _Memory:
    """最小记忆替身：同时满足决策线读历史与投递线写历史。"""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def get_chat_context(self, **_kw):
        return [], ""

    def record_outbound_message(self, **kw):
        self.records.append(kw)
        return True


def _scheduler() -> ProactiveScheduler:
    return ProactiveScheduler(ase_engine=None, send_message_func=None)


def _allow_judge(monkeypatch) -> None:
    from proactive.post_draft_judge import JudgeVerdict

    monkeypatch.setattr(
        "proactive.post_draft_judge.judge_proactive_message",
        lambda *a, **k: JudgeVerdict(True, "ok"),
    )


def _allow_consent(monkeypatch) -> None:
    import api.consent as consent

    monkeypatch.setattr(consent, "outbound_allowed_for_session_sync", lambda sk: True)


def _delivery_scheduler(monkeypatch, *, send_ok: bool = True):
    """构造只走 ``_deliver`` 的调度器：真实记账，其余外部依赖替身。"""
    s = _scheduler()
    mem = _Memory()
    s.set_memory(mem)
    monkeypatch.setattr(s, "_resolve_character_id", lambda sk: CHAR)
    monkeypatch.setattr(s, "_send_targeted", lambda *a, **k: send_ok)
    monkeypatch.setattr(s, "_run_blocking", lambda fn: fn())
    _allow_judge(monkeypatch)
    _allow_consent(monkeypatch)
    return s, mem


# ═══════════════════════════════════════════════════════════════
#  1. 签名契约
# ═══════════════════════════════════════════════════════════════


def test_deliver_channel_is_keyword_only_with_empty_default():
    sig = inspect.signature(ProactiveScheduler._deliver)
    param = sig.parameters["channel"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default == ""


# ═══════════════════════════════════════════════════════════════
#  2. 缺省推导：文字 active / 语音 active_voice
# ═══════════════════════════════════════════════════════════════


def test_deliver_default_text_records_proactive(monkeypatch):
    s, mem = _delivery_scheduler(monkeypatch)

    assert s._deliver("今天风有点大", session_key=SESSION) is True
    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "proactive"


def test_deliver_voice_without_explicit_channel_records_proactive_voice(monkeypatch):
    s, mem = _delivery_scheduler(monkeypatch)

    assert s._deliver("想听你的声音", session_key=SESSION, voice=True) is True
    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "proactive_voice"


@pytest.mark.parametrize(
    ("channel", "voice", "expected"),
    [
        ("important_date", True, "important_date"),   # 显式覆盖语音推导
        ("manual", False, "manual"),
        ("custom", True, "custom"),
    ],
)
def test_explicit_channel_wins_over_voice_default(monkeypatch, channel, voice, expected):
    s, mem = _delivery_scheduler(monkeypatch)

    assert s._deliver("一句", session_key=SESSION, voice=voice, channel=channel) is True
    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == expected


# ═══════════════════════════════════════════════════════════════
#  3. 失败零记账
# ═══════════════════════════════════════════════════════════════


def test_deliver_failure_records_nothing(monkeypatch):
    s, mem = _delivery_scheduler(monkeypatch, send_ok=False)

    assert s._deliver("发不出去", session_key=SESSION, channel="manual") is False
    assert mem.records == []


def test_judge_rejection_records_nothing_even_with_explicit_channel(monkeypatch):
    from proactive.post_draft_judge import JudgeVerdict

    s, mem = _delivery_scheduler(monkeypatch)
    monkeypatch.setattr(
        "proactive.post_draft_judge.judge_proactive_message",
        lambda *a, **k: JudgeVerdict(False, "test_drop"),
    )

    assert s._deliver("空泛问候", session_key=SESSION, channel="important_date") is False
    assert mem.records == []


# ═══════════════════════════════════════════════════════════════
#  4. 三线端到端：日期 / 手动 / 决策各只记一次且渠道正确
# ═══════════════════════════════════════════════════════════════


def test_date_wish_records_once_as_important_date(monkeypatch):
    from proactive import proactive_context as pctx

    s, mem = _delivery_scheduler(monkeypatch)
    monkeypatch.setattr(s, "_resolve_proactive_llm", lambda eng=None: None)
    monkeypatch.setattr(s, "_persist_throttle_ledger", lambda: None)
    monkeypatch.setattr(
        "utils.speaker_switches.speaker_reminder_enabled", lambda: False,
    )
    monkeypatch.setattr(
        pctx, "load_full_persona_hint", lambda cid, user_key=None: "",
    )
    s._important_dates_sent = set()

    s._send_date_wish(
        SESSION, "2026-11-12", label="生日", kind="birthday", names="阿闹",
    )

    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "important_date"
    assert "生日快乐" in mem.records[0]["message"]


def test_manual_send_records_once_as_manual(monkeypatch):
    s, mem = _delivery_scheduler(monkeypatch)
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    monkeypatch.setattr(
        s, "_quiet_hours_for_session",
        lambda *a, **k: ((h + 2) % 24, (h + 3) % 24),
    )
    eng = SimpleNamespace(
        _daily_message_count=0,
        _generate_and_return=lambda chosen, commit=True: {
            "message": "在忙吗", "type": "care_meal",
        },
        commit_sent=lambda result: None,
    )
    s.ase = SimpleNamespace(get=lambda key: eng)
    monkeypatch.setattr(
        "agent_core.events.record_event", lambda *a, **k: "evt",
    )

    result = s.manual_send_now("session-manual", message_type="care_meal")

    assert result["status"] == "sent"
    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "manual"
    assert mem.records[0]["message"] == "在忙吗"


def test_decision_line_records_once_as_proactive(monkeypatch):
    from proactive import llm_proactive as lp
    from proactive import proactive_context as pctx
    from shisi.agent_plane import runtime

    s, mem = _delivery_scheduler(monkeypatch)
    events: list[dict] = []
    monkeypatch.setattr(s, "_is_quiet_hours", lambda: False)
    monkeypatch.setattr(s, "_is_quiet_hours_for", lambda cfg: False)
    monkeypatch.setattr(s, "_resolve_proactive_llm", lambda eng=None: object())
    monkeypatch.setattr(
        pctx, "load_full_persona_hint", lambda cid, user_key=None: f"persona:{cid}",
    )
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid: f"persona:{cid}")
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(runtime, "project_profile_for", lambda sk: {})
    monkeypatch.setattr(runtime, "get_profile_prompt_block", lambda sk: "")
    monkeypatch.setattr(runtime, "append_proactive_event", lambda **kw: events.append(kw))
    monkeypatch.setattr(
        "agent_core.events.record_event", lambda *a, **k: "evt",
    )
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx, **kwargs: {
            "should_contact": True,
            "message": "那书看得怎么样了",
            "reason": "follow",
            "wait_minutes": None,
        },
    )
    hub = SimpleNamespace(
        get=lambda sk: SimpleNamespace(
            _knowledge_character_id=CHAR, _last_user_message="我在读书",
        ),
    )

    s._llm_proactive_one_user(hub, "session-decision")

    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "proactive"
    assert mem.records[0]["message"] == "那书看得怎么样了"


# ═══════════════════════════════════════════════════════════════
#  5. B2：日期/手动 per-user 静默窗
# ═══════════════════════════════════════════════════════════════


def _window_cfg(start: int, end: int):
    """构造 `proactive_config.effective` 的最小替身。"""
    values = {"quiet_hours_start": start, "quiet_hours_end": end}
    return SimpleNamespace(
        overridden_keys=frozenset(values),
        get=lambda key, default=None: values.get(key, default),
    )


def _patch_effective_windows(monkeypatch, windows: dict[str, tuple[int, int]]):
    calls: list[tuple[str, str]] = []

    def fake_effective(session_key, character_id=None):
        calls.append((str(session_key), str(character_id or "")))
        start, end = windows[str(session_key)]
        return _window_cfg(start, end)

    monkeypatch.setattr("utils.proactive_config.effective", fake_effective)
    return calls


def _patch_local_hour(monkeypatch, hour: int = 2) -> None:
    import datetime as dt

    monkeypatch.setattr(
        "proactive.scheduler._local_now",
        lambda: dt.datetime(2026, 11, 12, hour, 0),
    )


def test_quiet_hours_for_session_reads_effective_config(monkeypatch):
    s = _scheduler()
    s._quiet_hours = (23, 7)
    calls: list[tuple[str, str]] = []

    def fake_effective(session_key, character_id=None):
        calls.append((str(session_key), str(character_id or "")))
        return _window_cfg(1, 2)

    monkeypatch.setattr("utils.proactive_config.effective", fake_effective)

    assert s._quiet_hours_for_session("4:wx_a@im.wechat", "charA") == (1, 2)
    assert calls == [("4:wx_a@im.wechat", "charA")]


def test_quiet_hours_for_session_falls_back_to_global_on_config_error(monkeypatch):
    s = _scheduler()
    s._quiet_hours = (22, 6)

    def _boom(*_a, **_k):
        raise RuntimeError("effective broke")

    monkeypatch.setattr("utils.proactive_config.effective", _boom)

    assert s._quiet_hours_for_session("4:wx_a@im.wechat", "charA") == (22, 6)


def test_quiet_hours_for_session_uses_default_without_instance_window(monkeypatch):
    s = object.__new__(ProactiveScheduler)

    def _boom(*_a, **_k):
        raise RuntimeError("effective broke")

    monkeypatch.setattr("utils.proactive_config.effective", _boom)

    assert s._quiet_hours_for_session("4:wx_a@im.wechat", "charA") == (23, 7)


def test_manual_send_uses_per_user_quiet_window(monkeypatch):
    s, mem = _delivery_scheduler(monkeypatch)
    _patch_local_hour(monkeypatch, 2)
    effective_calls = _patch_effective_windows(
        monkeypatch, {"4:wx_a@im.wechat": (23, 7), "4:wx_b@im.wechat": (8, 18)},
    )
    engines = {
        "4:wx_a@im.wechat": SimpleNamespace(
            _daily_message_count=0,
            _generate_and_return=lambda chosen, commit=True: {"message": "A 不该生成"},
            commit_sent=lambda result: None,
        ),
        "4:wx_b@im.wechat": SimpleNamespace(
            _daily_message_count=0,
            _generate_and_return=lambda chosen, commit=True: {
                "message": "在忙吗", "type": "care_meal",
            },
            commit_sent=lambda result: None,
        ),
    }
    s.ase = SimpleNamespace(get=lambda key: engines[key])
    monkeypatch.setattr("agent_core.events.record_event", lambda *a, **k: "evt")
    deliver_calls: list[dict] = []
    real_deliver = s._deliver

    def spy_deliver(message, **kwargs):
        deliver_calls.append(kwargs)
        return real_deliver(message, **kwargs)

    s._deliver = spy_deliver

    quiet = s.manual_send_now("4:wx_a@im.wechat", message_type="care_meal")
    assert quiet == {
        "status": "rejected", "delivered": False, "reason": "quiet_hours",
        "session_key": "4:wx_a@im.wechat",
    }, "静默拒绝的返回结构必须保持不变"
    assert deliver_calls == [], "A 静默不得进入投递"
    assert engines["4:wx_a@im.wechat"]._daily_message_count == 0

    sent = s.manual_send_now("4:wx_b@im.wechat", message_type="care_meal")
    assert sent["status"] == "sent"
    assert len(deliver_calls) == 1
    assert deliver_calls[0]["quiet_hours"] == (8, 18)
    assert len(mem.records) == 1
    assert mem.records[0]["channel"] == "manual"
    # 生成前 / 发送前各解析一次（A 只到生成前那一次）。
    assert len([c for c in effective_calls if c[0] == "4:wx_b@im.wechat"]) == 2


def test_date_wish_passes_per_user_quiet_window(monkeypatch):
    from proactive import proactive_context as pctx

    s, mem = _delivery_scheduler(monkeypatch)
    _patch_local_hour(monkeypatch, 2)
    _patch_effective_windows(
        monkeypatch, {"4:wx_a@im.wechat": (23, 7), "4:wx_b@im.wechat": (8, 18)},
    )
    monkeypatch.setattr(s, "_resolve_proactive_llm", lambda eng=None: None)
    monkeypatch.setattr(s, "_persist_throttle_ledger", lambda: None)
    monkeypatch.setattr(
        "utils.speaker_switches.speaker_reminder_enabled", lambda: False,
    )
    monkeypatch.setattr(
        pctx, "load_full_persona_hint", lambda cid, user_key=None: "",
    )
    s._important_dates_sent = set()

    def _send_targeted(message, session_key=None, quiet_hours=None, voice=False):
        # 复刻 _send_targeted 的静默判据，证明 A 被拦、B 放行。
        return not (
            quiet_hours is not None and s._quiet_hours_contains(quiet_hours)
        )

    monkeypatch.setattr(s, "_send_targeted", _send_targeted)
    deliver_calls: list[dict] = []
    real_deliver = s._deliver

    def spy_deliver(message, **kwargs):
        deliver_calls.append(kwargs)
        return real_deliver(message, **kwargs)

    s._deliver = spy_deliver

    s._send_date_wish(
        "4:wx_a@im.wechat", "2026-11-12", label="生日", kind="birthday", names="A",
    )
    s._send_date_wish(
        "4:wx_b@im.wechat", "2026-11-12", label="生日", kind="birthday", names="B",
    )

    assert len(deliver_calls) == 2, "日期线每次投递都必须带 quiet_hours"
    assert deliver_calls[0]["quiet_hours"] == (23, 7)
    assert deliver_calls[1]["quiet_hours"] == (8, 18)
    assert len(mem.records) == 1, "A 静默零记账，B 放行记一次"
    assert mem.records[0]["channel"] == "important_date"
    assert mem.records[0]["session_id"] == "4:wx_b@im.wechat"


# ═══════════════════════════════════════════════════════════════
#  6. B3：手动线生成/记账分离
# ═══════════════════════════════════════════════════════════════


class _ManualEngine:
    """手动脉的最小 ASEEngine 契约桩（commit=False 不扣，commit_sent 才扣）。"""

    def __init__(self, message: str = "在忙吗", scene: str | None = None,
                 generation_fails: bool = False) -> None:
        self.message = message
        self.scene = scene
        self.generation_fails = generation_fails
        self._daily_message_count = 0
        self.calls: list[tuple[str, str, bool]] = []
        self.commits: list[dict] = []
        self.sent_history: list[dict] = []
        self.order: list[str] = []
        self.scene_commits: list[bool] = []
        self.urgency = SimpleNamespace(total=5.0)

    def _update_urgency(self, _hours: float) -> None:
        pass

    def _hours_since_last_chat(self) -> float:
        return 9.0

    def _check_scene_triggers(self, commit: bool = True):
        self.scene_commits.append(commit)
        return self.scene

    def _select_type_by_urgency(self) -> str:
        return "morning"

    def _generate_and_return(self, msg_type, commit: bool = True):
        label = str(getattr(msg_type, "value", msg_type))
        self.calls.append(("generate", label, commit))
        if self.generation_fails:
            return None
        if commit:
            self._daily_message_count += 1
        return {"message": self.message, "type": label}

    def _record_and_return(self, scene, commit: bool = True):
        self.calls.append(("record", str(scene), commit))
        if commit:
            self._daily_message_count += 1
        return {"message": self.message, "type": str(scene), "_scene": scene}

    def commit_sent(self, result: dict) -> None:
        if not result or result.get("_committed"):
            return
        result["_committed"] = True
        self.order.append("commit")
        self.commits.append(result)
        self._daily_message_count += 1

    def record_sent_entry(self, entry: dict) -> None:
        self.order.append("record")
        self.sent_history.append(entry)


def _patch_non_quiet_window(monkeypatch, s) -> None:
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    monkeypatch.setattr(
        s, "_quiet_hours_for_session",
        lambda *a, **k: ((h + 2) % 24, (h + 3) % 24),
    )


@pytest.mark.parametrize(
    ("message_type", "scene", "expected_method", "expected_type"),
    [
        ("care_meal", None, "generate", "care_meal"),
        (None, "morning", "record", "morning"),
        (None, None, "generate", "morning"),
    ],
)
def test_manual_generation_branches_pass_commit_false(
    monkeypatch, message_type, scene, expected_method, expected_type,
):
    s, _mem = _delivery_scheduler(monkeypatch)
    _patch_non_quiet_window(monkeypatch, s)
    eng = _ManualEngine(scene=scene)
    s.ase = SimpleNamespace(get=lambda key: eng)
    monkeypatch.setattr("agent_core.events.record_event", lambda *a, **k: "evt")

    result = s.manual_send_now("session-b3", message_type=message_type)

    assert result["status"] == "sent"
    assert eng.calls == [(expected_method, expected_type, False)]
    assert eng._daily_message_count == 0, (
        "生成 commit=False 不扣；受理后 commit_sent 记 1；手动自测按既有产品"
        "语义归还日配额（只回滚配额，冷却/连击保留）"
    )
    assert eng.order == ["commit", "record"], "commit_sent 必须在 record_sent_entry 之前"
    assert eng.sent_history == [eng.commits[0]]
    if scene:
        assert eng.scene_commits == [False], "场景触发器不得在投递前 commit"


def test_manual_not_delivered_does_not_commit(monkeypatch):
    s, _mem = _delivery_scheduler(monkeypatch, send_ok=False)
    _patch_non_quiet_window(monkeypatch, s)
    eng = _ManualEngine()
    s.ase = SimpleNamespace(get=lambda key: eng)
    monkeypatch.setattr("agent_core.events.record_event", lambda *a, **k: "evt")

    result = s.manual_send_now("session-b3", message_type="care_meal")

    assert result["status"] == "not_delivered"
    assert eng.calls == [("generate", "care_meal", False)]
    assert eng.commits == []
    assert eng.sent_history == []
    assert eng._daily_message_count == 0


def test_manual_generation_failure_does_not_commit(monkeypatch):
    s, _mem = _delivery_scheduler(monkeypatch)
    _patch_non_quiet_window(monkeypatch, s)
    eng = _ManualEngine(generation_fails=True)
    s.ase = SimpleNamespace(get=lambda key: eng)

    result = s.manual_send_now("session-b3", message_type="care_meal")

    assert result["status"] == "failed"
    assert eng.calls == [("generate", "care_meal", False)]
    assert eng.commits == []
    assert eng._daily_message_count == 0


def test_manual_commit_sent_same_result_is_idempotent(monkeypatch):
    s, _mem = _delivery_scheduler(monkeypatch)
    _patch_non_quiet_window(monkeypatch, s)
    eng = _ManualEngine()
    s.ase = SimpleNamespace(get=lambda key: eng)
    monkeypatch.setattr("agent_core.events.record_event", lambda *a, **k: "evt")

    assert s.manual_send_now("session-b3", message_type="care_meal")["status"] == "sent"
    first = eng.commits[0]
    assert len(eng.commits) == 1 and eng._daily_message_count == 0

    eng.commit_sent(first)  # 重复提交同一 result
    assert len(eng.commits) == 1
    assert eng._daily_message_count == 0


def test_real_engine_commit_sent_same_result_is_idempotent(tmp_path):
    from proactive.ase_engine import ASEEngine

    engine = ASEEngine(
        state_path=str(tmp_path / "ase_state_b3.json"), generation_mode="template",
    )
    result = {"message": "同一句", "type": "care_meal"}
    engine.commit_sent(result)
    engine.commit_sent(result)
    assert engine._daily_message_count == 1


def test_quiet_hours_start_equals_end_means_disabled(monkeypatch):
    """2026-11-12 复核修复：start==end 必须是「关闭静默」，不是全天静默。

    与 ``ASEEngine._in_quiet_hours`` 同语义；旧实现走跨零点分支
    ``hour >= start or hour < end``，任何时刻都为 True。
    """
    import proactive.scheduler as sched_mod

    monkeypatch.setattr(
        sched_mod, "_local_now", lambda: __import__("datetime").datetime(2026, 11, 12, 3, 0)
    )
    assert ProactiveScheduler._quiet_hours_contains((0, 0)) is False
    assert ProactiveScheduler._quiet_hours_contains((23, 7)) is True


def test_manual_and_date_channels_have_outbound_markers():
    """2026-11-12 B1 终审修复：manual/important_date 必须带因果标记。"""
    from orchestrator.context_budget import annotate_outbound_markers

    history = [
        {"role": "assistant", "content": "早呀", "channel": "manual"},
        {"role": "assistant", "content": "生日快乐", "channel": "important_date"},
        {"role": "assistant", "content": "普通回复", "channel": ""},
    ]
    out = annotate_outbound_markers(history)
    assert out[0]["content"].startswith("[主动]")
    assert out[1]["content"].startswith("[重要日期]")
    assert out[2]["content"] == "普通回复"
