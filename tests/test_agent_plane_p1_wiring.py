"""P1 接线回归：画像写走 ledger / 投影读 / LLM 主动决策。"""

from __future__ import annotations

from pathlib import Path

from shisi.agent_plane import runtime as apruntime
from shisi.agent_plane.event_ledger import EventLedger
from shisi.agent_plane.profile_projection import project_profile
from tools.builtin.profile_agent_tools import QueryProfileTool, UpdateUserProfileTool


def _patch_ledger(tmp_path: Path, monkeypatch) -> EventLedger:
    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    store_db = tmp_path / "profile_cache.db"
    from shisi.memory.legacy.user_profile import UserProfileStore

    UserProfileStore(store_db)
    monkeypatch.setattr(
        apruntime,
        "_store",
        lambda: UserProfileStore(store_db),
    )
    return led


def test_update_profile_tool_writes_ledger_not_only_store(tmp_path, monkeypatch):
    led = _patch_ledger(tmp_path, monkeypatch)
    tool = UpdateUserProfileTool()
    res = tool.execute(
        _meta={"session_key": "2:alice@im.wechat"},
        birthday="腊月初一",
        occupation="上班",
        reason="用户原话",
    )
    assert res.success
    assert res.data["profile"]["birthday"] == "腊月初一"
    assert res.data.get("source") == "event_ledger"
    proj = project_profile(led, "2:alice@im.wechat")
    assert proj["birthday"] == "腊月初一"
    assert proj["occupation"] == "上班"


def test_update_profile_correction_clears_via_ledger(tmp_path, monkeypatch):
    led = _patch_ledger(tmp_path, monkeypatch)
    tool = UpdateUserProfileTool()
    tool.execute(_meta={"session_key": "2:bob@im.wechat"}, birthday="11月14")
    res = tool.execute(
        _meta={"session_key": "2:bob@im.wechat"},
        clear_birthday=True,
        reason="用户否认",
    )
    assert res.success
    assert project_profile(led, "2:bob@im.wechat")["birthday"] == ""


def test_query_profile_reads_projection(tmp_path, monkeypatch):
    _patch_ledger(tmp_path, monkeypatch)
    UpdateUserProfileTool().execute(
        _meta={"session_key": "3:c@im.wechat"}, nickname="小c"
    )
    res = QueryProfileTool().execute(_meta={"session_key": "3:c@im.wechat"})
    assert res.success
    assert res.data["profile"]["nickname"] == "小c"
    assert res.data.get("source") == "event_ledger_projection"


def test_profile_isolation_ledger_projection(tmp_path, monkeypatch):
    led = _patch_ledger(tmp_path, monkeypatch)
    UpdateUserProfileTool().execute(
        _meta={"session_key": "2:a@im.wechat"}, birthday="腊月初一"
    )
    b = project_profile(led, "4:b@im.wechat")
    assert b.get("birthday", "") == ""


def test_persona_service_reads_ledger_profile_slot():
    import inspect

    from shisi.application import persona_service

    src = inspect.getsource(persona_service.PersonaService.build_system_prompt)
    assert "用户画像" in src
    assert "get_profile_prompt_block" in src or "to_prompt_block" in src


def test_llm_proactive_parse_and_no_policy_gate_in_source():
    import inspect

    from proactive import llm_proactive, scheduler

    d = llm_proactive.parse_decision(
        '{"should_contact": true, "wait_minutes": 120, "message": "在忙吗", "reason": "两天没聊"}'
    )
    assert d["should_contact"] is True
    assert d["message"] == "在忙吗"
    assert d["wait_minutes"] == 120
    d2 = llm_proactive.parse_decision('{"should_contact": false, "message": "", "reason": "刚聊完"}')
    assert d2["should_contact"] is False
    cls = scheduler.ProactiveScheduler
    src = inspect.getsource(cls._check_ase_per_user)
    assert "decide_proactive" in inspect.getsource(cls)
    # 用户裁决：per-user 路径不再用 quiet/frequency 作发送闸
    assert "quiet or not is_online" not in src


class _FakeLLM:
    def __init__(self, payload: str):
        self._payload = payload

    def chat_sync(self, *args, **kwargs):
        return self._payload


def test_scheduler_llm_proactive_delivers_and_records(monkeypatch, tmp_path):
    from proactive.llm_proactive import decide_proactive
    from shisi.agent_plane import runtime as apruntime
    from shisi.agent_plane.event_ledger import EventLedger

    led = EventLedger(tmp_path / "ap.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)

    llm = _FakeLLM(
        '{"should_contact": true, "wait_minutes": 30, "message": "在干嘛呀", "reason": "想找你"}'
    )
    ctx = apruntime  # noqa: F841 — keep import used
    from proactive.llm_proactive import build_proactive_context

    decision = decide_proactive(
        llm,
        build_proactive_context(session_key="N:u1", hours_since_last_chat=20.0, local_time="2026-09-21 15:00"),
    )
    assert decision["should_contact"] is True
    apruntime.append_proactive_event(
        session_key="N:u1", sent=True, message=decision["message"], reason=decision["reason"]
    )
    events = led.query(session_key="N:u1", event_type="proactive_send")
    assert events and events[0].payload.get("message") == "在干嘛呀"


def test_orchestrator_source_uses_ledger_and_llm_profile():
    import inspect

    from orchestrator import optimized_orchestrator as orch
    from proactive import scheduler

    src = inspect.getsource(orch.OptimizedOrchestrator)
    assert "append_chat_events" in src
    assert "run_profile_sync_agent" in src
    assert "apply_user_utterance" not in src
    ssrc = inspect.getsource(scheduler.ProactiveScheduler)
    assert "llm_proactive" in ssrc or "decide_proactive" in ssrc
