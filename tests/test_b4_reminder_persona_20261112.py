"""B-4 提醒线人设注入（2026-11-12）。

契约口径：

- ``SPEAKER_REMINDER``：缺省/空串 = 开；``0/false/off/no`` = 关（复用
  ``utils.speaker_switches._effective``）；
- 开关开且人设可取：``_compose_text`` 的 ``system_prompt`` = 人设前缀 +
  原 generic 任务指令（**人设在前、任务指令在后**）；
- 开关关 / 人设为空 / 人设加载失败：``system_prompt`` 逐字回到 2026-11-12
  之前的 generic 文案；
- ``query`` / ``temperature`` / ``max_tokens`` / ``timeout`` / 守卫 / fallback
  全部不变（本文件同时钉住 query 与 fallback 逐字值）；
- 重要日期线不改代码，只以源码级接线守卫防未来漂移。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

REMINDER_ENV = "SPEAKER_REMINDER"

# 2026-11-12 之前 _compose_text 的 generic system_prompt（character_id 空 →
# who 为空串）。开关关 / 人设为空时必须逐字等于它。
GENERIC_SYSTEM_PROMPT = (
    "你是用户的亲密AI伴侣角色，正在微信上主动发消息提醒用户。"
    "输出口语化的一句话（不超过40字）。"
)
TASK_INSTRUCTION = "输出口语化的一句话（不超过40字）。"
PERSONA_FEATURE = "【人设特征】温柔、爱笑、说话简短"
EXPECTED_QUERY = (
    "设定的提醒到时间了。请用一句符合你口吻的中文提醒用户：吃药。"
    "只输出这一句话本身，不要解释、不要动作神态、不要换行。"
)


@pytest.fixture()
def clean_reminder_env(monkeypatch):
    """删掉 SPEAKER_REMINDER（缺省 = 开），逐用例隔离。"""
    monkeypatch.delenv(REMINDER_ENV, raising=False)
    return monkeypatch


@pytest.fixture()
def fixed_reply_mode(monkeypatch):
    """隔离回复模式读库（本文件不验证模式解析，只验证提示词拼装）。"""
    from proactive import reminder_delivery as rd_mod

    monkeypatch.setattr(rd_mod, "read_reply_mode", lambda *a, **k: "immersive")
    return monkeypatch


class _CapturingLLM:
    """stub LLM：捕获 chat 入参；可选抛异常。"""

    def __init__(self, reply: str = "该吃药了", exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._reply = reply
        self._exc = exc

    async def chat(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._reply


def _task(llm: _CapturingLLM):
    from proactive.reminder_delivery import ReminderDeliveryTask

    return ReminderDeliveryTask(
        structured_memory=None,
        llm=llm,
        character_id_resolver=lambda sk: "",
    )


def _compose(
    task,
    *,
    content: str = "吃药",
    session_key: str = "4:wx_b4@im.wechat",
    character_id: str = "charB4",
) -> str:
    return asyncio.run(
        task._compose_text(  # noqa: SLF001
            {"content": content, "session_key": session_key},
            character_id=character_id,
        )
    )


def _patch_persona(monkeypatch, func):
    import proactive.proactive_context as pc

    monkeypatch.setattr(pc, "load_full_persona_hint", func)


# ---------------------------------------------------------------------------
# 开关矩阵
# ---------------------------------------------------------------------------


def test_speaker_reminder_enabled_defaults_on(clean_reminder_env):
    from utils.speaker_switches import speaker_reminder_enabled

    assert speaker_reminder_enabled() is True


@pytest.mark.parametrize("blank", ["", "   "])
def test_speaker_reminder_enabled_blank_is_on(clean_reminder_env, blank):
    from utils.speaker_switches import speaker_reminder_enabled

    clean_reminder_env.setenv(REMINDER_ENV, blank)
    assert speaker_reminder_enabled() is True


@pytest.mark.parametrize("off_value", ["0", "false", "OFF", " no "])
def test_speaker_reminder_enabled_honours_off(clean_reminder_env, off_value):
    from utils.speaker_switches import speaker_reminder_enabled

    clean_reminder_env.setenv(REMINDER_ENV, off_value)
    assert speaker_reminder_enabled() is False


# ---------------------------------------------------------------------------
# _compose_text：人设前缀
# ---------------------------------------------------------------------------


def test_switch_on_with_persona_prefixes_and_keeps_task_instruction(
    clean_reminder_env, fixed_reply_mode,
):
    clean_reminder_env.setenv(REMINDER_ENV, "1")
    calls: list[tuple[str, str | None]] = []

    def fake_loader(character_id, user_key=None):
        calls.append((character_id, user_key))
        return PERSONA_FEATURE

    _patch_persona(fixed_reply_mode, fake_loader)
    llm = _CapturingLLM()
    out = _compose(_task(llm))

    assert out == "该吃药了"
    assert len(llm.calls) == 1
    prompt = llm.calls[0]["system_prompt"]
    assert PERSONA_FEATURE in prompt
    assert TASK_INSTRUCTION in prompt
    # 人设在前、任务指令在后
    assert prompt.index(PERSONA_FEATURE) < prompt.index(TASK_INSTRUCTION)
    # 多用户隔离：角色与 user_key 显式传给同一人设源
    assert calls == [("charB4", "4:wx_b4@im.wechat")]
    # query / 采样参数不变
    assert llm.calls[0]["query"] == EXPECTED_QUERY
    assert llm.calls[0]["temperature"] == 0.7
    assert llm.calls[0]["max_tokens"] == 120


def test_switch_off_keeps_generic_prompt_verbatim(
    clean_reminder_env, fixed_reply_mode,
):
    clean_reminder_env.setenv(REMINDER_ENV, "0")
    calls: list[int] = []

    def spy_loader(*args, **kwargs):
        calls.append(1)
        return PERSONA_FEATURE

    _patch_persona(fixed_reply_mode, spy_loader)
    llm = _CapturingLLM()
    _compose(_task(llm), character_id="")

    assert llm.calls[0]["system_prompt"] == GENERIC_SYSTEM_PROMPT
    assert calls == [], "开关关时不应读取人设"


def test_switch_off_keeps_generic_prompt_with_nonempty_character(
    clean_reminder_env, fixed_reply_mode,
):
    """生产形态：character_id 非空时，开关关仍保留 who=「（你是{name}）」变体。"""
    clean_reminder_env.setenv(REMINDER_ENV, "0")
    from proactive import reminder_delivery as rd_mod

    fixed_reply_mode.setattr(
        rd_mod.character_resolver, "display_name", lambda cid: "测试角色",
    )
    llm = _CapturingLLM()
    _compose(_task(llm), character_id="charB4")

    expected = (
        "你是用户的亲密AI伴侣角色（你是测试角色），正在微信上主动发消息提醒用户。"
        "输出口语化的一句话（不超过40字）。"
    )
    assert llm.calls[0]["system_prompt"] == expected


def test_empty_persona_falls_back_to_generic(
    clean_reminder_env, fixed_reply_mode,
):
    clean_reminder_env.setenv(REMINDER_ENV, "1")
    _patch_persona(fixed_reply_mode, lambda *a, **k: "")
    llm = _CapturingLLM()
    _compose(_task(llm), character_id="")

    assert llm.calls[0]["system_prompt"] == GENERIC_SYSTEM_PROMPT


def test_persona_load_error_falls_back_to_generic(
    clean_reminder_env, fixed_reply_mode,
):
    clean_reminder_env.setenv(REMINDER_ENV, "1")

    def boom(*args, **kwargs):
        raise RuntimeError("persona source unavailable")

    _patch_persona(fixed_reply_mode, boom)
    llm = _CapturingLLM()
    out = _compose(_task(llm), character_id="")

    assert out == "该吃药了"
    assert llm.calls[0]["system_prompt"] == GENERIC_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# 失败兜底：LLM 抛异常 → 用户原文模板（逐字，绝不静默）
# ---------------------------------------------------------------------------


def test_llm_error_returns_user_content_fallback_verbatim(
    clean_reminder_env, fixed_reply_mode,
):
    clean_reminder_env.setenv(REMINDER_ENV, "1")
    _patch_persona(fixed_reply_mode, lambda *a, **k: PERSONA_FEATURE)
    llm = _CapturingLLM(exc=RuntimeError("upstream timeout"))

    out = _compose(_task(llm), content="吃药")

    assert out == "你设定的提醒时间到了：「吃药」。"
    assert len(llm.calls) == 1


# ---------------------------------------------------------------------------
# 重要日期线：不改代码，源码级接线守卫防漂移
# ---------------------------------------------------------------------------


def test_date_wish_keeps_full_persona_hint_wiring():
    from proactive import scheduler

    src = inspect.getsource(scheduler.ProactiveScheduler._send_date_wish)
    assert "load_full_persona_hint(" in src, (
        "重要日期祝福必须继续从完整版人设源取口吻（与提醒线同一真源）"
    )
    assert "persona_hint" in src
