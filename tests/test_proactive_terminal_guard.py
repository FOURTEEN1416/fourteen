"""主动链投递前终审守卫（2026-10-14 对抗验收执行位矩阵抓到的缺口）。

缺口定性：主动链（scheduler._llm_proactive_one_user 决策→_deliver 投递）
此前只有 sanitize_message（剥时间前缀），错误哨兵/表情复读/退化回复会
绕过终审直达用户。主链与流式已接 terminal_reply_guard，主动链遗漏。

修法：投递前接同一真源 terminal_reply_guard（单源防分叉）。
"""
from __future__ import annotations


def test_proactive_delivery_runs_terminal_guard():
    """主动链投递路径必须调用终审守卫（源码级钉存在性）。"""
    import inspect

    from proactive import scheduler

    src = inspect.getsource(scheduler)
    assert "terminal_reply_guard" in src, "主动链投递前未接终审守卫"
    # 必须在 _deliver 之前
    guard_pos = src.index("terminal_reply_guard")
    deliver_pos = src.index("self._deliver(message, session_key=user_key")
    assert guard_pos < deliver_pos, "终审守卫必须在投递之前执行"


def test_proactive_terminal_guard_blocks_sentinel():
    """行为级：主动消息若为错误哨兵文案，守卫必须拦截（返回空）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    sentinel = "（所有 LLM 提供商均不可用，请检查配置。最后错误: x）"
    # 主动链 user_msg 为空（独白场景）
    assert terminal_reply_guard(sentinel, user_msg="", character_name="") == ""


def test_proactive_terminal_guard_passes_normal():
    """主动链正常消息零改动（独白场景无 user_msg，表情复读剥除不触发）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    normal = "早。今天天气凉了些，上课记得多穿点。"
    assert terminal_reply_guard(normal, user_msg="", character_name="十四") == normal
    # 主动链可能带表情（如早安问候），不得被误剥（无用户消息上下文）
    with_emoji = "[太阳] 早安呀"
    assert terminal_reply_guard(with_emoji, user_msg="", character_name="十四") == with_emoji


def test_proactive_terminal_guard_blocks_degenerate():
    """退化回复（只剩角色名）在主动链同样拦截。"""
    from utils.prompt_sanitize import terminal_reply_guard

    assert terminal_reply_guard("十四", user_msg="", character_name="十四") == ""


def test_proactive_guard_shares_source_with_main_chain():
    """主动链与主链必须共用同一守卫真源（防再次分叉）。"""
    import inspect

    from proactive import scheduler
    from utils import prompt_sanitize

    # 主链的守卫函数在 prompt_sanitize；主动链必须 import 同一函数
    sched_src = inspect.getsource(scheduler)
    assert "from utils.prompt_sanitize import terminal_reply_guard" in sched_src
    # 不得有本地重实现（哨兵判据单一真源）
    assert "def terminal_reply_guard" not in sched_src
    assert hasattr(prompt_sanitize, "terminal_reply_guard")
