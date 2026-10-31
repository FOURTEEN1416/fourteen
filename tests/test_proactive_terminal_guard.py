"""主动链投递前终审守卫（2026-10-14 对抗验收执行位矩阵抓到的缺口）。

缺口定性：主动链（scheduler._llm_proactive_one_user 决策→_deliver 投递）
此前只有 sanitize_message（剥时间前缀），错误哨兵/表情复读/退化回复会
绕过终审直达用户。主链与流式已接 terminal_reply_guard，主动链遗漏。

修法：投递前接同一真源 terminal_reply_guard（单源防分叉）。
"""
from __future__ import annotations


def test_proactive_delivery_runs_terminal_guard():
    """主动链**决策线**：守卫必须在 ``_deliver`` 之前执行且结果就是投递正文。

    2026-10-31 R1-3：旧实现拿**整个 scheduler 模块源码**做首个 ``index`` ——
    首个命中是第 36 行注释里的 ``terminal_reply_guard``；``_deliver`` 首个
    命中是 ``_send_date_wish`` 的手动发送（scheduler.py:2802），根本没
    钉住决策线（当时 docstring 误记为 ``_send_important_dates``/``:954``，
    后者实为 ``manual_send_now`` 且参数是 ``session_key=key``，旧 needle
    不会命中）。改为 AST 只解析 ``_llm_proactive_one_user`` 函数体：
    删掉该函数里的守卫或投递调用，AST 找不到对应 Call → 测试必红。
    """
    import ast
    import inspect
    import textwrap

    from proactive import scheduler

    src = inspect.getsource(scheduler.ProactiveScheduler._llm_proactive_one_user)
    tree = ast.parse(textwrap.dedent(src))

    guard_line: int | None = None
    guard_assigns_message = False
    deliver_line: int | None = None
    delivers_message = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "terminal_reply_guard"
        ):
            guard_line = node.lineno
            guard_assigns_message = any(
                isinstance(target, ast.Name) and target.id == "message"
                for target in node.targets
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_deliver"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            deliver_line = node.lineno
            if node.args and isinstance(node.args[0], ast.Name):
                delivers_message = node.args[0].id == "message"

    assert guard_line is not None, "决策线未接终审守卫 terminal_reply_guard"
    assert guard_assigns_message, "守卫结果没有赋回 message（投递仍是未净化正文）"
    assert deliver_line is not None, "决策线没有投递调用 self._deliver"
    assert guard_line < deliver_line, "终审守卫必须在决策线投递之前执行"
    assert delivers_message, "决策线投递的不是守卫生成的 message"



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


# --------------------------------------------------------------------------
# 2026-10-21 FIX-1：主动/提醒链出站守卫未传 reply_mode → 沉浸式括号不剥
#
# 缺陷：三处 terminal_reply_guard 调用（scheduler 决策线、scheduler._deliver
# 兜底线、reminder_delivery._compose_text）均未传 reply_mode，缺省空串使
# `_strip_immersive_stage_directions` 直接返回原文 → 沉浸式模式下主动消息/
# 提醒仍把「（想了想）」这类括号动作发给用户。主动线还把带括号的 mes_example
# 喂进生成 prompt（「教它写括号又不拦」），构成正向教唆。
#
# 修法：三处调用各补 `reply_mode=read_reply_mode()`（utils.reply_mode 单一
# 真源）。语义下界：immersive 下带括号必剥、novel 下原样、缺省（reply_mode
# 不传）不改行为以防误伤其它未接线调用方。
# --------------------------------------------------------------------------

_BRACKETED = "（想了想）今天降温了，记得加衣服哦"
_BRACKETED_STRIPPED = "今天降温了，记得加衣服哦"


def test_guard_reply_mode_semantics_lower_bound():
    """守卫 reply_mode 语义下界：immersive 剥、novel 原样、缺省原样。

    这是三处接线共同依赖的下层契约——缺省不剥是**刻意保留**，避免误伤
    未传 reply_mode 的历史调用方（主链/流式在别处已传）。
    """
    from utils.prompt_sanitize import terminal_reply_guard

    # 沉浸式：括号动作是小说模式专属格式，出站必剥
    assert (
        terminal_reply_guard(
            _BRACKETED, user_msg="", character_name="", reply_mode="immersive",
        )
        == _BRACKETED_STRIPPED
    )
    # 小说式：括号动作是设计的一部分，原样保留
    assert (
        terminal_reply_guard(
            _BRACKETED, user_msg="", character_name="", reply_mode="novel",
        )
        == _BRACKETED
    )
    # 缺省（不传）：行为与旧版一致，不剥（防误伤未接线调用方）
    assert terminal_reply_guard(_BRACKETED, user_msg="", character_name="") == _BRACKETED


def test_proactive_scheduler_callsites_pass_reply_mode():
    """源码级：scheduler 两处守卫调用都必须传 reply_mode=read_reply_mode()。

    缺陷形态是「调用点漏传参数」，行为级单测难以覆盖决策线内部（需重度
    装配）；此处以源码级钉住两处调用点的实参，配合 _deliver 行为级测试
    双向锁定（缺一不可：源码级防漏传，行为级防传了不用）。
    """
    import inspect

    from proactive import scheduler

    for fn in (
        scheduler.ProactiveScheduler._llm_proactive_one_user,
        scheduler.ProactiveScheduler._deliver,
    ):
        src = inspect.getsource(fn)
        assert "terminal_reply_guard(" in src, f"{fn.__name__} 未接终审守卫"
        # 2026-10-21 R7 改写：模式由**全局单例**改为按会话解析（per-user 覆盖）。
        # 原断言钉死字面量 `reply_mode=read_reply_mode()`，签名加 user_key 后
        # 必然变红；改成语义级断言 —— 必须传 reply_mode，且取值来自
        # `read_reply_mode(` 真源（允许带会话键实参）。
        assert "reply_mode=" in src, (
            f"{fn.__name__} 的守卫调用未传 reply_mode（沉浸式括号不剥）"
        )
        assert "read_reply_mode(" in src, (
            f"{fn.__name__} 的 reply_mode 未取自 utils.reply_mode 真源"
        )


def test_reminder_delivery_compose_passes_reply_mode(monkeypatch):
    """行为级：提醒文案在沉浸式下括号必剥（_compose_text 实调守卫）。"""
    import asyncio

    from proactive import reminder_delivery as rd_mod

    class _LLM:
        async def chat(self, **kwargs):  # noqa: ANN003
            return _BRACKETED

    monkeypatch.setattr(
        rd_mod, "read_reply_mode", lambda: "immersive", raising=False,
    )
    task = rd_mod.ReminderDeliveryTask(
        structured_memory=None, llm=_LLM(), character_id_resolver=lambda sk: "",
    )
    out = asyncio.run(task._compose_text({"content": "加衣服"}, character_id=""))
    assert out == _BRACKETED_STRIPPED, "提醒线沉浸式未剥括号动作"


def test_reminder_delivery_compose_keeps_brackets_in_novel(monkeypatch):
    """行为级：小说式下提醒括号动作原样保留（不误伤设计格式）。"""
    import asyncio

    from proactive import reminder_delivery as rd_mod

    class _LLM:
        async def chat(self, **kwargs):  # noqa: ANN003
            return _BRACKETED

    monkeypatch.setattr(
        rd_mod, "read_reply_mode", lambda: "novel", raising=False,
    )
    task = rd_mod.ReminderDeliveryTask(
        structured_memory=None, llm=_LLM(), character_id_resolver=lambda sk: "",
    )
    out = asyncio.run(task._compose_text({"content": "加衣服"}, character_id=""))
    assert out == _BRACKETED, "小说式提醒被误剥括号动作"


def test_deliver_strips_stage_directions_in_immersive(monkeypatch):
    """行为级：_deliver 兜底线在沉浸式下括号必剥（发送前已净化）。"""
    from proactive import scheduler as sched_mod

    captured: list[str] = []

    monkeypatch.setattr(sched_mod, "read_reply_mode", lambda *a, **k: "immersive", raising=False)
    sched = sched_mod.ProactiveScheduler.__new__(sched_mod.ProactiveScheduler)
    sched._run_blocking = lambda fn: fn()  # type: ignore[method-assign]
    sched._send_targeted = lambda msg, sk: (captured.append(msg), True)[1]  # type: ignore[method-assign]
    sched._record_outbound = lambda *a, **k: None  # type: ignore[method-assign]

    ok = sched._deliver(_BRACKETED, session_key=None)
    assert ok is True
    assert captured == [_BRACKETED_STRIPPED], "主动兜底线沉浸式未剥括号动作"


def test_deliver_keeps_stage_directions_in_novel(monkeypatch):
    """行为级：小说式下 _deliver 不剥括号（与今日行为一致）。"""
    from proactive import scheduler as sched_mod

    captured: list[str] = []

    monkeypatch.setattr(sched_mod, "read_reply_mode", lambda *a, **k: "novel", raising=False)
    sched = sched_mod.ProactiveScheduler.__new__(sched_mod.ProactiveScheduler)
    sched._run_blocking = lambda fn: fn()  # type: ignore[method-assign]
    sched._send_targeted = lambda msg, sk: (captured.append(msg), True)[1]  # type: ignore[method-assign]
    sched._record_outbound = lambda *a, **k: None  # type: ignore[method-assign]

    ok = sched._deliver(_BRACKETED, session_key=None)
    assert ok is True
    assert captured == [_BRACKETED], "小说式主动消息被误剥括号动作"


def test_deliver_fail_closed_when_immersive_strips_to_empty(monkeypatch):
    """沉浸式整条都是括号动作 → 剥空 → fail-closed 不投递。

    这是既有裁决「宁可不发也不发括号」的语义下界（非新行为）：
    `_deliver` 对空守卫结果返回 False，调用方走失败退避。
    """
    from proactive import scheduler as sched_mod

    captured: list[str] = []

    monkeypatch.setattr(sched_mod, "read_reply_mode", lambda *a, **k: "immersive", raising=False)
    sched = sched_mod.ProactiveScheduler.__new__(sched_mod.ProactiveScheduler)
    sched._run_blocking = lambda fn: fn()  # type: ignore[method-assign]
    sched._send_targeted = lambda msg, sk: (captured.append(msg), True)[1]  # type: ignore[method-assign]
    sched._record_outbound = lambda *a, **k: None  # type: ignore[method-assign]

    ok = sched._deliver("（想了想）", session_key=None)
    assert ok is False, "剥空后必须 fail-closed（返回 False 不投递）"
    assert captured == [], "剥空后不得投递任何内容"
