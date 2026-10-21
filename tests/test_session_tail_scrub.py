"""跨会话尾巴（session_tail）回灌净化 + 历史行净化单一真源（2026-10-21 FIX-3）。

缺陷：主链历史回灌净化只作用于 ``get_chat_context`` 的 chat_history。跨会话尾巴
走 ``structured_memory.get_cross_session_tail`` → 原始 content 直拼
``- 用户（MM-DD HH:MM）：…`` / ``- 角色（cid）（MM-DD HH:MM）：…``
（`shisi/memory/legacy/structured_memory.py`，只过滤「处理超时」占位），
经 ``format_session_tail`` 注入 `orchestrator/optimized_orchestrator.py`，
既不经沉浸式剥括号、也不删元记忆解释/归属翻转。

命中条件恰是 ``hist_len < 2``（新会话 / 近史浅）——冷启动最易出问题的场景。

修法：把主链既有的逐行净化内联循环抽成 ``utils.prompt_sanitize.scrub_history_rows``
纯函数（dict 行 = chat_history；str 行 = 已格式化的尾巴行），主链内联循环与
尾巴调用点共用同一真源，避免规则分叉。只改内存副本，不动库。
"""
from __future__ import annotations

import os
import tempfile

from utils.prompt_sanitize import scrub_history_rows

_ASSISTANT_LINE = "- 角色（abc123）（09-20 12:00）：（想了想）好的，今天降温了"
_USER_LINE = "- 用户（09-20 12:00）：calm down"


# ── 1. dict 行（chat_history 形态）──────────────────────────


def test_scrub_rows_immersive_strips_brackets_and_drops_degenerate():
    """沉浸式：assistant 行括号动作剥除；剥空的行丢弃（不喂回空白行）。"""
    rows = [
        {"role": "user", "content": "今天好冷"},
        {"role": "assistant", "content": "（想了想）记得加衣服哦"},
        {"role": "assistant", "content": "（歪头）"},
    ]
    out = scrub_history_rows(rows, immersive=True)

    assert [r["role"] for r in out] == ["user", "assistant"]
    assert out[1]["content"] == "记得加衣服哦", "沉浸式 assistant 行必须剥括号动作"
    assert out[0]["content"] == "今天好冷", "非括号正文不得改动"


def test_scrub_rows_novel_keeps_brackets():
    """小说式：括号动作是设计的一部分，不得剥（模式边界）。"""
    rows = [{"role": "assistant", "content": "（想了想）记得加衣服哦"}]
    out = scrub_history_rows(rows, immersive=False)
    assert out[0]["content"] == "（想了想）记得加衣服哦"


def test_scrub_rows_strips_meta_memory_and_attribution_regardless_of_mode():
    """元记忆解释 / 归属翻转与模式无关（与主链既有语义一致），两种模式都删。"""
    meta = "我想确认一下你是不是真的记得。今天降温了。"
    flip = "你是说刚刚我说的「点完名就吃」吗？"
    for immersive in (True, False):
        out_meta = scrub_history_rows(
            [{"role": "assistant", "content": meta}], immersive=immersive,
        )
        assert "确认一下" not in out_meta[0]["content"]
        assert "今天降温了" in out_meta[0]["content"], "只删该句，保留其余正文"

        out_flip = scrub_history_rows(
            [{"role": "assistant", "content": flip}], immersive=immersive,
        )
        # 整条只有翻转句 → 净化后为空 → 该行被丢弃（不得留空行）
        assert out_flip == [], "归属翻转句必须删除（两种模式都删）"


def test_scrub_rows_user_side_keeps_meta_phrasing():
    """user 侧的同样措辞属用户原话，不得删（只对 assistant 侧做语义净化）。"""
    rows = [{"role": "user", "content": "我想确认一下你是不是真的记得这件事"}]
    out = scrub_history_rows(rows, immersive=True)
    assert out[0]["content"] == "我想确认一下你是不是真的记得这件事"


def test_scrub_rows_preserves_extra_keys_and_non_dict_rows():
    """净化不得丢行内其它键（timestamp/channel 是下游依赖）；非 dict 行原样透传。"""
    rows = [
        {
            "role": "assistant", "content": "（想了想）好的",
            "timestamp": "09-20 12:00", "channel": "proactive",
        },
        "not-a-row",
        42,
    ]
    out = scrub_history_rows(rows, immersive=True)
    assert out[0]["content"] == "好的"
    assert out[0]["timestamp"] == "09-20 12:00" and out[0]["channel"] == "proactive"
    assert out[1] == "not-a-row" and out[2] == 42


def test_scrub_rows_non_list_passthrough():
    """非 list 形态原样返回（主链「非 list 不炸」契约）。"""
    assert scrub_history_rows(None, immersive=True) is None
    assert scrub_history_rows("x", immersive=True) == "x"


# ── 2. str 行（跨会话尾巴形态）──────────────────────────────


