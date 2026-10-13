"""时间前缀学舌回库根治守卫（2026-10-13 生产事故驱动）。

事故（对抗验收 F3 漏网排查中发现的第三类污染）：历史注入给每行加的
``[MM-DD HH:MM]`` 前缀被模型**学舌**写进回复原文并落库；下一轮已带前缀的
正文又被叠新前缀 → 滚雪球（生产实证 chat_history 2633/2635/2670-2702
共 14 行，前缀从 1 个滚到 12 个）。

修复三层：
1. `strip_time_prefixes` 纯函数（可叠加前缀循环剥净）；
2. `sanitize_reply_text` 出站定稿时剥前缀（发送和落库之前）；
3. `TIME_REASONING_GUIDE` 增加「回复时绝不写前缀」的显式指令。
"""

from __future__ import annotations

from utils.local_time import TIME_REASONING_GUIDE
from utils.prompt_sanitize import sanitize_reply_text, strip_time_prefixes

# ── 1. 纯函数 ──────────────────────────────────────────────


def test_strip_single_prefix():
    assert strip_time_prefixes("[10-13 13:07] 你好") == "你好"


def test_strip_stacked_prefixes():
    raw = "[10-13 13:07] [10-13 13:07] [10-13 13:07] 哦，不怕忙是好事"
    assert strip_time_prefixes(raw) == "哦，不怕忙是好事"


def test_strip_twelve_prefixes_production_case():
    """生产实证极端值：前缀滚到 12 个。"""
    raw = "[10-13 13:07] " * 12 + "看来你是担心忙碌但没有方向"
    assert strip_time_prefixes(raw) == "看来你是担心忙碌但没有方向"


def test_strip_keeps_normal_brackets():
    """合法中括号内容不得误剥（表情/普通方括号）。"""
    assert strip_time_prefixes("[流泪][流泪][流泪]") == "[流泪][流泪][流泪]"
    assert strip_time_prefixes("他说[这件事]很重要") == "他说[这件事]很重要"
    # 非时间格式的方括号保留
    assert strip_time_prefixes("[abc] 你好") == "[abc] 你好"


def test_strip_time_not_at_line_start_kept():
    """前缀只在行首剥离；正文中的时间引用保留。"""
    s = "我们约在 [10-13 13:07] 见面"
    assert strip_time_prefixes(s) == s


# ── 2. 出站定稿集成 ────────────────────────────────────────


def test_sanitize_reply_strips_prefix():
    assert sanitize_reply_text("[10-13 13:07] 今天风不大") == "今天风不大"


def test_sanitize_reply_strips_multiline_per_line():
    raw = "[10-13 13:07] 第一行\n[10-13 13:08] 第二行"
    assert sanitize_reply_text(raw) == "第一行\n第二行"


def test_sanitize_reply_combined_with_labels():
    """前缀 + 发言标签叠加时一并清理。"""
    raw = "[10-13 13:07] 助手：你好呀"
    assert sanitize_reply_text(raw, character_name="") == "你好呀"


# ── 3. 注入侧指令 ──────────────────────────────────────────


def test_guide_forbids_writing_prefix():
    assert "绝对不要写出任何这类前缀" in TIME_REASONING_GUIDE
