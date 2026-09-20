"""角色归属与记忆注入质量回归（2026-09-21）。"""

from __future__ import annotations

from utils.prompt_sanitize import (
    ROLE_CLARITY_RULE,
    is_injectable_fact,
    looks_like_dialogue,
    sanitize_fact_list,
    sanitize_llm_history,
    sanitize_reflections,
)


def test_dialogue_detection():
    assert looks_like_dialogue("User: 消息\nAssistant: 什么消息？")
    assert looks_like_dialogue("用户: 为什么不叫我")
    assert not looks_like_dialogue("用户约定明早七点叫我起床")
    assert not looks_like_dialogue("你似乎对约定比较上心")


def test_fact_filter_drops_fragments_and_questions():
    assert not is_injectable_fact("叫我")
    assert not is_injectable_fact("来自哪里？")
    assert not is_injectable_fact("")
    assert is_injectable_fact("用户约定明早七点叫我起床")
    assert is_injectable_fact("用户不喜欢被催")


def test_sanitize_lists():
    facts = sanitize_fact_list(["叫我", "用户喜欢猫", {"fact": "User: hi"}, {"fact": "用户在学日语"}])
    assert facts == ["用户喜欢猫", "用户在学日语"]
    refs = sanitize_reflections([
        "User: 消息\nAssistant: 什么消息？",
        "你似乎对不喜欢你哦挺上心。",
        "嗯",
    ])
    assert refs == ["你似乎对不喜欢你哦挺上心。"]


def test_sanitize_llm_history_roles():
    hist = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "为什么不叫我起床"},
        {"role": "assistant", "content": "现在是7点38分了"},
        {"role": "user", "content": "处理超时"},
        {"role": "user", "content": "现在七点28分"},
        {"role": "assistant", "content": "行，是我算错了"},
        {"role": "user", "content": "现在七点28分"},  # 与当前消息重复
        {"role": "tool", "content": "x"},
    ]
    out = sanitize_llm_history(hist, current_user_message="现在七点28分")
    roles = [m["role"] for m in out]
    assert set(roles) <= {"user", "assistant"}
    assert "处理超时" not in "".join(m["content"] for m in out)
    # 最后一条不得是与 query 相同的 user（避免双重用户消息）
    if out:
        assert not (out[-1]["role"] == "user" and out[-1]["content"] == "现在七点28分")


def test_role_clarity_rule_present():
    assert "role=user" in ROLE_CLARITY_RULE
    assert "最后一条" in ROLE_CLARITY_RULE


def test_persona_prompt_memory_not_labeled_as_dialogue_history():
    import inspect

    from shisi.application.persona_service import PersonaService

    src = inspect.getsource(PersonaService.build_system_prompt)
    assert "chat_history=\"\"" in src or "chat_history=''" in src
    assert "不是**本轮对话记录" in src or "不是对话记录" in src
    assert "ROLE_CLARITY_RULE" in src


def test_character_aggregate_not_receiving_memory_as_history():
    """PersonaService 不得把记忆层传给 prompt_builder 的 chat_history 槽。"""
    import inspect

    from shisi.application import persona_service

    # build_system_prompt 内不得再调用 _build_chat_history 喂给 prompt_builder
    build_src = inspect.getsource(persona_service.PersonaService.build_system_prompt)
    assert "_build_chat_history" not in build_src