def test_scrub_tail_lines_immersive_strips_brackets_keeps_prefix():
    """沉浸式尾巴行：剥 content 里的括号动作，但**保留**行首角色/时间前缀。"""
    out = scrub_history_rows([_USER_LINE, _ASSISTANT_LINE], immersive=True)

    assert len(out) == 2, "两行都非空，不得被丢弃"
    assert out[0] == _USER_LINE, "user 行无动作段 → 原样"
    assert out[1] == "- 角色（abc123）（09-20 12:00）：好的，今天降温了", (
        f"必须只剥 content、保留前缀：{out[1]!r}"
    )
    assert "09-20 12:00" in out[1], "时间前缀是 10-11 批刻意加的，不得被剥"
    assert "abc123" in out[1], "角色归属前缀不得被剥"


def test_scrub_tail_lines_novel_keeps_brackets():
    """小说式尾巴行：括号动作原样保留（模式边界）。"""
    out = scrub_history_rows([_ASSISTANT_LINE], immersive=False)
    assert out == [_ASSISTANT_LINE]


def test_scrub_tail_lines_assistant_side_meta_and_attribution():
    """尾巴里 assistant 侧的元记忆解释 / 归属翻转按句删除（user 侧不动）。"""
    meta_line = "- 角色（abc123）：我想确认一下你是不是真的记得。今天降温了。"
    flip_line = "- 角色（abc123）：你是说刚刚我说的「点完名就吃」吗？"
    user_line = "- 用户：我想确认一下你是不是真的记得这件事"

    out = scrub_history_rows([meta_line, flip_line, user_line], immersive=True)

    # 翻转行整条只剩翻转句 → 净化后为空 → 丢弃；余下两行按原序保留
    assert len(out) == 2, f"翻转空壳行必须丢弃，实得 {out!r}"
    assert "确认一下" not in out[0] and "今天降温了" in out[0]
    assert out[1] == user_line, "user 原话不得被删（原样保留）"


def test_scrub_tail_lines_drop_emptied_rows():
    """剥空/删空后的尾巴行必须丢弃（不留 ``- 角色（cid）：`` 空壳）。"""
    out = scrub_history_rows(
        ["- 角色（abc123）：（歪头）", "- 角色（abc123）：我想确认一下你是不是真的记得。"],
        immersive=True,
    )
    assert out == [], f"空壳行必须丢弃，实得 {out!r}"


# ── 3. 接线：orchestrator 两个调用点共用单一真源 ─────────────


def test_orchestrator_tail_scrubbed_before_format():
    """源码级：尾巴净化必须发生在 format_session_tail(**调用**) 之前。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    tail_start = src.index("get_cross_session_tail")
    scrub_pos = src.index("scrub_history_rows", tail_start)
    fmt_call_pos = src.index("format_session_tail(tail_lines)", tail_start)
    assert tail_start < scrub_pos < fmt_call_pos, (
        "取 tail → 净化 → format_session_tail 的顺序不得颠倒"
    )
    # 净化结果必须真的喂给 format_session_tail（不得净化到别的变量上）
    assert "scrub_history_rows(\n                        tail_lines" in src or (
        "tail_lines = scrub_history_rows(" in src
    ), "净化结果必须回写 tail_lines"


def test_orchestrator_history_scrub_uses_shared_source():
    """源码级：主链内联逐行净化必须改调共用真源，不得再分叉重实现。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert "scrub_history_rows" in src, "orchestrator 未接入共用净化真源"
    assert "strip_stage_directions(_content)" not in src, (
        "旧内联括号剥离循环应改调共用真源"
    )
    assert "_strip_attribution_flip(_content)" not in src, (
        "旧内联归属翻转净化应改调共用真源"
    )


# ── 4. 真实链路：structured_memory 尾巴 → 净化 ────────────────


def test_real_tail_lines_are_scrubbed():
    """与真实生产成品的行格式对接（`- 角色（cid）（MM-DD HH:MM）：content`）。"""
    from shisi.memory.legacy.structured_memory import StructuredMemory

    d = tempfile.mkdtemp()
    db = StructuredMemory(os.path.join(d, "tail.db"))
    try:
        db.add_chat("user", "你好呀", session_id="2:wxid_z")
        db.add_chat(
            "assistant", "（想了想）好的，今天降温了",
            session_id="2:wxid_z", character_id="abc123",
        )
        raw = db.get_cross_session_tail("2:wxid_z", limit=4)
    finally:
        db.close()

    assert raw and any("（想了想）" in x for x in raw), "前提：原始尾巴确实带括号"

    imm = scrub_history_rows(raw, immersive=True)
    assert imm and all("（想了想）" not in x for x in imm), f"沉浸式尾巴未净化: {imm!r}"
    assert any("今天降温了" in x for x in imm), "正文必须留存"
    assert all(x.startswith("- ") for x in imm), "行格式（行首 - ）必须保持"

    novel = scrub_history_rows(raw, immersive=False)
    assert any("（想了想）" in x for x in novel), "小说式不得剥"
