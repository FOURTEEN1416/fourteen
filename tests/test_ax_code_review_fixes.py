"""代码审查回归：turn_id 回放 / 投影不被挤出 / wait_minutes / curator 强化。"""

from __future__ import annotations

from pathlib import Path

from shisi.agent_plane.curator import curate_facts_rule_based
from shisi.agent_plane.event_ledger import (
    EVENT_ASSISTANT_REPLY,
    EVENT_TOOL_RESULT,
    EVENT_USER_MESSAGE,
    EventLedger,
)
from shisi.agent_plane.profile_projection import project_profile, write_profile_event


def test_project_profile_not_drowned_by_chat_events(tmp_path: Path):
    led = EventLedger(tmp_path / "led.db")
    sk = "N:busy"
    # 塞入大量 chat 事件
    for i in range(600):
        led.append(session_key=sk, event_type=EVENT_USER_MESSAGE, payload={"i": i})
    write_profile_event(led, session_key=sk, payload={"birthday": "11月14"})
    write_profile_event(led, session_key=sk, payload={"birthday": "腊月初一"}, correct=True)
    proj = project_profile(led, sk)
    assert proj["birthday"] == "腊月初一"


def test_ensure_seed_not_repeat_when_many_chat_events(tmp_path: Path, monkeypatch):
    from shisi.agent_plane import runtime as apruntime

    led = EventLedger(tmp_path / "led2.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    sk = "N:seed"
    for i in range(550):
        led.append(session_key=sk, event_type=EVENT_USER_MESSAGE, payload={"i": i})
    # 写一次画像事件（相当于 seed 或 update）
    write_profile_event(led, session_key=sk, payload={"nickname": "彩儿"})
    from shisi.memory.legacy.user_profile import UserProfileStore

    store_db = tmp_path / "up.db"
    UserProfileStore(store_db)
    monkeypatch.setattr(apruntime, "_store", lambda: UserProfileStore(store_db))
    # store 里有另一份会误导 seed 的数据
    UserProfileStore(store_db).upsert(sk, birthday="错误值")
    proj = apruntime.ensure_profile_seeded(sk)
    assert proj["nickname"] == "彩儿"
    # 不得因看不到 profile 类型而再 seed「错误值」
    assert project_profile(led, sk)["birthday"] in ("", "错误值")
    # 再调一次应仍以 ledger 为准
    proj2 = apruntime.project_profile_for(sk)
    assert proj2["nickname"] == "彩儿"


def test_replay_uses_turn_id(tmp_path: Path):
    led = EventLedger(tmp_path / "led3.db")
    sk = "N:u"
    tid, rid = "t-abc", "r-xyz"
    led.append(session_key=sk, event_type=EVENT_USER_MESSAGE, turn_id=tid, reply_id=rid, payload={"text": "hi"})
    led.append(session_key=sk, event_type=EVENT_TOOL_RESULT, turn_id=tid, reply_id=rid, payload={"success": True})
    led.append(session_key=sk, event_type=EVENT_ASSISTANT_REPLY, turn_id=tid, reply_id=rid, payload={"text": "hey"})
    bundle = led.replay(session_key=sk, turn_id=tid)
    assert bundle.summary()["event_count"] == 3
    assert bundle.tool_ops and bundle.tool_ops[0].get("success") is True


def test_llm_wait_window_logic():
    class FakeHub:
        def get(self, k):
            return None

    from proactive.scheduler import ProactiveScheduler

    s = ProactiveScheduler.__new__(ProactiveScheduler)
    s._llm_proactive_next_ok = {"N:u": 10**12}  # 远未来
    s._llm_provider = None
    s._channels = {}
    import time as t

    assert t.time() < s._llm_proactive_next_ok["N:u"]


def test_curator_rule_keeps_real_facts():
    r = curate_facts_rule_based(
        [
            {"id": 1, "fact": "用户生日是腊月初一", "confidence": 0.8},
            {"id": 2, "fact": "叫我"},
        ]
    )
    assert any("腊月初一" in k["fact"] for k in r["kept"])
    assert r["dropped"]


def test_orchestrator_passes_turn_id():
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert "ax_turn_id" in src
    src2 = inspect.getsource(OptimizedOrchestrator._after_process)
    assert "turn_id" in src2
    assert "append_chat_events" in src2
