"""出站系统标记回显剥除（2026-10-16 生产泄漏驱动）。

事故（会话4/林挽夏，07:12）：模型把历史注入的行首标记当格式抄进回复原文，
生产回复 [2844] 原文开头「[主动] [10-15 18:27] 七点吃饭啊」直接发给了用户
——07:06 主动消息（channel=proactive）落库后，下轮历史注入经
``orchestrator.context_budget.annotate_outbound_markers`` 加回「[主动]」+时间
前缀，模型照抄回显。原守卫只拦哨兵/表情复读/退化回复与行首称呼，标记不在
覆盖面内。

修法：``utils.prompt_sanitize._strip_system_markers``（行首循环剥
``[主动]``/``[定时提醒]``/``[MM-DD HH:MM]``，两组交替），由
``terminal_reply_guard`` 在既有拦截之后、称呼剥除之前调用；剥空返回空串，
调用方走 empty_reply 兜底。
"""

from __future__ import annotations

from utils.prompt_sanitize import _strip_system_markers, terminal_reply_guard

# ── 1. 纯函数：正向剥除 ────────────────────────────────────


def test_strip_proactive_marker():
    assert _strip_system_markers("[主动] 今天好") == "今天好"


def test_strip_reminder_marker():
    assert _strip_system_markers("[定时提醒] 该吃药了") == "该吃药了"


def test_strip_marker_plus_time_prefix_production_case():
    """生产原文（回复 [2844] 开头）：两组标记交替出现，剥到没有为止。"""
    assert _strip_system_markers("[主动] [10-15 18:27] 七点吃饭啊") == "七点吃饭啊"


def test_strip_two_consecutive_markers():
    assert _strip_system_markers("[主动] [定时提醒] 该吃药了") == "该吃药了"


def test_strip_three_markers_to_bare_text():
    assert _strip_system_markers("[主动] [定时提醒] [10-15 18:27] X") == "X"


def test_strip_only_markers_returns_empty():
    """剥空（只剩标记）→ 空串，由调用方走 empty_reply 兜底。"""
    assert _strip_system_markers("[主动]") == ""
    assert _strip_system_markers("[主动] [定时提醒]") == ""


def test_strip_production_multiline_body_kept():
    raw = "[主动] [10-15 18:27] 七点吃饭啊\n我这边还没决定呢"
    assert _strip_system_markers(raw) == "七点吃饭啊\n我这边还没决定呢"


# ── 2. 纯函数：误伤保护 ────────────────────────────────────


def test_keep_marker_mid_text():
    """正文中部（非行首）出现的同样字样不动。"""
    assert _strip_system_markers("今天[主动]帮了你") == "今天[主动]帮了你"
    assert _strip_system_markers("他说[主动]找我了") == "他说[主动]找我了"


def test_keep_emoji_brackets():
    """普通方括号表情不属这两种形态，不得误剥。"""
    assert _strip_system_markers("[666] 哈哈") == "[666] 哈哈"
    assert _strip_system_markers("[微笑] 早") == "[微笑] 早"


def test_keep_other_brackets_with_time_like_text():
    assert _strip_system_markers("[abc] 你好") == "[abc] 你好"


# ── 3. 集成：terminal_reply_guard ─────────────────────────


def test_guard_strips_marker():
    assert terminal_reply_guard("[主动] 今天好", user_msg="早") == "今天好"
    assert terminal_reply_guard("[定时提醒] 该吃药了", user_msg="") == "该吃药了"


def test_guard_strips_marker_plus_prefix():
    assert terminal_reply_guard("[主动] [10-15 18:27] 七点吃饭啊", user_msg="嗯") == "七点吃饭啊"


def test_guard_strip_only_markers_returns_empty():
    """剥空 → 空串（调用方走 empty_reply 兜底），不得把标记原样发出。"""
    assert terminal_reply_guard("[主动]", user_msg="") == ""
    assert terminal_reply_guard("[主动] [定时提醒]", user_msg="") == ""


def test_guard_keeps_mid_text_marker():
    assert terminal_reply_guard("今天[主动]帮了你", user_msg="") == "今天[主动]帮了你"


def test_guard_keeps_emoji_brackets():
    assert terminal_reply_guard("[666] 哈哈", user_msg="") == "[666] 哈哈"
    assert terminal_reply_guard("[微笑] 早", user_msg="") == "[微笑] 早"


def test_guard_strips_with_session_and_character():
    """主链形态（带 session_key/character_name）下剥除仍然生效。"""
    r = terminal_reply_guard(
        "[主动] [10-15 18:27] 七点吃饭啊",
        user_msg="刚醒",
        character_name="林挽夏",
        session_key="4:wx@im.wechat",
        character_id="linwanxia",
    )
    assert r == "七点吃饭啊"


# ── 4. 接线位次（源码级钉死：系统标记最前 → 表情复读 → 名称退化 → 称呼剥除）──
#
# 2026-10-16 序洞修复：标记剥除必须在表情复读/名称退化**之前** —— 剥除会
# 改变首行文本（「[主动] 十四」→「十四」），后置会让退化/复读检查对着未剥
# 文本判定，漏拦剥后形态（独立复核实测：「[主动] 十四」character_name=十四
# 曾直接放行、「[主动] [流泪]」user_msg=[流泪] 曾绕过复读剥除）。


