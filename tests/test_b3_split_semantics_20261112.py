"""B-3 主链拆条语义（2026-11-12）—— hard_limit / 永不拆 / 回滚开关。

背景：`_split_overlong_line` 旧实现只对「无标点收尾的尾串」硬切，带句末标点的
超长句原样保留（100 字带句号 → [100]）。B-3 新增 opt-in 的 `hard_limit`：
默认 False 逐字保持 legacy；True 时保证每个非末条 ≤ max_chars。
"""

from __future__ import annotations

from utils import humanize_config as hc
from wechat_direct import wechat_connector as wc


def test_legacy_default_still_keeps_overlong_sentence():
    """默认 hard_limit=False：legacy 行为逐字不变（100 字仍是一条）。"""
    text = "一" * 99 + "。"
    assert wc._split_overlong_line(text, 45) == [text]
    assert wc.split_reply_for_wechat(text, max_segments=2) == [text]


def test_legacy_multiline_sample_is_unchanged():
    """legacy 多行样例的拆条结果固定为 [7, 8, 40]。"""
    text = "第一句讲完了。\n第二句也讲完了。\n" + "x" * 40
    assert [len(s) for s in wc.split_reply_for_wechat(text, max_segments=3)] == [7, 8, 40]


def test_hard_limit_splits_long_sentence_and_preserves_text():
    text = "一" * 99 + "。"
    pieces = wc.split_reply_for_wechat(text, max_segments=3, hard_limit=True)
    assert [len(p) for p in pieces] == [45, 45, 10]
    assert "".join(pieces) == text
    assert all(len(p) <= 45 for p in pieces)


def test_hard_limit_46_char_sentence():
    text = "一" * 45 + "。"
    pieces = wc.split_reply_for_wechat(text, max_segments=3, hard_limit=True)
    assert [len(p) for p in pieces] == [45, 1]
    assert "".join(pieces) == text


def test_hard_limit_two_sentences_each_capped():
    text = "一" * 45 + "。" + "二" * 54 + "。"
    pieces = wc.split_reply_for_wechat(text, max_segments=3, hard_limit=True)
    assert "".join(pieces) == text
    # 契约只要求**非末条** ≤ max_chars；末条按 max_segments 上限并条，允许超长。
    assert all(len(p) <= 45 for p in pieces[:-1])


def test_max_segments_one_means_never_split():
    text = "一" * 99 + "。"
    pieces = wc.split_reply_for_wechat(text, max_segments=1, hard_limit=True)
    assert pieces == [text]


def test_segment_cap_choices_include_one():
    assert hc.SEGMENT_CAP_CHOICES == (1, 2, 3, 4)
    assert hc.MAX_SEGMENTS == 3


def test_speaker_reply_switch_defaults_on_and_off_values(monkeypatch):
    from utils.speaker_switches import speaker_reply_enabled

    monkeypatch.delenv("SPEAKER_REPLY", raising=False)
    assert speaker_reply_enabled() is True
    for value in ("0", "false", "off", "no", " OFF "):
        monkeypatch.setenv("SPEAKER_REPLY", value)
        assert speaker_reply_enabled() is False
    monkeypatch.setenv("SPEAKER_REPLY", "1")
    assert speaker_reply_enabled() is True


def test_connector_hard_limit_helper_follows_switch(monkeypatch):
    monkeypatch.delenv("SPEAKER_REPLY", raising=False)
    assert wc._speaker_reply_hard_limit() is True
    monkeypatch.setenv("SPEAKER_REPLY", "0")
    assert wc._speaker_reply_hard_limit() is False
