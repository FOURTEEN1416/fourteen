"""主动决策线历史回灌净化（2026-10-21 FIX-5）。

缺陷：`ProactiveScheduler._llm_proactive_one_user` 直接
``mem.get_chat_context(...)`` 拿 recent（scheduler.py:1549-1555）→
``build_proactive_context(recent_messages=recent)`` → 决策 prompt 的
【当前角色最近对话原文】就是**原始库行**（llm_proactive.py:869-903），
既没过 ``sanitize_llm_history``（连续同角色合并 / 系统占位过滤），
也没过回灌净化（沉浸式括号动作 / 元记忆解释 / 归属翻转）。

于是上一轮写坏、已落库的 assistant 行会被当「我自己说过的话」few-shot 进
主动决策，再由决策器写出主动消息 —— 与主链 R4 要切断的自我强化回路同构，
只是发生在主动线。

修法：在 scheduler 取到 recent 后先过同一套净化（复用 FIX-3 抽出的
``utils.prompt_sanitize.scrub_history_rows`` + ``sanitize_llm_history``），
再交给 ``build_proactive_context``；净化作用于**副本**，不改 ``recent`` 本体
（``last_user_message`` 与否决检测仍读原文语义）。
"""
from __future__ import annotations

from types import SimpleNamespace

_ASSISTANT_RAW = "（想了想）今天降温了，记得加衣服哦"
_ASSISTANT_SCRUBBED = "今天降温了，记得加衣服哦"
_DENIAL_USER = "我不是夏夏，别这么叫我"


