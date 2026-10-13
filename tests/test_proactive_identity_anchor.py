"""主动消息链用户身份锚守卫（2026-10-13 生产事故驱动）。

事故（agent_plane 账本 id=14475 及 chat_history 2623/2656 回放）：主动独白场景
没有用户消息在场，模型从角色卡世界观给真人用户套人设——按角色名给用户起昵称
「夏夏」、编造「你以前总爱看那些齿轮」的共同记忆、假设共处同一空间。

本文件钉死修复三件：
1. DECISION_SYSTEM 硬规则含用户身份锚（禁起名/禁编造用户经历/禁假设共处）；
2. build_proactive_context 显式注入【用户身份】段（nickname 空→只称「你」；
   有→只用该昵称），且排在【角色人设】之前；
3. persona_hint 带框架注（卡文中「你」≠ 真实用户）。
"""

from __future__ import annotations

from proactive.llm_proactive import DECISION_SYSTEM, build_proactive_context


def _ctx(**kw):
    base = dict(local_time="2026-10-13 12:00 Monday", persona_hint="你是她从小一起长大的青梅竹马")
    base.update(kw)
    return build_proactive_context(**base)


# ── 1. 决策器硬规则 ──────────────────────────────────────────


def test_decision_system_has_identity_anchor():
    """硬规则 8：用户身份锚三禁（起名/编造经历/假设共处）。"""
    for phrase in (
        "不是角色卡世界观里的人物",
        "不得给用户起名",
        "不得编造用户的经历",
        "不得假设与用户共处同一物理空间",
    ):
        assert phrase in DECISION_SYSTEM, f"决策器缺身份锚条款: {phrase}"


# ── 2. 上下文【用户身份】段 ──────────────────────────────────


def test_identity_block_no_nickname():
    ctx = _ctx(profile={"nickname": "", "occupation": "上学"})
    assert "【用户身份】真实用户（注册账号本人）" in ctx
    assert "无已知昵称——对用户只能称「你」，不得另起名字" in ctx


def test_identity_block_with_nickname():
    ctx = _ctx(profile={"nickname": "十四"})
    assert "用户昵称：十四——对用户的称呼只能用这个昵称" in ctx
    assert "不得另起名字" not in ctx


def test_identity_block_before_persona_hint():
    """身份段必须先于人设段注入（框架注指向「以上方【用户身份】段」）。"""
    ctx = _ctx(profile={"nickname": ""})
    i, u = ctx.find("【用户身份】"), ctx.find("【角色人设】")
    assert 0 < i < u, f"顺序错误: 身份段@{i} 人设段@{u}"


# ── 3. 角色卡框架注 ──────────────────────────────────────────


def test_persona_hint_carries_disclaimer():
    ctx = _ctx(persona_hint="你是她从小一起长大的青梅竹马")
    assert "不是真实用户" in ctx
    assert "真实用户身份以上方【用户身份】段为准" in ctx
