"""Speaker 核心件单元测试（第 1 刀，2026-11-12）。

覆盖契约要求的核心行为：能力块三态、ThreadState 全归属委托、提示词段顺序与
空段跳过、拆条开关、deliver 多段汇总/异常不抛、record 双落点。
"""

from __future__ import annotations

from typing import Any

from speaker import (
    CapabilityModel,
    DeliveryResult,
    Speaker,
    ThreadState,
    UtteranceIntent,
)

# ---------------------------------------------------------------------------
# CapabilityModel
# ---------------------------------------------------------------------------


def test_capability_text_only_block():
    block = CapabilityModel(text=True, voice=False, image=False).prompt_block()
    assert "【能力声明】" in block
    assert "只能发送文字消息" in block
    assert "不能发送语音消息" in block
    assert "不能发送文件、链接或图片" in block
    assert "先确认当前能力是否支持" in block


def test_capability_voice_block():
    block = CapabilityModel.detect(voice_available=True).prompt_block()
    assert "也可以发送语音消息" in block
    assert "不能发送语音消息" not in block
    # 2026-11-12 复核修复：同一能力不得重复声明两遍。
    assert block.count("也可以发送语音消息") == 1


def test_capability_detect_never_enables_image():
    model = CapabilityModel.detect(voice_available=True)
    assert model.image is False
    assert "不能发送文件、链接或图片" in model.prompt_block()


# ---------------------------------------------------------------------------
# ThreadState：只做 facade，归属键必须全量显式传
# ---------------------------------------------------------------------------


def test_thread_state_delegates_full_owner(monkeypatch):
    from utils import topic_state

    calls: dict[str, Any] = {}

    def fake_snapshot(session_key, character_id="default"):
        calls["snapshot"] = (session_key, character_id)
        return {"active": [{"title": "周末"}]}

    def fake_render(snapshot):
        calls["render"] = snapshot
        return "话题块"

    def fake_update(session_key, character_id, user_message, assistant_reply="", *, turn_id="", now=None):
        calls["update"] = (session_key, character_id, user_message, assistant_reply, turn_id)
        return {"action": "continue"}

    def fake_take(session_key, character_id="default", *, now=None, consume=False):
        calls["take"] = (session_key, character_id, consume)
        return {"topic": "周末"}

    def fake_save(session_key, character_id, topic, intent, *, wait_minutes=0, planned_at=None):
        calls["save"] = (session_key, character_id, topic, intent, wait_minutes)
        return {"ok": True}

    def fake_recall(session_key, character_id="default", topic_id="", *, now=None):
        calls["recall"] = (session_key, character_id, topic_id)
        return True

    monkeypatch.setattr(topic_state, "get_topic_snapshot", fake_snapshot)
    monkeypatch.setattr(topic_state, "render_topic_state_block", fake_render)
    monkeypatch.setattr(topic_state, "update_after_turn", fake_update)
    monkeypatch.setattr(topic_state, "take_next_plan", fake_take)
    monkeypatch.setattr(topic_state, "save_next_plan", fake_save)
    monkeypatch.setattr(topic_state, "mark_topic_recalled", fake_recall)

    state = ThreadState("4:wx_user", "charA")
    assert state.snapshot() == {"active": [{"title": "周末"}]}
    assert calls["snapshot"] == ("4:wx_user", "charA")
    assert state.text_block() == "话题块"
    assert state.record_turn("你好", "在呢", turn_id="t1") == {"action": "continue"}
    assert calls["update"] == ("4:wx_user", "charA", "你好", "在呢", "t1")
    assert state.take_plan(consume=True) == {"topic": "周末"}
    assert calls["take"] == ("4:wx_user", "charA", True)
    assert state.save_plan("周末", "问问计划", 30) == {"ok": True}
    assert calls["save"] == ("4:wx_user", "charA", "周末", "问问计划", 30)
    assert state.mark_recalled("topic-1") is True
    assert calls["recall"] == ("4:wx_user", "charA", "topic-1")


def test_thread_state_fail_open(monkeypatch):
    from utils import topic_state

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(topic_state, "get_topic_snapshot", boom)
    monkeypatch.setattr(topic_state, "take_next_plan", boom)
    monkeypatch.setattr(topic_state, "save_next_plan", boom)
    monkeypatch.setattr(topic_state, "mark_topic_recalled", boom)

    state = ThreadState("4:wx_user", "charA")
    assert state.snapshot() == {}
    assert state.text_block() == ""
    assert state.take_plan() is None
    assert state.save_plan("t", "i") is None
    assert state.mark_recalled("id") is False


