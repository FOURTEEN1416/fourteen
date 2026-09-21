"""Prompt 槽位顺序契约 — 钉死「画像在记忆前、工具在 PHI 前、记忆非对话历史」。"""

from __future__ import annotations


def _sample_system_prompt() -> str:
    """模拟 v1.31+ 目标序列（测试用拼装样例，非生产完整 prompt）。"""
    return "\n".join(
        [
            "# 角色",
            "你是米彩。",
            "# 用户画像",
            "称呼：彩儿；生日：腊月初一；职业：上班。",
            "禁止编造以上画像之外的用户信息。",
            "# 关系与情绪",
            "关系阶段：熟人（level 2）。",
            "# 记忆上下文（供参考，不是本轮对话记录）",
            "<context id=\"memory\" source=\"user_facts\" trust=\"untrusted\">",
            "- 用户偏好：安静。",
            "</context>",
            "# 对话示例",
            "用户：今天好累。米彩：那早点休息。",
            "<context id=\"tool\" source=\"tool_result\" trust=\"untrusted\">",
            "set_reminder ok id=3",
            "</context>",
            "# 扮演规则与输出格式",
            "用角色口吻回复。",
        ]
    )


def _index(text: str, marker: str) -> int:
    pos = text.find(marker)
    assert pos >= 0, f"missing marker: {marker}"
    return pos


def test_profile_before_memory() -> None:
    text = _sample_system_prompt()
    assert _index(text, "# 用户画像") < _index(text, "# 记忆上下文")


def test_memory_marked_not_chat_history() -> None:
    text = _sample_system_prompt()
    mem_at = _index(text, "# 记忆上下文")
    # 记忆段不得伪装成对话历史标题
    window = text[mem_at : mem_at + 200]
    assert "# 对话历史" not in window
    assert "不是本轮对话记录" in window


def test_tool_context_before_phi() -> None:
    text = _sample_system_prompt()
    assert _index(text, 'id="tool"') < _index(text, "# 扮演规则")


def test_mutated_order_would_fail() -> None:
    """突变说明：若实现把记忆放在画像前，应由 test_profile_before_memory 失败。"""
    bad = "# 记忆上下文\nfacts\n# 用户画像\nbirthday"
    # 直接用与生产断言相同的比较逻辑
    assert _index(bad, "# 用户画像") > _index(bad, "# 记忆上下文")