class _Mem:
    """最小 memory 替身：只实现决策线用到的 get_chat_context。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    def get_chat_context(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.rows), ""


def _recent_rows(assistant_content: str = _ASSISTANT_RAW) -> list[dict]:
    return [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant", "content": assistant_content,
            "channel": "proactive", "timestamp": "2026-10-20 12:00:00",
        },
        {"role": "user", "content": _DENIAL_USER},
    ]


def _make_scheduler(monkeypatch, mem: _Mem):
    """装配一个能走通到 build_proactive_context 的调度器（全闸放行）。"""
    import proactive.llm_proactive as lp_mod
    import proactive.proactive_context as pctx_mod
    import proactive.proactive_hooks as hooks_mod
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._quiet_event_day = {}
    sched._wait_window_event_day = {}
    sched._deliver_fail_counts = {}
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._primary_model_ok = lambda: True
    sched._record_model_degraded = lambda *a, **k: None
    sched._emit_skip_event_daily = lambda *a, **k: False
    sched._persist_throttle_ledger = lambda: None
    sched._proactive_sent_today = lambda eng, now: 0
    sched._resolve_character_id = lambda k: "char-test"
    sched._resolve_memory = lambda: mem
    sched._session_llm_resolver = None
    sched._resolve_proactive_llm = lambda eng: None  # 走到上下文组装后即返回

    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp_mod, "account_pacing_state", lambda hub, key, eng: (5.0, None))
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda *a, **k: "")
    monkeypatch.setattr(pctx_mod, "load_full_persona_hint", lambda cid, user_key=None: "")
    monkeypatch.setattr(
        hooks_mod, "load_live_hooks",
        lambda *a, **k: SimpleNamespace(section="", keywords=[]),
    )
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda key: "")
    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: None,
    )

    # 回复模式钉到沉浸式（净化是否发生依赖它；不读宿主真实配置）
    import utils.reply_mode as rm

    # 2026-10-21 R7：签名加 user_key 后 mock 必须接受实参（语义不变）
    monkeypatch.setattr(rm, "read_reply_mode", lambda *a, **k: "immersive")
    return sched


def _capture_context(monkeypatch):
    """把 build_proactive_context 包成 spy（记录实参 + 仍走真实现）。"""
    import proactive.llm_proactive as lp_mod

    captured: dict = {}
    real = lp_mod.build_proactive_context

    def _spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(lp_mod, "build_proactive_context", _spy)
    return captured


def _run(sched) -> None:
    hub = SimpleNamespace(get=lambda key: None)
    sched._llm_proactive_one_user(hub, "u1")


# ── 1. 进决策 prompt 的 recent_messages 必须已净化 ───────────


def test_decision_line_scrubs_assistant_rows(monkeypatch):
    """带括号动作 + 元记忆解释的 assistant 行不得原样进决策上下文。"""
    mem = _Mem(_recent_rows("（想了想）今天降温了。我想确认一下你是不是真的记得。"))
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    rows = captured["recent_messages"]
    assistant = [r for r in rows if r.get("role") == "assistant"]
    assert assistant, "assistant 行不得整行被吞"
    for row in assistant:
        assert "（想了想）" not in row["content"], "沉浸式括号动作必须剥除"
        assert "确认一下" not in row["content"], "元记忆解释必须删除"
    assert any("今天降温了" in r["content"] for r in assistant), "正文必须留存"


def test_decision_prompt_json_has_no_bracket_or_meta(monkeypatch):
    """端到端：真实 build_proactive_context 渲染文本里不得出现坏形态。"""
    mem = _Mem(_recent_rows())
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    # spy 已把真实渲染结果交给调用方；这里直接对渲染产物断言
    import proactive.llm_proactive as lp_mod

    rendered = lp_mod.build_proactive_context(**{
        k: v for k, v in captured.items()
    })
    assert "（想了想）" not in rendered, "决策 prompt 里仍带括号动作"
    assert "今天降温了" in rendered, "净化不得把正文一起吞掉"


def test_decision_line_drops_system_placeholders(monkeypatch):
    """sanitize_llm_history 的职责一并生效：系统占位行不得进决策上下文。"""
    rows = _recent_rows()
    rows.append({"role": "assistant", "content": "（处理超时，请重试）"})
    mem = _Mem(rows)
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    assert all(
        "处理超时" not in str(r.get("content") or "")
        for r in captured["recent_messages"]
    ), "系统占位行必须被过滤"


# ── 2. recent 本体与其其它消费者语义不变 ────────────────────


def test_recent_object_not_mutated(monkeypatch):
    """净化必须作用于副本：原 recent 行内容不得被就地改写。"""
    rows = _recent_rows()
    mem = _Mem(rows)
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    assistant = [r for r in rows if r.get("role") == "assistant"]
    assert assistant[0]["content"] == _ASSISTANT_RAW, "原 recent 被就地改写（应只动副本）"
    assert captured["recent_messages"] is not rows, "不得把原列表当净化结果传下去"


def test_last_user_message_reads_original(monkeypatch):
    """last_user_message 取自原文 recent（否决句原样，不经净化裁剪）。"""
    mem = _Mem(_recent_rows())
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    assert captured["last_user_message"] == _DENIAL_USER


def test_denial_detection_unchanged(monkeypatch):
    """否决检测语义不变：对净化副本与原文的检测结果一致。"""
    from proactive.llm_proactive import detect_denied_nicknames

    rows = _recent_rows()
    mem = _Mem(rows)
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    assert detect_denied_nicknames(captured["recent_messages"]) == detect_denied_nicknames(rows)
    assert "夏夏" in detect_denied_nicknames(captured["recent_messages"])


def test_non_list_recent_does_not_break(monkeypatch):
    """recent 取不到（None/异常）时不得炸，决策链照常组装上下文。"""
    mem = _Mem([])
    sched = _make_scheduler(monkeypatch, mem)
    captured = _capture_context(monkeypatch)

    _run(sched)

    assert captured["recent_messages"] == []


def test_no_local_import_shadows_module_read_reply_mode():
    """AST：决策线内不得用局部 import 绑定 read_reply_mode。

    回归实证（本批实装时踩到）：本函数内既有的出站守卫实参用的是**模块级**
    ``read_reply_mode``（FIX-1 加法）；若在此函数里写
    ``from utils.reply_mode import read_reply_mode``，该名字在整个函数作用域
    变成局部名 → ``recent`` 为空时守卫实参处抛
    ``UnboundLocalError: cannot access local variable 'read_reply_mode'``
    （test_proactive_dedup_gate 8 例当场变红）。故本函数只允许用模块别名形态。
    """
    import ast
    import inspect
    import textwrap

    from proactive import scheduler as sched_mod

    assert hasattr(sched_mod, "read_reply_mode"), "模块级 read_reply_mode 缺失（FIX-1 接线）"
    src = inspect.getsource(sched_mod.ProactiveScheduler._llm_proactive_one_user)
    # textwrap.dedent（非 inspect.cleandoc）：后者按「后续行的公共缩进」去边，
    # 会把方法体一起顶到行首 → IndentationError。
    tree = ast.parse(textwrap.dedent(src))
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
    assert "read_reply_mode" not in bound, (
        "决策线内局部 import 绑定了 read_reply_mode → 会遮蔽模块级导入，"
        "recent 为空时守卫实参处 UnboundLocalError"
    )