def test_guard_order_pins_marker_strip_before_vocative_strip():
    import inspect

    src = inspect.getsource(terminal_reply_guard)
    emoji_pos = src.index("strip_emoji_echo(s, user_msg)")
    marker_pos = src.index("_strip_system_markers(s)")
    vocative_pos = src.index("strip_forbidden_vocatives")
    assert marker_pos < emoji_pos < vocative_pos, "剥除位次偏离（系统标记 → 表情复读 → 称呼）"


def test_marker_strip_then_name_degenerate_caught():
    """序洞回归：剥完标记后只剩角色名 → 必须被名称退化检查拦下（返回空串）。"""
    r = terminal_reply_guard("[主动] 十四", character_name="十四")
    assert r == "", f"剥后退化形态漏拦: {r!r}"


def test_marker_strip_then_emoji_echo_caught():
    """序洞回归：剥完标记后只剩机械复读表情 → 必须被复读剥除剥成空串。"""
    r = terminal_reply_guard("[主动] [流泪]", user_msg="[流泪]")
    assert r == "", f"剥后复读形态漏拦: {r!r}"


# ── 5. 提醒线守卫接线（2026-10-16 独立复核 #5：守卫矩阵唯一缺口）──
#
# 提醒投递曾只过 sanitize_message，若提醒文案抄了 [定时提醒] 行首标记不剥。
# 行为级验证 _compose_text 真的走 terminal_reply_guard（非源码 grep —— 假
# 阳性陷阱：注释/import 都含 "terminal_reply_guard" 字样）。


class _MarkerLeakLLM:
    """stub LLM：文案里带行首系统标记（模拟抄注入标记的形态）。"""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    async def chat(self, **kwargs):  # noqa: ANN003
        return self._reply


def _compose_with_llm(reply: str) -> str:
    import asyncio

    from proactive.reminder_delivery import ReminderDeliveryTask

    task = ReminderDeliveryTask(structured_memory=object(), llm=_MarkerLeakLLM(reply))
    return asyncio.run(
        task._compose_text(  # noqa: SLF001
            {"content": "吃药", "session_key": "4:wx@im.wechat"},
            character_id="default",
        )
    )


def test_reminder_compose_strips_proactive_marker():
    assert _compose_with_llm("[主动] 该吃药了") == "该吃药了"


def test_reminder_compose_strips_reminder_and_time_marker():
    assert _compose_with_llm("[定时提醒] [10-15 18:27] 该吃药了") == "该吃药了"


def test_reminder_compose_keeps_clean_text():
    assert _compose_with_llm("该吃药了，别忘了") == "该吃药了，别忘了"

def test_reminder_compose_marker_only_falls_back():
    """剥空（只剩标记）→ 走固定兜底文案（不把纯标记发出去）。"""
    out = _compose_with_llm("[主动]")
    assert out and "[主动]" not in out


# ── 5. 行尾「（距上一条 X）」间隔注记（2026-10-16 对抗验收抓出的漏网） ──
#
# 生产原文「[主动] [10-15 18:27] 七点吃饭啊」证明模型会把注入侧元数据当格式回显；
# ``annotate_history_time_gaps`` 追加的**行尾**间隔注记（「（距上一条 X）」）同属
# 注入元数据、同样可被回显，而旧 ``_strip_system_markers`` 只剥行首 → 漏网。


def test_strip_trailing_gap_note():
    assert _strip_system_markers("早啊（距上一条 3 小时）") == "早啊"


def test_strip_marker_plus_gap_note():
    """行首标记 + 行尾间隔注记同时出现（真实历史行形态）。"""
    assert _strip_system_markers("[10-15 18:27] 早啊（距上一条 3 小时）") == "早啊"


def test_strip_gap_note_multiline():
    src = "[主动] 在忙吗（距上一条 2 小时）\n[定时提醒] 吃药（距上一条 1 天）"
    assert _strip_system_markers(src) == "在忙吗\n吃药"


def test_gap_note_mid_line_not_stripped():
    """正文中部同形文字不误伤（仅行尾匹配）。"""
    assert (
        _strip_system_markers("他说（距上一条 3 小时）不算")
        == "他说（距上一条 3 小时）不算"
    )


def test_gap_note_only_falls_back_to_empty():
    """整条只剩间隔注记 → 返回空串（调用方走 empty_reply 兜底）。"""
    assert terminal_reply_guard("（距上一条 3 小时）") == ""


def test_gap_note_prefix_matches_truth_source():
    """真源对账：剥除前缀取自 ``context_budget``，注记形态与真源产出逐字一致。"""
    from datetime import datetime

    from orchestrator.context_budget import (
        HISTORY_GAP_NOTE_PREFIX,
        annotate_history_time_gaps,
    )

    # now 钉在当天 14:00：两行距今均 <6h（不触发时段分段）、行间隔 3h<6h
    # （无段间头注）——本用例只测「间隔后缀真源对账」，分段渲染由
    # test_session_segment_render.py 专测。
    rows = annotate_history_time_gaps(
        [
            {"role": "assistant", "content": "早啊", "timestamp": "2026-10-15T10:00:00+00:00"},
            {"role": "user", "content": "在吗", "timestamp": "2026-10-15T13:00:00+00:00"},
        ],
        now=datetime(2026, 10, 15, 22, 0, 0),
    )
    annotated = rows[1]["content"]
    assert HISTORY_GAP_NOTE_PREFIX in annotated  # 真源确实追加了该前缀
    assert _strip_system_markers(annotated) == "在吗"  # 剥除可吃掉真源产出
