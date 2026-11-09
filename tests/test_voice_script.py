"""语音朗读稿（``voice.voice_script``）回归。

2026-11-09 生产取证驱动：线上语音送的是 ``reply[:200]`` 原样文本，
``[主动]`` 标记、Markdown、链接、表情、小说模式的括号动作全都会被念出来。

本层铁律：**只做减法**。2026-11-09 试听教训——上一版在「我才不是」后面
插了「，」和「嗯…」，把语义块切成两个孤儿短语，听感是「停顿很怪」。
所以这里只允许：剥符号、按文本自带标点断句、换行按前文有无停顿决定并接。
**不得**插入语气词、气口、额外逗号。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from voice.voice_script import to_spoken_script

# ── 剥除：注入元数据 / 动作括号 / Markdown / 链接 / 表情 ──


def test_strips_injected_outbound_markers():
    assert to_spoken_script("[主动] 今天降温了") == "今天降温了"
    assert to_spoken_script("[主动] [10-15 18:27] 七点吃饭啊") == "七点吃饭啊"
    assert to_spoken_script("[定时提醒] 该吃药了") == "该吃药了"


def test_strips_stage_directions_but_keeps_speech():
    assert to_spoken_script("（她轻轻叹了口气）你今天怎么样") == "你今天怎么样"
    assert to_spoken_script("*笑了笑* 我没事") == "我没事"
    assert to_spoken_script("我没事（其实有点难过）你在哪") == "我没事你在哪"


def test_strips_markdown_but_keeps_text():
    assert to_spoken_script("**好**啊") == "好啊"
    assert to_spoken_script("# 标题") == "标题"
    assert to_spoken_script("- 第一句\n- 第二句") == "第一句，第二句"
    assert to_spoken_script("> 引用的一行") == "引用的一行"


def test_strips_urls_and_emoji():
    out = to_spoken_script("看这个 https://example.com/a?b=1 好不好")
    assert "http" not in out and "example.com" not in out
    assert out.startswith("看这个") and out.endswith("好不好")
    assert to_spoken_script("哈哈😄真棒👍") == "哈哈真棒"


def test_drops_code_blocks_entirely():
    text = "看看这段```python\nprint(1)\n```就这样"
    assert "print" not in to_spoken_script(text)


# ── 断句：只在文本自带标点处停顿，绝不凭空造 ──


def test_newline_after_sentence_final_punct_is_joined_without_comma():
    # 「我才不是非说给你听的」是一个完整语气块，换行不该把它切开
    out = to_spoken_script("哼，又催。想听就听吧，我才不是非说给你听的。\n大笨蛋，想你了。")
    assert out == "哼，又催。想听就听吧，我才不是非说给你听的。大笨蛋，想你了。"
    assert "听，的" not in out and "的，大笨蛋" not in out


def test_newline_without_punct_becomes_single_comma():
    assert to_spoken_script("在的\n刚听到你说") == "在的，刚听到你说"


def test_never_inserts_filler_words_or_pauses():
    """回归 2026-11-09「停顿很怪」：不得凭空插入「嗯…」/逗号。"""
    src = "哼，又催。想听就听吧，我才不是非说给你听的。"
    out = to_spoken_script(src)
    assert out == src
    assert "嗯" not in out
    assert out.count("，") == src.count("，")
    assert out.count("。") == src.count("。")


def test_ellipsis_preserved_not_collapsed():
    # 「……」是两拍停顿，等价成「…」会缩短停顿；去重规则不得碰它
    assert to_spoken_script("嗯……好吧") == "嗯……好吧"


def test_duplicate_punctuation_collapsed():
    assert to_spoken_script("真的吗？？？") == "真的吗？"
    assert to_spoken_script("好的。。") == "好的。"


# ── 边界 ──


@pytest.mark.parametrize("text", ["", "   ", None, "（只有动作）", "[主动]", "😄😄"])
def test_returns_empty_when_nothing_speakable(text):
    assert to_spoken_script(text) == ""


def test_truncates_after_cleaning():
    long_text = "（动作）" + "啊" * 300
    out = to_spoken_script(long_text, max_chars=50)
    assert len(out) == 50
    assert "（" not in out


def test_signature_keeps_keyword_only_max_chars():
    params = inspect.signature(to_spoken_script).parameters
    assert params["max_chars"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["max_chars"].default == 200


# ── 接线：对话链与主动线必须走同一 owner ──


def test_chat_chain_uses_spoken_script_not_raw_slice():
    src = (Path(__file__).resolve().parents[1]
           / "orchestrator" / "optimized_orchestrator.py").read_text(encoding="utf-8")
    assert "to_spoken_script(reply" in src
    assert "voice_text = reply[:200]" not in src, (
        "对话链又把原样 reply 送进 TTS 了：动作括号/标记会被念出来"
    )


def test_proactive_delivery_uses_spoken_script():
    src = (Path(__file__).resolve().parents[1]
           / "utils" / "proactive_voice_delivery.py").read_text(encoding="utf-8")
    assert "to_spoken_script(text" in src
    assert 'str(text or "")[:200]' not in src
