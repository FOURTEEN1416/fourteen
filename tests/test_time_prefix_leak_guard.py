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
    # 指引仍守 ≤3 行预算（tests/test_history_row_timestamps 同钉）
    from utils.local_time import time_anchor_line
    lines = [ln for ln in time_anchor_line().splitlines() if ln.strip()]
    assert len(lines) <= 4, f"指引不得超过 3 行，实际 {len(lines)-1} 行"


# ── 4. 主动链清洗同真源（2026-10-13 对抗验收补）────────────────


def test_ase_sanitize_message_strips_prefix():
    """主动链 sanitize_message 与主链共用剥离真源（LLM 输出不可信同等风险面）。"""
    from proactive.ase_engine import sanitize_message

    assert sanitize_message("[10-13 13:07] 吃早饭了吗") == "吃早饭了吗"
    # 叠加前缀
    assert sanitize_message("[10-13 13:07] [10-13 13:07] 想你啦") == "想你啦"
    # 合法内容不误伤
    assert sanitize_message("[流泪] 想你了") == "[流泪] 想你了"


def test_both_sanitizers_share_source():
    """两条清洗链必须走同一剥离真源（防再次分叉）。"""
    import inspect

    from proactive import ase_engine

    src = inspect.getsource(ase_engine.sanitize_message)
    assert "strip_time_prefixes" in src
