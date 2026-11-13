"""Speaker 接线测试（第 1 刀，2026-11-12）：提醒线 + 重要日期线。

覆盖契约：提醒线 Speaker 提示词/开关/fallback 过守卫/agent_msg 影子账本；
日期线 user_key 透传/开关回旧 prompt/当日幂等不变。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

REMINDER_ENV = "SPEAKER_REMINDER"

GENERIC_SYSTEM_PROMPT = (
    "你是用户的亲密AI伴侣角色，正在微信上主动发消息提醒用户。"
    "输出口语化的一句话（不超过40字）。"
)
TASK_INSTRUCTION = "输出口语化的一句话（不超过40字）。"
PERSONA = "【人设】温柔、爱笑、说话简短"


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv(REMINDER_ENV, raising=False)
    return monkeypatch


def _patch_persona(monkeypatch, text: str = PERSONA):
    from shisi.application import persona_context

    calls: list[tuple[str, str]] = []

    def fake(character_id, user_key=None):
        calls.append((character_id, user_key))
        return text

    monkeypatch.setattr(persona_context, "render_persona_hint", fake)
    return calls


def _patch_empty_topic(monkeypatch):
    from utils import topic_state

    monkeypatch.setattr(
        topic_state,
        "get_topic_snapshot",
        lambda session_key, character_id="default": {},
    )
    monkeypatch.setattr(topic_state, "render_topic_state_block", lambda snapshot: "")


class _CapturingLLM:
    def __init__(self, reply: str = "该吃药了") -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._reply

    _reply = "该吃药了"


def _reminder_task(llm):
    from proactive.reminder_delivery import ReminderDeliveryTask

    return ReminderDeliveryTask(
        structured_memory=None,
        llm=llm,
        character_id_resolver=lambda session_key: "",
    )


def _compose(task, *, content: str = "吃药", character_id: str = "charW"):
    return asyncio.run(
        task._compose_text(  # noqa: SLF001
            {"content": content, "session_key": "4:wx_wire@im.wechat"},
            character_id=character_id,
        )
    )


# ---------------------------------------------------------------------------
# 提醒线：提示词
# ---------------------------------------------------------------------------


def test_reminder_prompt_has_persona_capability_and_task(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "1")
    calls = _patch_persona(monkeypatch)
    _patch_empty_topic(monkeypatch)
    from proactive import reminder_delivery as rd

    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")
    llm = _CapturingLLM()

    assert _compose(_reminder_task(llm)) == "该吃药了"

    prompt = llm.calls[0]["system_prompt"]
    assert prompt.startswith(PERSONA)
    assert "【能力声明】" in prompt
    assert TASK_INSTRUCTION in prompt
    assert prompt.index(PERSONA) < prompt.index("【能力声明】") < prompt.index(TASK_INSTRUCTION)
    assert calls == [("charW", "4:wx_wire@im.wechat")]


def test_reminder_prompt_without_persona_still_keeps_task(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "1")
    _patch_persona(monkeypatch, text="")
    _patch_empty_topic(monkeypatch)
    from proactive import reminder_delivery as rd

    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")
    llm = _CapturingLLM()

    _compose(_reminder_task(llm))

    prompt = llm.calls[0]["system_prompt"]
    assert "【人设】" not in prompt
    assert prompt.startswith("【能力声明】")
    assert prompt.endswith(TASK_INSTRUCTION)


def test_reminder_switch_off_keeps_generic_verbatim(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "0")
    from proactive import reminder_delivery as rd
    from shisi.application import persona_context

    called: list[int] = []
    monkeypatch.setattr(
        persona_context,
        "render_persona_hint",
        lambda *a, **k: called.append(1) or PERSONA,
    )
    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")
    llm = _CapturingLLM()

    _compose(_reminder_task(llm), character_id="")

    assert llm.calls[0]["system_prompt"] == GENERIC_SYSTEM_PROMPT
    assert called == []


# ---------------------------------------------------------------------------
# 提醒线：fallback 必须过 terminal_reply_guard
# ---------------------------------------------------------------------------


def test_reminder_fallback_passes_terminal_guard(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "0")
    import utils.prompt_sanitize as ps
    from proactive import reminder_delivery as rd

    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")
    seen: list[str] = []
    monkeypatch.setattr(
        ps,
        "terminal_reply_guard",
        lambda text, **kwargs: seen.append(str(text)) or "终审后的提醒",
    )

    out = _compose(_reminder_task(None), content="吃药", character_id="")

    assert out == "终审后的提醒"
    assert seen == ["你设定的提醒时间到了：「吃药」。"]


def test_reminder_fallback_stripped_empty_is_not_sent(clean_env, monkeypatch):
    """守卫把兜底文案整段剥空时必须返回空串（由投递层按未送达处理），
    绝不再回落原文——旧实现 `guarded or fallback` 让兜底绕过守卫。"""
    clean_env.setenv(REMINDER_ENV, "0")
    import utils.prompt_sanitize as ps
    from proactive import reminder_delivery as rd

    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")
    monkeypatch.setattr(ps, "terminal_reply_guard", lambda text, **kwargs: "")

    out = _compose(_reminder_task(None), content="吃药", character_id="")

    assert out == ""


def test_reminder_fallback_guard_exception_returns_empty(clean_env, monkeypatch):
    """终审器异常时同样不得按原文发送（异常路径也不得绕过守卫）。"""
    clean_env.setenv(REMINDER_ENV, "0")
    import utils.prompt_sanitize as ps
    from proactive import reminder_delivery as rd

    monkeypatch.setattr(rd, "read_reply_mode", lambda *a, **k: "immersive")

    def boom(*args, **kwargs):
        raise RuntimeError("guard unavailable")

    monkeypatch.setattr(ps, "terminal_reply_guard", boom)

    out = _compose(_reminder_task(None), content="吃药", character_id="")

    assert out == ""


# ---------------------------------------------------------------------------
# 提醒线：agent_msg 影子账本
# ---------------------------------------------------------------------------


def test_reminder_delivery_records_agent_msg(clean_env, monkeypatch):
    import agent_core.events as events
    import api.consent as consent
    from proactive import reminder_delivery as rd

    async def _allow(session_key: str) -> bool:
        return True

    monkeypatch.setattr(consent, "outbound_allowed_for_session", _allow)
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        events,
        "record_event",
        lambda kind, **kwargs: recorded.append((kind, kwargs)) or "evt-1",
    )

    class _SM:
        def __init__(self) -> None:
            self.results: list[tuple[Any, bool]] = []

        def mark_reminder_result(self, reminder_id, delivered):
            self.results.append((reminder_id, delivered))

    sm = _SM()
    task = rd.ReminderDeliveryTask(
        structured_memory=sm,
        llm=None,
        character_id_resolver=lambda session_key: "charW",
        ws_sender=lambda session_key, text: True,
    )

    async def _stub_compose(reminder, *, character_id):
        return "该吃药了"

    task._compose_text = _stub_compose  # type: ignore[method-assign]

    asyncio.run(task._deliver({"id": 7, "session_key": "4:web:wire", "content": "吃药"}))

    assert sm.results == [(7, True)]
    assert recorded and recorded[0][0] == "agent_msg"
    kwargs = recorded[0][1]
    assert kwargs["session_key"] == "4:web:wire"
    assert kwargs["character_id"] == "charW"
    assert kwargs["source"] == "speaker/reminder"
    assert kwargs["payload"]["phase"] == "speaker_delivered"


def test_reminder_delivery_records_accepted_prefix_only(clean_env, monkeypatch):
    """2026-11-13 提醒拆条：只把回执里的已受理前缀写进历史。"""
    import api.consent as consent
    from proactive import reminder_delivery as rd

    async def _allow(session_key: str) -> bool:
        return True

    monkeypatch.setattr(consent, "outbound_allowed_for_session", _allow)

    class _SM:
        def __init__(self) -> None:
            self.results: list[tuple[Any, bool]] = []

        def mark_reminder_result(self, reminder_id, delivered):
            self.results.append((reminder_id, delivered))

    class _Plane:
        def enqueue_send(self, **kwargs):
            return 1

        def wait_for_receipt(self, cid, timeout):
            return {"status": "accepted", "accepted_text": "该吃", "fail_reason": ""}

    class _Mem:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def record_outbound_message(self, **kwargs):
            self.calls.append(kwargs)
            return True

    sm, mem = _SM(), _Mem()
    task = rd.ReminderDeliveryTask(
        structured_memory=sm,
        llm=None,
        character_id_resolver=lambda session_key: "charW",
        memory=mem,
        plane=_Plane(),
    )

    async def _stub_compose(reminder, *, character_id):
        return "该吃药了"

    task._compose_text = _stub_compose  # type: ignore[method-assign]
    asyncio.run(task._deliver({"id": 7, "session_key": "4:web:wire", "content": "吃药"}))

    assert sm.results == [(7, True)]
    assert mem.calls[0]["message"] == "该吃"
    assert mem.calls[0]["channel"] == "reminder"


# ---------------------------------------------------------------------------
# 重要日期线
# ---------------------------------------------------------------------------


class _DateLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat_sync(self, **kwargs):
        self.calls.append(kwargs)
        return "生日快乐呀，今天属于你。"


def _bare_scheduler():
    from proactive.scheduler import ProactiveScheduler

    s = object.__new__(ProactiveScheduler)
    s._important_dates_sent = set()
    s._resolve_character_id = lambda session_key: "charD"
    s._session_llm_resolver = None
    s._resolve_proactive_llm = lambda: None
    s._deliver = lambda message, session_key=None, character_id=None, **kwargs: True
    s._persist_throttle_ledger = lambda: None
    return s


def _patch_date_sources(monkeypatch, *, persona_text: str = PERSONA):
    import proactive.proactive_context as pctx
    from proactive import scheduler as sched_mod
    from shisi.application import persona_context

    persona_calls: list[tuple[str, str]] = []

    def fake_load(character_id, user_key=None):
        persona_calls.append((character_id, user_key))
        return persona_text

    monkeypatch.setattr(pctx, "load_full_persona_hint", fake_load)
    # Speaker 开时日期线只走 persona_context；两处共用同一个记录器，
    # 这样 user_key/角色透传在「开关开/关」两条路径都钉得住。
    monkeypatch.setattr(persona_context, "render_persona_hint", fake_load)
    _patch_empty_topic(monkeypatch)
    monkeypatch.setattr(sched_mod, "sanitize_message", lambda text: text)

    import agent_core.events as events

    monkeypatch.setattr(events, "record_event", lambda *a, **k: "evt-date")
    return persona_calls


def test_date_wish_passes_user_key_and_uses_speaker(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "1")
    calls = _patch_date_sources(monkeypatch)
    sched = _bare_scheduler()
    llm = _DateLLM()
    sched._resolve_proactive_llm = lambda: llm

    sched._send_date_wish(
        "4:wx_date|charD", "2026-11-12", label="生日", kind="birthday", names="默默",
    )

    assert calls == [("charD", "4:wx_date|charD")]
    prompt = llm.calls[0]["system_prompt"]
    assert prompt.startswith(PERSONA)
    assert "【能力声明】" in prompt
    # 2026-11-12 复核修复：人设只出现一次（旧接线把 persona_hint 又当 base
    # 追加，导致人设翻倍、任务指令被挤到人设之前）。
    assert prompt.count(PERSONA) == 1


def test_date_wish_switch_off_keeps_old_prompt(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "0")
    _patch_date_sources(monkeypatch)
    sched = _bare_scheduler()
    llm = _DateLLM()
    sched._resolve_proactive_llm = lambda: llm

    sched._send_date_wish(
        "4:wx_date", "2026-11-12", label="生日", kind="birthday", names="默默",
    )

    assert llm.calls[0]["system_prompt"] == PERSONA
    assert "【能力声明】" not in llm.calls[0]["system_prompt"]


def test_date_wish_same_day_idempotent(clean_env, monkeypatch):
    clean_env.setenv(REMINDER_ENV, "0")
    _patch_date_sources(monkeypatch)
    sched = _bare_scheduler()
    delivered: list[str] = []
    sched._deliver = lambda message, session_key=None, character_id=None, **kwargs: delivered.append(message) or True
    sched._resolve_proactive_llm = lambda: _DateLLM()

    sched._send_date_wish(
        "4:wx_date", "2026-11-12", label="生日", kind="birthday", names="默默",
    )
    sched._send_date_wish(
        "4:wx_date", "2026-11-12", label="生日", kind="birthday", names="默默",
    )

    assert len(delivered) == 1
