"""L1 终审补「记忆检索」规则（2026-10-21 FIX-2）。

缺陷：`orchestrator/tool_gate.py` 的 L1 裁决 rules 逐条点名 set_reminder /
update_user_profile / remember_facts / forget_facts / query_reminders / calendar /
manage_reminder / query_profile，**没有一条**对应「用户问过去说过的事 → 查长期
记忆」；而与爱语 `<search:>` 对等的 `MemoryTool`（`tools/builtin/extra_tools.py`，
name="memory"、description="查询与用户相关的长期记忆事实"）恰恰被
`_QUERY_PATTERNS` 里的「你还记得|记得我|我的偏好|关于我的记忆」晋级到 L1 ——
模型拿到 schema 却没有任何指令指引它用 = **晋级后空窗**。

前提（不在本改动范围）：`memory` 工具当前未在 `config/system.yaml` 的
`builtin_tools` 启用；启用与否属配置决策。

修法：在 rules 列表补一行（与「查询已有提醒 → query_reminders」同格式）。
只改规则文案，不动执行逻辑、不动待澄清槽位逻辑。
"""
from __future__ import annotations

import re

from orchestrator.tool_gate import build_review_messages, should_escalate

# 典型触发句：命中 _QUERY_PATTERNS 的「你还记得|记得我|关于我的记忆」
_MEMORY_QUERY = "你还记得我说过我喜欢吃什么吗"

# 精确工具名 token（前/后不得再接词字符或 `-`）——不能用裸 `in`：`"memory" in
# "memory_tool"` 也为真，那样把工具名写错（memory_tool）照样假绿（突变实测踩到）。
_MEMORY_TOOL_TOKEN_RE = re.compile(r"(?<![\w-])memory(?![\w-])")


def _review_system(query: str = _MEMORY_QUERY) -> str:
    system, _ = build_review_messages(query, "角色 system", [])
    return system


# ── 1. 规则文案在场且可指认 ─────────────────────────────────


def test_rule_mentions_memory_tool():
    """规则必须点名 memory 工具（模型据此把 schema 与场景对上号）。"""
    system = _review_system()
    assert _MEMORY_TOOL_TOKEN_RE.search(system), (
        "L1 规则未点名 memory 工具（晋级后仍无指引）"
    )


def test_rule_covers_recall_intent():
    """必须覆盖「你还记得…/我说过…吗」这类回忆意图。"""
    system = _review_system()
    assert "你还记得" in system, "规则未覆盖「你还记得…」这句触发语"
    assert "说过" in system, "规则未覆盖「我说过…」这类回忆意图"


def test_rule_has_no_fabrication_guard():
    """查不到时必须如实说记不清，禁止编造（与主链记忆守则同口径）。"""
    system = _review_system()
    assert "记不清" in system
    assert "不要编造" in system or "不得编造" in system or "禁止编造" in system


def test_rule_names_a_real_tool():
    """文案里点名的工具名必须**精确**等于真实工具名（防改名/写错后文案变空话）。"""
    from tools.builtin.extra_tools import MemoryTool

    assert MemoryTool.name == "memory"
    assert MemoryTool.description  # 工具自带描述，规则与之呼应
    assert _MEMORY_TOOL_TOKEN_RE.search(_review_system()), (
        "规则里的工具名与真实注册名不一致（须精确为 memory）"
    )


# ── 2. 格式与既有规则一致（可读、可 grep）────────────────────


def test_rule_line_format_matches_siblings():
    """新增规则必须是 `- ` 开头的单条列表项（与既有规则同款）。"""
    system = _review_system()
    line = next(
        (ln for ln in system.splitlines() if "你还记得" in ln and "工具" in ln),
        None,
    )
    assert line is not None, "未找到新增的记忆检索规则行"
    assert line.startswith("- "), f"规则行格式与其它条不一致：{line!r}"
    assert line.count("- ") == 1, "一条规则不应在同一行里塞多条列表项"


def test_rules_still_contain_previously_named_tools():
    """既有规则一条不得被本次插入挤压/覆盖（回归护栏）。"""
    system = _review_system()
    for tool in (
        "set_reminder",
        "update_user_profile",
        "remember_facts",
        "forget_facts",
        "query_reminders",
        "query_profile",
        "manage_reminder",
        "ask_user",
    ):
        assert tool in system, f"既有规则 {tool} 丢失"


# ── 3. 执行逻辑不变：只动文案 ───────────────────────────────


def test_escalation_behaviour_unchanged():
    """晋级判定本身不在本次改动内：回忆类说法本来就能晋级（空窗才在 L1）。"""
    assert should_escalate(_MEMORY_QUERY) is True
    assert should_escalate("今天天气怎么样") is True
    assert should_escalate("嗯嗯") is False


def test_pending_slots_rule_path_untouched():
    """待澄清槽位分支照常渲染（本次不动该逻辑）。"""
    system, _ = build_review_messages(
        _MEMORY_QUERY, "角色 system", [], pending_slots={"intent": "set_reminder"}, pending_ask_count=1,
    )
    assert "待继续任务" in system
    assert "memory" in system, "带待澄清槽位时新规则同样必须在场"


def test_history_and_query_passthrough_unchanged():
    """返回值契约不变：返回 (system, query)，query 原样。"""
    system, query = build_review_messages("你好", "S", [{"role": "user", "content": "早"}])
    assert query == "你好"
    assert isinstance(system, str) and "任务裁决" in system
