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


# ── 否认称呼否决门（2026-10-14「十七」生产事故根治）──────────────
#
# 生产实证：用户 09-21 说「我叫十七」→ profile 写入；此后十余轮否认
# （「我不是十七」「谁是十七」）→ profile agent 三次清空（10-03/10-07）
# 又三次从旧历史"纠正"写回；用户每轮被叫「十七」。根因=写路径只认
# 「历史自称」不认「后来的否认」。修法=写侧否决 + 存量自愈 + 读侧守卫。


def _seed_denial(led: EventLedger, session: str, nickname: str) -> None:
    """账本里写「用户自称 + 用户否认」两段用户消息（检测判据来源）。"""
    led.append(session_key=session, event_type="user_message", payload={"text": f"我叫{nickname}"})
    led.append(session_key=session, event_type="user_message", payload={"text": f"{nickname}是谁"})


def test_write_gate_rejects_denied_nickname(tmp_path, monkeypatch):
    """写侧否决：被否认过的称呼拒绝写回画像。"""
    led = _patch_ledger(tmp_path, monkeypatch)
    _seed_denial(led, "2:a@im.wechat", "十七")
    UpdateUserProfileTool().execute(
        _meta={"session_key": "2:a@im.wechat"},
        nickname="十七",
        occupation="上学",
        reason="历史里用户自称过十七",
    )
    proj = project_profile(led, "2:a@im.wechat")
    assert proj.get("nickname", "") == "", "被否认的称呼不得写回"
    assert proj.get("occupation") == "上学", "否决不阻塞其余字段"


def test_write_gate_selfheals_existing_denied_nickname(tmp_path, monkeypatch):
    """存量自愈：投影里已有被否认的称呼时，任一写路径触发清空更正。"""
    led = _patch_ledger(tmp_path, monkeypatch)
    # 先正常写入（此时尚无否认历史）
    UpdateUserProfileTool().execute(
        _meta={"session_key": "2:b@im.wechat"}, nickname="十七", reason="用户自称"
    )
    assert project_profile(led, "2:b@im.wechat")["nickname"] == "十七"
    # 用户随后否认 → 下一次任意画像写入必须触发清空
    led.append(session_key="2:b@im.wechat", event_type="user_message", payload={"text": "我不是十七"})
    UpdateUserProfileTool().execute(
        _meta={"session_key": "2:b@im.wechat"}, occupation="上学", reason="日常更新"
    )
    assert project_profile(led, "2:b@im.wechat")["nickname"] == "", "存量被否认称呼必须自愈清空"


def test_read_gate_hides_denied_nickname_from_prompt(tmp_path, monkeypatch):
    """读侧守卫：脏数据仍在投影时，画像 prompt 段不得注入被否认称呼。"""
    led = _patch_ledger(tmp_path, monkeypatch)
    # 直接绕门写一条（模拟历史脏数据），再否认
    led.append(session_key="2:c@im.wechat", event_type="profile_update", payload={"nickname": "十七"})
    led.append(session_key="2:c@im.wechat", event_type="user_message", payload={"text": "谁是十七"})
    block = apruntime.get_profile_prompt_block("2:c@im.wechat")
    assert "十七" not in block, "被否认的称呼不得进入画像 prompt 段"


def test_denied_gate_does_not_affect_normal_nickname(tmp_path, monkeypatch):
    """非否认昵称零改动（不得误伤正常画像）。"""
    led = _patch_ledger(tmp_path, monkeypatch)
    led.append(session_key="2:d@im.wechat", event_type="user_message", payload={"text": "我叫阿树"})
    UpdateUserProfileTool().execute(
        _meta={"session_key": "2:d@im.wechat"}, nickname="阿树", reason="用户自称"
    )
    assert project_profile(led, "2:d@im.wechat")["nickname"] == "阿树"
    assert "阿树" in apruntime.get_profile_prompt_block("2:d@im.wechat")


def test_denial_memory_survives_user_message_window(tmp_path, monkeypatch):
    """否认记忆性（2026-10-14 自纠）：否决记录持久化——即使用户消息全被删，
    被拒名仍从 profile_correct.denied_name 读回，写门照常拒绝。"""
    led = _patch_ledger(tmp_path, monkeypatch)
    led.append(session_key="2:e@im.wechat", event_type="user_message", payload={"text": "我叫十七"})
    led.append(session_key="2:e@im.wechat", event_type="user_message", payload={"text": "我不是十七"})
    # 首次自愈：把「十七」写进持久否决记录
    UpdateUserProfileTool().execute(_meta={"session_key": "2:e@im.wechat"}, occupation="上学")
    # 模拟账本 prune：删掉全部用户消息（否认句消失）
    import sqlite3

    with sqlite3.connect(str(led.db_path)) as conn:
        conn.execute("DELETE FROM event_ledger WHERE event_type = 'user_message'")
        conn.commit()
    # 无用户消息可扫，但持久否决记录仍在 → 写门继续拒绝「十七」
    res = UpdateUserProfileTool().execute(
        _meta={"session_key": "2:e@im.wechat"}, nickname="十七", reason="旧历史自称"
    )
    assert res.success
    assert project_profile(led, "2:e@im.wechat").get("nickname", "") == "", (
        "用户消息窗口滑出后，持久否决记录必须继续拦截"
    )


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
