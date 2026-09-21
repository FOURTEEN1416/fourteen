"""记忆混乱回归：残句/截断承诺不得进「我记得的」。"""

from __future__ import annotations

from shisi.memory.legacy.fact_extractor import FactExtractor, normalize_commitment_text
from utils.prompt_sanitize import is_injectable_fact, sanitize_fact_list


def test_production_garbage_facts_not_injectable():
    bad = [
        "叫我",
        "叫我起床",
        "明天要",
        "后天也要",
        "明天也要",
        "不喜欢你哦",
        "我是委屈啊",
        "记得多少，一一说来",
        "提醒我起床就好了",
        "明天早上七点二十分叫",
        "明天六点记得给我发消息叫",
    ]
    for f in bad:
        assert not is_injectable_fact(f), f
    good = [
        "用户生日是腊月初一",
        "用户约定了明天早上七点二十分叫我起床",
        "用户目前在军训/在校",
    ]
    for f in good:
        assert is_injectable_fact(f), f


def test_sanitize_fact_list_drops_fragments():
    out = sanitize_fact_list(
        [
            "明天要",
            "用户生日是腊月初一",
            {"fact": "不喜欢你哦"},
            {"fact": "记得多少，一一说来"},
            "用户约定明早七点叫起床",
        ]
    )
    assert out == ["用户生日是腊月初一", "用户约定明早七点叫起床"]


def test_normalize_commitment_text():
    assert normalize_commitment_text("叫我") is None
    assert normalize_commitment_text("明天早上七点二十分叫") is None
    assert normalize_commitment_text("明天早上七点二十分叫我起床") is not None


def test_rules_extractor_rejects_bare_commitment():
    fe = FactExtractor(llm_func=None)
    facts = fe.extract_facts(["叫我", "提醒我", "明天要"])
    texts = [str(f.get("fact") or "") for f in facts]
    assert "叫我" not in texts
    assert "明天要" not in texts
