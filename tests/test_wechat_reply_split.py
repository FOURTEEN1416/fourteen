"""「一句一句发」回复拆分回归（2026-09-19 用户反馈）。

用户原话：「一个正常人，怎么会一次性发那么回一大段？……不应该一句一句的吗？」
并给出真实反例（5 行一次发出）。
"""

from __future__ import annotations


def _split(reply, **kw):
    from wechat_direct.wechat_connector import split_reply_for_wechat

    return split_reply_for_wechat(reply, **kw)


# ── 基本行为 ──────────────────────────────────────────────

def test_short_reply_stays_single():
    assert _split("在呢") == ["在呢"]
    assert _split("还没呢，正想着你呢") == ["还没呢，正想着你呢"]


def test_empty_returns_empty_list():
    assert _split("") == []
    assert _split("   \n  ") == []


def test_user_reported_example_is_split():
    """用户贴的真实反例：5 行一次发出 → 必须拆成多条。"""
    reply = (
        "就是...你打字的时候，停顿比较长\n\n"
        "而且，声音听起来也不太精神\n\n"
        "可能是我多想了\n\n"
        "那你自己注意别中暑了\n\n"
        "我继续听雨了"
    )
    segs = _split(reply)
    assert len(segs) > 1, "整段多行不得作为一条发出"
    assert len(segs) <= 4, "段数须受上限约束，避免刷屏"
    assert all(s.strip() for s in segs)
    # 内容无损（拼接后应包含全部原文关键词）
    joined = "".join(segs)
    for kw in ("停顿比较长", "不太精神", "多想了", "中暑", "听雨"):
        assert kw in joined, kw


def test_long_single_line_split_at_sentence_enders():
    reply = "早上好呀。今天天气不错，你要不要出去走走？我刚刚看了下窗外，太阳很大。"
    segs = _split(reply, max_chars=20, max_segments=4)
    assert len(segs) >= 2
    assert all(len(s) <= 20 for s in segs[:-1])   # 末段可能因合并兜底而略长


def test_segments_capped():
    reply = "\n".join(f"第{i}句" for i in range(12))
    segs = _split(reply, max_segments=4)
    assert 1 < len(segs) <= 4, "超出上限的应并入最后一段，而不是继续刷屏"
    for i in range(12):
        assert f"第{i}句" in "".join(segs)


def test_no_content_loss_on_merge():
    reply = "一。" * 30
    segs = _split(reply, max_chars=10, max_segments=3)
    assert len(segs) == 3
    assert "".join(segs) == reply


def test_no_newlines_in_any_segment():
    """不得把换行留在消息里（微信里会显示成奇怪的空行）。"""
    reply = "第一句\n\n第二句\n\n第三句"
    for seg in _split(reply):
        assert "\n" not in seg


# ── 段间停顿 ──────────────────────────────────────────────

def test_segment_delay_is_bounded():
    from wechat_direct.wechat_connector import _segment_delay

    assert 0.4 <= _segment_delay("") <= 1.6
    assert 0.4 <= _segment_delay("短") <= 1.6
    assert 0.4 <= _segment_delay("啊" * 200) <= 1.6
