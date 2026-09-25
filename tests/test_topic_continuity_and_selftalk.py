"""话题续聊 + 自问自答清洗 + 历史角色卫生（2026-09-23）。

用户诉求：「像人一样真的能够按照一个话题聊下去」；
追问必须**接话**（不是改成无关关心）；自问自答要根治。
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════
#  1. 当前话题提取
# ══════════════════════════════════════════════════════════


def test_extract_current_topics_from_recent_and_current():
    from utils.prompt_sanitize import extract_current_topics

    hist = [
        {"role": "user", "content": "今天军训好累啊"},
        {"role": "assistant", "content": "辛苦了，记得拉伸"},
    ]
    topics = extract_current_topics(hist, current_user_message="教官还让我们站军姿")
    assert "军训" in topics
    assert len(topics) <= 3


def test_extract_current_topics_empty_when_no_signal():
    from utils.prompt_sanitize import extract_current_topics

    assert extract_current_topics([], current_user_message="") == []
    assert extract_current_topics(None, current_user_message="嗯") == []


def test_extract_current_topics_captures_explicit_mention():
    from utils.prompt_sanitize import extract_current_topics

    topics = extract_current_topics(
        [], current_user_message="我们聊聊面试吧"
    )
    assert "面试" in topics


# ══════════════════════════════════════════════════════════
#  2. 回复清洗：剧本体 / 自问自答
# ══════════════════════════════════════════════════════════


def test_sanitize_reply_strips_user_dialogue_lines():
    from utils.prompt_sanitize import sanitize_reply_text

    raw = "用户：今天天气不错\n林挽夏：是啊，挺舒服的"
    out = sanitize_reply_text(raw)
    assert "用户：" not in out
    assert "林挽夏：" not in out
    assert "挺舒服的" in out


def test_sanitize_reply_keeps_normal_single_turn():
    from utils.prompt_sanitize import sanitize_reply_text

    raw = "军训累不累？我当年站军姿腿都在抖"
    assert sanitize_reply_text(raw) == raw


def test_sanitize_reply_drops_self_answer():
    from utils.prompt_sanitize import sanitize_reply_text

    raw = "今天吃什么？嗯我觉得火锅不错"
    out = sanitize_reply_text(raw)
    assert out == "今天吃什么？"


def test_sanitize_reply_keeps_question_only():
    from utils.prompt_sanitize import sanitize_reply_text

    raw = "你那边降温了吗？"
    assert sanitize_reply_text(raw) == raw


# ══════════════════════════════════════════════════════════
#  3. 历史角色卫生：连续同角色合并
# ══════════════════════════════════════════════════════════


def test_sanitize_llm_history_merges_consecutive_assistant():
    from utils.prompt_sanitize import sanitize_llm_history

    hist = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "主动消息一句"},
        {"role": "assistant", "content": "追问接话一句"},
        {"role": "user", "content": "在的"},
    ]
    out = sanitize_llm_history(hist)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert "主动消息一句" in out[1]["content"]
    assert "追问接话一句" in out[1]["content"]


def test_sanitize_llm_history_still_dedups_current_user():
    from utils.prompt_sanitize import sanitize_llm_history

    hist = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
        {"role": "user", "content": "现在七点28分"},
    ]
    out = sanitize_llm_history(hist, current_user_message="现在七点28分")
    assert out[-1]["role"] == "assistant"


# ══════════════════════════════════════════════════════════
#  4. Prompt 指令在位
# ══════════════════════════════════════════════════════════


def test_role_clarity_forbids_playing_both_sides():
    from utils.prompt_sanitize import ROLE_CLARITY_RULE

    assert "禁止一人分饰两角" in ROLE_CLARITY_RULE
    assert "不得生成用户的台词" in ROLE_CLARITY_RULE


def test_topic_continuity_rule_present():
    from utils.prompt_sanitize import TOPIC_CONTINUITY_RULE

    assert "沿着话题" in TOPIC_CONTINUITY_RULE
    assert "自问自答" in TOPIC_CONTINUITY_RULE


def test_persona_service_renders_current_topics():
    import inspect

    from shisi.application import persona_service

    src = inspect.getsource(persona_service.PersonaService.build_system_prompt)
    assert "current_topics" in src
    assert "TOPIC_CONTINUITY_RULE" in src


def test_orchestrator_extracts_topics_and_sanitizes_reply():
    import inspect

    from orchestrator import optimized_orchestrator as oo

    src = inspect.getsource(oo)
    assert "extract_current_topics" in src
    assert "sanitize_reply_text" in src


def test_followup_prompt_keeps_jiehua():
    import inspect

    from wechat_direct import wechat_connector as wc

    src = inspect.getsource(wc)
    assert "接话" in src or "延伸你自己" in src
    # 对方没回时：只延伸自己，禁止虚构对方提问
    assert "禁止虚构对方" in src or "禁止虚构对方的发言" in src
    assert "延伸你自己" in src


def test_followup_rejects_invented_user_speech():
    """生产实证（2026-09-25）：「就想知道月饼啥味？」= 虚构对方提问，必须拒发。"""
    from proactive.ase_engine import _INVENTED_USER_RE

    assert _INVENTED_USER_RE.search("才两小时没说话就想知道月饼啥味？")
    assert not _INVENTED_USER_RE.search("月饼是我特意留的，你尝尝")


def test_ase_sanitize_rejects_invented_user():
    from proactive.ase_engine import sanitize_message

    assert sanitize_message("哼，才两小时没说话就想知道月饼啥味？我又不是没尝过。") is None
    assert sanitize_message("中秋快乐呀") == "中秋快乐呀"
