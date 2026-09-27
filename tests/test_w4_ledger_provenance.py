"""W4 · 因果账本来源链（缺陷 I / 包任务 9）。

钉住：
1. 工具逐调用入账：EVENT_TOOL_CALL 带 call_id、结果状态、关联 turn；
2. memory_write 带 turn_id / character_id（回放可关联本轮）；
3. prompt_slots 带事实版本来源与知识片段摘要（不新建全文影子库）；
4. 回放能回答「本轮用了哪些事实版本与知识片段、哪个调用失败」。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from shisi.agent_plane.event_ledger import (
    EVENT_MEMORY_WRITE,
    EVENT_PROMPT_SLOTS,
    EVENT_TOOL_CALL,
    EventLedger,
    set_default_ledger,
)
from shisi.agent_plane.runtime import (
    append_chat_events,
    append_memory_write_event,
    append_tool_call_event,
)
from shisi.application.memory_service import ShisiMemoryService
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION = "1:peer-a@im.wechat"
TURN = "turn-abc"
REPLY = "reply-xyz"


class FakeVectorMemory:
    def __init__(self) -> None:
        self.stored: list = []

    async def store_fact(self, fact, category="general", confidence=0.5, user_key=""):
        self.stored.append(fact)

    def store_chat_sync(self, *a, **k):
        return None

    def health_check(self):
        return {"available": True}


def test_tool_call_event_has_call_id_status_and_turn():
    from shisi.agent_plane.event_ledger import default_ledger

    led = EventLedger(Path(tempfile.mkdtemp()) / "led.db")
    old = default_ledger()
    set_default_ledger(led)
    try:
        append_tool_call_event(
            session_key=SESSION,
            tool_name="set_reminder",
            call_id="call-1",
            provider="builtin",
            success=False,
            error="permission_denied",
            turn_id=TURN,
            character_id="c1",
        )
        events = led.query(session_key=SESSION, event_type=EVENT_TOOL_CALL)
        assert len(events) == 1
        ev = events[0]
        assert ev.payload["call_id"] == "call-1"
        assert ev.payload["success"] is False
        assert ev.payload["error"] == "permission_denied"
        assert ev.turn_id == TURN
        bundle = led.replay(session_key=SESSION, turn_id=TURN)
        assert bundle.tool_ops, "回放必须能看到工具调用"
        assert bundle.tool_ops[0]["call_id"] == "call-1"
    finally:
        set_default_ledger(old)


def test_memory_write_carries_turn_and_character():
    from shisi.agent_plane.event_ledger import default_ledger

    led = EventLedger(Path(tempfile.mkdtemp()) / "led.db")
    old = default_ledger()
    set_default_ledger(led)
    try:
        append_memory_write_event(
            session_key=SESSION,
            facts=[{"fact": "用户喜欢猫", "fact_id": 3, "action": "inserted"}],
            action="write",
            turn_id=TURN,
            reply_id=REPLY,
            character_id="c1",
        )
        events = led.query(session_key=SESSION, event_type=EVENT_MEMORY_WRITE)
        assert len(events) == 1
        assert events[0].turn_id == TURN
        assert events[0].reply_id == REPLY
        assert events[0].character_id == "c1"
    finally:
        set_default_ledger(old)


def test_record_fact_ledger_includes_turn_id():
    tmp = Path(tempfile.mkdtemp())
    sm = StructuredMemory(str(tmp / "s.db"))
    svc = ShisiMemoryService(
        structured_memory=sm, vector_memory=FakeVectorMemory(), db_path=tmp / "f.db"
    )
    sm.add_chat_turn("我养了猫", "好呀", session_id=SESSION, character_id="c1", turn_id=TURN)
    from shisi.agent_plane.event_ledger import default_ledger

    led = EventLedger(tmp / "led.db")
    old = default_ledger()
    set_default_ledger(led)
    try:
        svc.record_fact("用户养猫", session_key=SESSION, turn_id=TURN, category="pet")
        events = led.query(session_key=SESSION, event_type=EVENT_MEMORY_WRITE)
        assert events, "record_fact 必须入账"
        assert events[0].turn_id == TURN or events[0].payload.get("facts", [{}])[0].get("turn_id") == TURN
    finally:
        set_default_ledger(old)


def test_prompt_slots_include_fact_sources_and_knowledge():
    from shisi.agent_plane.event_ledger import default_ledger

    led = EventLedger(Path(tempfile.mkdtemp()) / "led.db")
    old = default_ledger()
    set_default_ledger(led)
    try:
        append_chat_events(
            session_key=SESSION,
            character_id="c1",
            turn_id=TURN,
            reply_id=REPLY,
            user_msg="我生日是三月",
            reply="记住了",
            slots={
                "emotion_tag": "warm",
                "turn_id": TURN,
                "memory_fact_sources": [{"fact_id": 3, "source_last_id": 10}],
                "knowledge_snippets": ["用户喜欢猫"],
            },
        )
        events = led.query(session_key=SESSION, event_type=EVENT_PROMPT_SLOTS)
        assert len(events) == 1
        slots = events[0].payload.get("slots") or {}
        assert slots.get("memory_fact_sources"), "槽必须带事实版本来源"
        assert slots.get("knowledge_snippets"), "槽必须带知识片段"
        bundle = led.replay(session_key=SESSION, turn_id=TURN)
        assert bundle.slots.get("memory_fact_sources")
        assert bundle.slots.get("knowledge_snippets")
    finally:
        set_default_ledger(old)


def test_replay_answers_which_tool_failed():
    from shisi.agent_plane.event_ledger import default_ledger

    led = EventLedger(Path(tempfile.mkdtemp()) / "led.db")
    old = default_ledger()
    set_default_ledger(led)
    try:
        append_tool_call_event(
            session_key=SESSION, tool_name="weather", call_id="c-ok",
            success=True, turn_id=TURN,
        )
        append_tool_call_event(
            session_key=SESSION, tool_name="set_reminder", call_id="c-bad",
            success=False, error="invalid_time", turn_id=TURN,
        )
        bundle = led.replay(session_key=SESSION, turn_id=TURN)
        failed = [t for t in bundle.tool_ops if t.get("success") is False]
        assert failed and failed[0]["tool"] == "set_reminder"
        assert failed[0]["error"] == "invalid_time"
    finally:
        set_default_ledger(old)
