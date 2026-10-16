"""主动决策上下文注入【当前记忆库内容】（2026-10-16 上下文复用批）。

对齐爱语 ILinkService.e() 的记忆注入：决策器开口前要知道"我记得用户什么"
（用户事实列表）。数据源 `get_memory_context.user_facts`
（shisi/memory/legacy/memory_pipeline.py:1133，只读、按会话隔离）；
空则整段不出现（宁缺毋串）；条数硬顶 10（与注入窗口 k 值上限一致）。
"""

from __future__ import annotations

from types import SimpleNamespace

import proactive.llm_proactive as lp
from proactive.llm_proactive import build_proactive_context

_SESSION = "N:memfacts@im.wechat"


def _svc(facts: list[str], seen: list[str] | None = None) -> SimpleNamespace:
    def _get_memory_context(session_id: str = "") -> dict:
        if seen is not None:
            seen.append(str(session_id))
        return {"user_facts": list(facts)}

    return SimpleNamespace(get_memory_context=_get_memory_context)


def _ctx() -> str:
    return build_proactive_context(
        session_key=_SESSION,
        local_time="2026-10-16 12:00 Thursday",
        hours_since_last_chat=3.0,
    )


def test_memory_facts_injected_when_present(monkeypatch):
    """有记忆：【当前记忆库内容】段在场、含事实原文，且按会话键取数。"""
    seen: list[str] = []
    monkeypatch.setattr(
        lp,
        "_resolve_memory_service",
        lambda: _svc(["用户喜欢喝美式咖啡", "用户在准备考研"], seen),
    )
    text = _ctx()
    assert "【当前记忆库内容】" in text
    assert "用户喜欢喝美式咖啡" in text
    assert "用户在准备考研" in text
    assert seen == [_SESSION], "事实必须按当前会话键取（会话隔离收口在存储层）"


def test_memory_facts_absent_when_empty(monkeypatch):
    """无记忆：整段不出现（宁缺毋串，不注入占位假值）。"""
    monkeypatch.setattr(lp, "_resolve_memory_service", lambda: _svc([]))
    assert "【当前记忆库内容】" not in _ctx()


def test_memory_facts_absent_when_service_unavailable(monkeypatch):
    """记忆服务解析不到（None）：同样整段不出现，上下文构建不抛异常。"""
    monkeypatch.setattr(lp, "_resolve_memory_service", lambda: None)
    assert "【当前记忆库内容】" not in _ctx()


def test_memory_facts_absent_without_session_key(monkeypatch):
    """无会话键：不注入——没有用户身份可挂，事实必须会话隔离。"""
    monkeypatch.setattr(
        lp,
        "_resolve_memory_service",
        lambda: _svc(["用户喜欢喝美式咖啡"]),
    )
    text = build_proactive_context(
        session_key="",
        local_time="2026-10-16 12:00 Thursday",
        hours_since_last_chat=3.0,
    )
    assert "【当前记忆库内容】" not in text


def test_memory_facts_capped_at_ten(monkeypatch):
    """长度上限：>10 条事实只注入前 10 条（与注入窗口 k 值上限一致）。"""
    facts = [f"事实{i:02d}" for i in range(15)]
    monkeypatch.setattr(lp, "_resolve_memory_service", lambda: _svc(facts))
    text = _ctx()
    assert "【当前记忆库内容】" in text
    body = text.split("【当前记忆库内容】", 1)[1]
    for i in range(10):
        assert f"事实{i:02d}" in body
    for i in range(10, 15):
        assert f"事实{i:02d}" not in body
    lines = text.split("【当前记忆库内容】", 1)[1].splitlines()
    block: list[str] = []
    for ln in lines[1:]:  # [1:] 跳过段头说明行，只数本段连续的事实行
        if ln.startswith("- "):
            block.append(ln)
        elif block:
            break
    assert len(block) == 10


def test_memory_facts_absent_when_service_raises(monkeypatch):
    """记忆读取抛异常：整段不出现，上下文构建不受影响（只读降级）。"""
    def _boom(session_id: str = "") -> dict:
        raise RuntimeError("memory down")

    svc = SimpleNamespace(get_memory_context=_boom)
    monkeypatch.setattr(lp, "_resolve_memory_service", lambda: svc)
    text = _ctx()
    assert "【当前记忆库内容】" not in text
    assert "会话键" in text, "其余段落照常注入，证明只读降级不拖垮上下文"