def test_thread_state_empty_character_is_noop(monkeypatch):
    """空 character_id 不得把底层 default 角色的话题块注入进来。"""
    from utils import topic_state

    calls: list[str] = []
    monkeypatch.setattr(
        topic_state, "get_topic_snapshot",
        lambda *a, **k: calls.append("snapshot") or {"active": [1]},
    )
    monkeypatch.setattr(
        topic_state, "take_next_plan", lambda *a, **k: calls.append("take") or {"topic": "x"}
    )
    monkeypatch.setattr(
        topic_state, "save_next_plan", lambda *a, **k: calls.append("save") or {"ok": True}
    )
    monkeypatch.setattr(
        topic_state, "mark_topic_recalled", lambda *a, **k: calls.append("recall") or True
    )

    state = ThreadState("4:wx_user", "")
    assert state.snapshot() == {}
    assert state.text_block() == ""
    assert state.take_plan() is None
    assert state.save_plan("t", "i") is None
    assert state.mark_recalled("id") is False
    assert calls == []

    # 2026-11-12 复核修复：空白串也必须 no-op（str.strip 后才判空）。
    state_blank = ThreadState("4:wx_user", "   ")
    assert state_blank.snapshot() == {}
    assert state_blank.text_block() == ""
    assert state_blank.take_plan() is None
    assert state_blank.save_plan("t", "i") is None
    assert state_blank.mark_recalled("id") is False
    assert calls == []


# ---------------------------------------------------------------------------
# build_system_prompt：顺序 人设 → 能力 → 话题 → base
# ---------------------------------------------------------------------------


def _patch_prompt_sources(monkeypatch, *, persona: str, topic: str):
    from shisi.application import persona_context
    from utils import topic_state

    persona_calls: list[tuple[str, str]] = []

    def fake_persona(character_id, user_key=None):
        persona_calls.append((character_id, user_key))
        return persona

    monkeypatch.setattr(persona_context, "render_persona_hint", fake_persona)
    monkeypatch.setattr(
        topic_state, "get_topic_snapshot", lambda session_key, character_id="default": {"active": [1]}
    )
    monkeypatch.setattr(topic_state, "render_topic_state_block", lambda snapshot: topic)
    return persona_calls


def test_build_system_prompt_segment_order(monkeypatch):
    calls = _patch_prompt_sources(monkeypatch, persona="【人设】温柔", topic="【话题】周末")

    prompt = Speaker().build_system_prompt(
        UtteranceIntent("reminder", "4:wx_user", "charA"), base="【任务】提醒吃药"
    )

    assert prompt.index("【人设】温柔") < prompt.index("【能力声明】")
    assert prompt.index("【能力声明】") < prompt.index("【话题】周末")
    assert prompt.index("【话题】周末") < prompt.index("【任务】提醒吃药")
    assert calls == [("charA", "4:wx_user")]


def test_build_system_prompt_skips_empty_segments(monkeypatch):
    _patch_prompt_sources(monkeypatch, persona="", topic="")

    prompt = Speaker().build_system_prompt(
        UtteranceIntent("reminder", "4:wx_user", ""), base="【任务】提醒吃药"
    )

    assert prompt.startswith("【能力声明】")
    assert prompt.endswith("\n\n【任务】提醒吃药")
    assert "\n\n\n" not in prompt
    assert "【人设】" not in prompt


# ---------------------------------------------------------------------------
# express：拆条唯一入口 + SPEAKER_REPLY 跟随
# ---------------------------------------------------------------------------


def test_express_uses_split_and_follows_reply_switch(monkeypatch):
    import wechat_direct.wechat_connector as wc

    calls: list[tuple[str, int, bool]] = []

    def fake_split(reply, max_segments=2, max_chars=500, *, hard_limit=False):
        calls.append((reply, max_segments, hard_limit))
        return ["第一段", "第二段"]

    monkeypatch.setattr(wc, "split_reply_for_wechat", fake_split)
    monkeypatch.setenv("SPEAKER_REPLY", "0")
    out = Speaker().express(UtteranceIntent("reply", "4:wx_user", ""), "原文")
    assert out == ["第一段", "第二段"]
    assert calls[-1] == ("原文", 1, False)

    monkeypatch.setenv("SPEAKER_REPLY", "1")
    Speaker().express(UtteranceIntent("reply", "4:wx_user", ""), "原文")
    assert calls[-1] == ("原文", 1, True)


def test_express_empty_text_returns_empty():
    assert Speaker().express(UtteranceIntent("reply", "4:wx_user", ""), "   ") == []


