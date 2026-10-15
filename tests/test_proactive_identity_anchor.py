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
    """硬规则 8：用户身份锚三禁（起名/编造经历/假设共处）+ 亲密呼语豁免。"""
    for phrase in (
        "不是角色卡世界观里的人物",
        "不得给用户起名字",
        "不得编造用户的经历",
        "不得假设与用户共处同一物理空间",
    ):
        assert phrase in DECISION_SYSTEM, f"决策器缺身份锚条款: {phrase}"
    # 2026-10-14 拟人化修正：禁令对象是名字，不是亲密呼语（防拟人化弱化）
    assert "「笨蛋」「傻瓜」" in DECISION_SYSTEM, "必须显式豁免亲密呼语"


# ── 2. 上下文【用户身份】段 ──────────────────────────────────


def test_identity_block_no_nickname():
    ctx = _ctx(profile={"nickname": "", "occupation": "上学"})
    assert "【用户身份】真实用户（注册账号本人）" in ctx
    assert "无已知昵称——对用户称「你」，不得另起名字" in ctx
    # 拟人化豁免（2026-10-14）：呼语不受昵称禁令限制
    assert "「笨蛋」「傻瓜」这类表达亲密的呼语不受此限" in ctx


def test_identity_block_with_nickname():
    ctx = _ctx(profile={"nickname": "十四"})
    assert "用户昵称：十四——对用户的称呼用这个昵称" in ctx
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


# ── 4. 用户称呼白名单段（2026-10-13 15:28 复犯事故 → 2026-10-15 称呼根治批）──
#
# 事故：身份锚 12:52 部署后，15:28 决策器 reason 明知「用户指出角色混淆了
# 身份（夏夏）…避免错误称呼」，输出 message 却仍叫「夏夏，最近怎么样」。
# 根因：历史里用户引用该错误称呼（「谁是夏夏」「你叫我夏夏的时候」），
# 锚的「没有昵称只称你」被历史注意力惯性压过。旧修法是黑名单式
# 【被否认称呼】禁令段——三重结构性失效：依赖用户先发现 / 窗口不滑出 /
# 模型听话。本批翻转为**白名单唯一真源**（utils.address_terms.
# address_prompt_block）：正面指定唯一合法称呼 + 角色名派生词自动禁用
# （林挽夏 → 夏夏，无需用户发现即兜住根因）+ 否认清单补充行。


def _denied_ctx(**kw):
    """带会话键 + 历史含否认事件（决策线正常形态：scheduler 恒带 session_key）。"""
    kw.setdefault("session_key", "N:anchor")
    kw.setdefault(
        "recent_messages",
        [
            {"role": "assistant", "content": "夏夏，你吃了吗"},
            {"role": "user", "content": "谁是夏夏？我不是什么夏夏"},
            {"role": "assistant", "content": "哦哦我记错了"},
            {"role": "user", "content": "你叫我夏夏的时候我都没有回过话"},
        ],
    )
    kw.setdefault("profile", {"nickname": ""})
    return _ctx(**kw)


def test_address_whitelist_block_injected(monkeypatch):
    """带会话键 → 【用户称呼】白名单段在位，且正面指定唯一合法称呼。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: {"夏夏"})
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    ctx = _denied_ctx()
    assert "# 用户称呼" in ctx
    assert "只能" in ctx, "必须是正面指定（白名单），不是禁令清单"
    assert "「你」" in ctx, "无昵称时唯一合法称呼 = 「你」"


def test_denied_names_still_listed_inside_whitelist_block(monkeypatch):
    """否认记录仍在（作为白名单段的补充行），措辞含禁令。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: {"夏夏"})
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    ctx = _denied_ctx()
    seg = ctx.split("【被否认称呼】")[1][:400]
    assert "夏夏" in seg
    assert "否认" in seg and ("不得" in seg or "禁止" in seg)


def test_character_name_derivatives_cover_accident_root_cause(monkeypatch):
    """🔴 根因回归：「夏夏」= 林挽夏叠字 → 无需用户发现即自动禁用（结构性修复）。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())  # 无任何用户否认记录
    monkeypatch.setattr(cr, "display_name", lambda cid: "林挽夏")

    ctx = _denied_ctx(character_id="62105bca", recent_messages=[])
    assert "你自己的名字" in ctx, "角色名/变体只能指自己必须显式在案（无否认也生效）"


def test_no_session_key_keeps_legacy_ban_line():
    """无会话键（画不出合法称呼）→ 降级保留旧禁令行（不放过窗口内否认词）。"""
    ctx = _ctx(
        recent_messages=[
            {"role": "user", "content": "我不是十七，别这么叫我"},
        ],
        profile={"nickname": ""},
    )
    assert "【被否认称呼】" in ctx
    assert "十七" in ctx.split("【被否认称呼】")[1][:200]


def test_no_denial_no_ban_line(monkeypatch):
    """历史无否认事件 → 无否认清单行（宁缺毋串）。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    ctx = _ctx(
        session_key="N:anchor-clean",
        recent_messages=[
            {"role": "user", "content": "今天好累"},
            {"role": "assistant", "content": "早点休息"},
        ],
        profile={"nickname": ""},
    )
    assert "【被否认称呼】" not in ctx


def test_decision_system_has_denied_name_rule():
    """硬规则 8 追加：历史中被用户否认过的称呼（含用户引用的）一律不得再用。"""
    assert "否认" in DECISION_SYSTEM and "不得再用" in DECISION_SYSTEM