# ---------------------------------------------------------------------------
# deliver：多段汇总 + 异常不抛
# ---------------------------------------------------------------------------


def test_deliver_aggregates_segments(monkeypatch):
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(
        wc, "split_reply_for_wechat", lambda *a, **k: ["第一段", "第二段"]
    )
    sent: list[str] = []

    def sender(segment: str) -> str:
        sent.append(segment)
        return segment

    result = Speaker().deliver(
        UtteranceIntent("reply", "4:wx_user", "charA"), "原文", sender=sender
    )
    assert result.accepted is True
    assert result.segments == ("第一段", "第二段")
    assert result.accepted_text == "第一段\n第二段"
    assert result.channel == "reply"
    assert sent == ["第一段", "第二段"]


def test_deliver_exception_returns_false_with_prefix(monkeypatch):
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(
        wc, "split_reply_for_wechat", lambda *a, **k: ["第一段", "第二段"]
    )

    def sender(segment: str) -> str:
        if segment == "第二段":
            raise RuntimeError("channel down")
        return segment

    result = Speaker().deliver(
        UtteranceIntent("reminder", "4:wx_user", ""), "原文", sender=sender
    )
    assert result.accepted is False
    assert "channel down" in result.reason
    assert result.accepted_text == "第一段"
    assert result.segments == ("第一段",)


def test_deliver_empty_sender_return_is_failure(monkeypatch):
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(wc, "split_reply_for_wechat", lambda *a, **k: ["唯一段"])
    result = Speaker().deliver(
        UtteranceIntent("reminder", "4:wx_user", ""), "原文", sender=lambda seg: ""
    )
    assert result.accepted is False
    assert result.reason == "sender_returned_empty"


def test_deliver_explicit_max_segments_uses_real_splitter():
    """显式传 max_segments 时走真实拆条：逐段/前缀契约在生产路径可达。"""
    result = Speaker().deliver(
        UtteranceIntent("reply", "4:wx_user", "charA"),
        "第一段\n第二段\n第三段",
        sender=lambda seg: True,
        max_segments=2,
    )
    assert result.accepted is True
    assert result.segments == ("第一段", "第二段\n第三段")
    assert result.accepted_text == "第一段\n第二段\n第三段"


# ---------------------------------------------------------------------------
# record：chat_history + agent_msg 双落点
# ---------------------------------------------------------------------------


class _MemoryRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record_outbound_message(self, **kwargs):
        self.calls.append(kwargs)
        return True


def test_record_writes_history_and_agent_event(monkeypatch):
    import agent_core.events as events

    event_calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        events,
        "record_event",
        lambda kind, **kwargs: event_calls.append((kind, kwargs)) or "evt-1",
    )
    memory = _MemoryRecorder()
    speaker = Speaker({"memory": memory})
    result = DeliveryResult(
        accepted=True, accepted_text="该吃药了", segments=("该吃药了",), channel="reminder"
    )

    speaker.record(
        UtteranceIntent("reminder", "4:wx_user", "charA"), "原文", result
    )

    assert memory.calls[0]["message"] == "该吃药了"
    assert memory.calls[0]["session_id"] == "4:wx_user"
    assert memory.calls[0]["character_id"] == "charA"
    assert memory.calls[0]["channel"] == "reminder"
    assert event_calls[0][0] == "agent_msg"
    assert event_calls[0][1]["session_key"] == "4:wx_user"
    assert event_calls[0][1]["character_id"] == "charA"
    assert event_calls[0][1]["source"] == "speaker/reminder"
    assert event_calls[0][1]["payload"]["phase"] == "speaker_delivered"


def test_record_skips_unaccepted_result(monkeypatch):
    import agent_core.events as events

    event_calls: list[Any] = []
    monkeypatch.setattr(events, "record_event", lambda *a, **k: event_calls.append(1))
    memory = _MemoryRecorder()
    Speaker({"memory": memory}).record(
        UtteranceIntent("reminder", "4:wx_user", ""),
        "原文",
        DeliveryResult(accepted=False, reason="sender_returned_empty"),
    )
    assert memory.calls == []
    assert event_calls == []


def test_record_failures_do_not_raise(monkeypatch):
    import agent_core.events as events

    class _BrokenMemory:
        def record_outbound_message(self, **kwargs):
            raise RuntimeError("history down")

    monkeypatch.setattr(
        events, "record_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down"))
    )
    Speaker({"memory": _BrokenMemory()}).record(
        UtteranceIntent("reminder", "4:wx_user", "charA"),
        "原文",
        DeliveryResult(accepted=True, accepted_text="原文"),
    )


def test_record_skips_chat_history_when_character_id_empty(monkeypatch):
    """空 character_id 不写历史：无角色归属的 assistant 行会被过滤/继承。"""
    import agent_core.events as events

    monkeypatch.setattr(events, "record_event", lambda *a, **k: "evt-none")
    memory = _MemoryRecorder()
    Speaker({"memory": memory}).record(
        UtteranceIntent("reply", "4:wx_user", ""),
        "原文",
        DeliveryResult(accepted=True, accepted_text="原文"),
    )
    assert memory.calls == []


# ---------------------------------------------------------------------------
# 2026-11-12 独立复核修复：bool sender 契约 + 已受理前缀记账 + 空会话键防护
# ---------------------------------------------------------------------------


def test_deliver_accepts_bool_sender_without_writing_true_text(monkeypatch):
    """既有微信/WS sender 返回 bool True；不得把 "True" 当正文记账。"""
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(wc, "split_reply_for_wechat", lambda *a, **k: ["唯一段"])
    result = Speaker().deliver(
        UtteranceIntent("reminder", "4:wx_user", "charA"), "原文",
        sender=lambda seg: True,
    )
    assert result.accepted is True
    assert result.accepted_text == "唯一段"
    assert result.segments == ("唯一段",)


def test_record_writes_accepted_prefix_even_when_later_segment_failed(monkeypatch):
    """已发前缀永不回滚：accepted=False 但有 accepted_text 时必须落历史。"""
    import agent_core.events as events

    event_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        events, "record_event",
        lambda kind, **kwargs: event_calls.append(kwargs) or "evt-prefix",
    )
    memory = _MemoryRecorder()
    Speaker({"memory": memory}).record(
        UtteranceIntent("reply", "4:wx_user", "charA"),
        "第一段\n第二段",
        DeliveryResult(
            accepted=False, accepted_text="第一段", segments=("第一段",),
            channel="reply", reason="sender_returned_empty",
        ),
    )
    assert memory.calls[0]["message"] == "第一段"
    assert memory.calls[0]["session_id"] == "4:wx_user"
    assert event_calls[0]["payload"]["phase"] == "speaker_partial"
    assert event_calls[0]["payload"]["accepted"] is False


def test_record_skips_chat_history_when_session_key_empty(monkeypatch):
    """空 session_key 不得回落 memory 自身 session_id（防串会话）。"""
    import agent_core.events as events

    monkeypatch.setattr(events, "record_event", lambda *a, **k: "evt-none")
    memory = _MemoryRecorder()
    Speaker({"memory": memory}).record(
        UtteranceIntent("reply", "", "charA"),
        "原文",
        DeliveryResult(accepted=True, accepted_text="原文"),
    )
    assert memory.calls == []


def test_deliver_marks_channel_not_ready_reason(monkeypatch):
    """ChannelNotReadyError 以固定 reason 前缀返回，供调用方不计失败。"""
    import wechat_direct.wechat_connector as wc

    class ChannelNotReadyError(RuntimeError):
        pass

    monkeypatch.setattr(wc, "split_reply_for_wechat", lambda *a, **k: ["唯一段"])

    def sender(segment: str) -> bool:
        raise ChannelNotReadyError("window closed")

    result = Speaker().deliver(
        UtteranceIntent("reminder", "4:wx_user", "charA"), "原文", sender=sender,
    )
    assert result.accepted is False
    assert result.reason.startswith("channel_not_ready:")


def test_deliver_rejects_async_sender_without_false_success(monkeypatch):
    """既有 WS sender 是 async；同步 deliver 不得把协程 repr 当受理正文。"""
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(wc, "split_reply_for_wechat", lambda *a, **k: ["唯一段"])

    async def async_sender(segment: str) -> bool:
        return True

    result = Speaker().deliver(
        UtteranceIntent("reply", "4:wx_user", "charA"), "原文", sender=async_sender,
    )
    assert result.accepted is False
    assert result.reason == "async_sender_unsupported"
    assert result.accepted_text == ""


def test_deliver_string_prefix_is_partial_not_complete(monkeypatch):
    """sender 返回非整段字符串 = 部分受理，不得标 accepted=True。"""
    import wechat_direct.wechat_connector as wc

    monkeypatch.setattr(wc, "split_reply_for_wechat", lambda *a, **k: ["第一段"])

    result = Speaker().deliver(
        UtteranceIntent("reply", "4:wx_user", "charA"), "原文",
        sender=lambda seg: "第一",
    )
    assert result.accepted is False
    assert result.reason == "partial_segment"
    assert result.accepted_text == "第一"
    assert result.segments == ()
