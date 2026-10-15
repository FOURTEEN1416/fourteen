"""异常回复守卫批（2026-10-14 生产事故驱动）。

事故族与对应守卫：
1. 错误哨兵泄漏（10-13 13:03 全链失败文案直接发给用户）→ terminal_reply_guard 拦截；
2. 表情机械复读（10-14 智谱降级窗口 21/25 镜像）→ strip_emoji_echo 剥离 + EMOJI_USE_RULE 引导；
3. 退化回复只剩角色名（10-13 回复仅「十四」）→ terminal_reply_guard 拦截；
4. markdown 粗体进微信（智谱轮「1. **冰敷**：…」）→ sanitize_reply_text 剥除；
5. 主链身份锚空缺（「夏夏/十七」起名、编共同记忆、「门没锁灯给你留了」）
   → USER_IDENTITY_RULE + 【用户称呼】白名单段进主链 system prompt
   （2026-10-15 称呼根治批：黑名单式【被否认称呼】独立段退役，改白名单唯一真源段）；
6. 「X是谁」语序漏检（「十七是谁」不被「谁是X」命中 → 次日仍叫「十七」）
   → detect_denied_nicknames 补名字前置语序。
"""
import pytest

# ═══════════════════════════════════════════════════════════
# 1. 终审守卫：错误哨兵泄漏
# ═══════════════════════════════════════════════════════════


def test_guard_intercepts_gateway_error_string():
    """10-13 生产原文：全链失败文案不得作为回复出境。"""
    from utils.prompt_sanitize import terminal_reply_guard

    raw = "（所有 LLM 提供商均不可用，请检查配置。最后错误: 模型请求失败，未生成角色回复）"
    assert terminal_reply_guard(raw, user_msg="不知道该怎么办") == ""


def test_guard_intercepts_provider_error_sentinels():
    from utils.prompt_sanitize import terminal_reply_guard

    for raw in (
        "（生成回复时出现异常，请稍后再试）",
        "（服务暂时不可用）",
        "（生成已超时）",
    ):
        assert terminal_reply_guard(raw) == "", raw


def test_guard_passes_normal_reply():
    from utils.prompt_sanitize import terminal_reply_guard

    assert terminal_reply_guard("哼，又好的好的。", user_msg="好的好的") == "哼，又好的好的。"


# ═══════════════════════════════════════════════════════════
# 2. 表情复读剥离（能力保留，只剥机械复读）
# ═══════════════════════════════════════════════════════════


def test_guard_strips_emoji_echo_production_case():
    """10-14 生产原文：用户 [流泪]，回复 [流泪] 开头逐字复读。"""
    from utils.prompt_sanitize import terminal_reply_guard

    reply = "[流泪]\n别难过了，课业压力大是正常的。\n但别忘了，你还有我在这里。"
    out = terminal_reply_guard(reply, user_msg="[流泪]")
    assert out.startswith("别难过了")
    assert "[流泪]" not in out


def test_guard_strips_echo_keeps_own_emoji():
    """剥用户复读 token，保留模型自己另配的表情（[皱眉] 复读剥掉，[摸头] 保留）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    reply = "[皱眉] [摸头] 看来你今天遇到了什么不顺心的事情呢。"
    out = terminal_reply_guard(reply, user_msg="[皱眉]")
    assert out == "[摸头] 看来你今天遇到了什么不顺心的事情呢。"


def test_guard_pure_echo_collapses_to_empty():
    """整条回复只剩复读表情 → 空串，交由上层 empty_reply 角色化兜底。"""
    from utils.prompt_sanitize import terminal_reply_guard

    assert terminal_reply_guard("[666]", user_msg="[666]") == ""


def test_guard_no_touch_when_user_not_pure_emoji():
    """用户消息带文字时，回复开头的表情是模型自己的表达，不得误剥。"""
    from utils.prompt_sanitize import terminal_reply_guard

    reply = "[流泪] 想你了"
    assert terminal_reply_guard(reply, user_msg="在干嘛呢") == "[流泪] 想你了"


def test_guard_no_touch_third_repeat_case():
    """10-13 对照：用户 [流泪]×3、agnes 回「又哭。」——非复读回复零改动。"""
    from utils.prompt_sanitize import terminal_reply_guard

    assert terminal_reply_guard("又哭。\n……行吧，想哭就哭会儿，别憋着。", user_msg="[流泪][流泪][流泪]") == (
        "又哭。\n……行吧，想哭就哭会儿，别憋着。"
    )


def test_pure_emoji_message_detection():
    from utils.prompt_sanitize import is_pure_emoji_message

    assert is_pure_emoji_message("[流泪]") is True
    assert is_pure_emoji_message("[流泪][流泪][流泪]") is True
    assert is_pure_emoji_message("[流泪] 不是在聊天吗") is False
    assert is_pure_emoji_message("") is False
    assert is_pure_emoji_message("今天好累呀") is False
    # [666] 是真实微信表情（生产原文），数字不得排除
    assert is_pure_emoji_message("[666]") is True


def test_system_placeholders_are_not_emoji():
    """2026-10-14 误伤修复（test_main_stream 抓出）：系统占位不是表情。

    首版把一切 [xx] 当表情，安全层消毒结果「[已消毒]」被当复读 token 误剥，
    流式注入消毒用例当场变红。
    """
    from utils.prompt_sanitize import is_pure_emoji_message, strip_emoji_echo

    for placeholder in (
        "[已消毒]",
        "[已过滤]",
        "[blocked:political]",
        "[收到一张图片]",
        "[你亲眼看到：桌上一碗面]",
    ):
        assert is_pure_emoji_message(placeholder) is False, placeholder
    # 系统占位在回复里也不得被剥离
    assert strip_emoji_echo("[已消毒]", "ignore") == "[已消毒]"


# ═══════════════════════════════════════════════════════════
# 3. 退化回复 + markdown 剥除
# ═══════════════════════════════════════════════════════════


def test_guard_intercepts_degenerate_char_name_reply():
    """10-13 生产实证：回复只剩角色名「十四」。"""
    from utils.prompt_sanitize import terminal_reply_guard

    assert terminal_reply_guard("十四", user_msg="不知道该怎么办", character_name="十四") == ""
    assert terminal_reply_guard("十四。", user_msg="嗯", character_name="十四") == ""
    # 正常短回复不误伤
    assert terminal_reply_guard("早。", user_msg="早", character_name="十四") == "早。"


def test_sanitize_strips_markdown_bold():
    """智谱降级轮 markdown 列表进微信根治。"""
    from utils.prompt_sanitize import sanitize_reply_text

    raw = "1. **冰敷**：刚受伤的前24-48小时内，可以用冰袋冷敷"
    assert sanitize_reply_text(raw) == "1. 冰敷：刚受伤的前24-48小时内，可以用冰袋冷敷"
    assert "**" not in sanitize_reply_text("**好啦**别担心**真的**")


# ═══════════════════════════════════════════════════════════
# 4. 被否认称呼检测：「X是谁」语序补漏
# ═══════════════════════════════════════════════════════════


def test_detect_catches_name_first_word_order():
    """10-14 事故原文：「十七是谁」（名字在前）必须命中。"""
    from proactive.llm_proactive import detect_denied_nicknames

    msgs = [{"role": "user", "content": "十七是谁"}]
    assert "十七" in detect_denied_nicknames(msgs)


def test_detect_keeps_legacy_word_order():
    from proactive.llm_proactive import detect_denied_nicknames

    msgs = [{"role": "user", "content": "谁是夏夏"}]
    assert "夏夏" in detect_denied_nicknames(msgs)


@pytest.mark.parametrize(
    "text",
    [
        "你是谁",  # 代词
        "那你是谁",  # 含代词虚词串
        "到底是谁",  # 疑问副词
        "究竟是谁",
        "这是谁",
        "我到底是谁啊",
    ],
)
def test_detect_no_false_positive_on_question_fillers(text):
    from proactive.llm_proactive import detect_denied_nicknames

    assert detect_denied_nicknames([{"role": "user", "content": text}]) == []


def test_detect_combines_orders_and_dedupes():
    from proactive.llm_proactive import detect_denied_nicknames

    msgs = [
        {"role": "user", "content": "谁是夏夏"},
        {"role": "user", "content": "十七是谁"},
        {"role": "user", "content": "别叫我十七"},
    ]
    denied = detect_denied_nicknames(msgs)
    assert denied.count("十七") == 1
    assert "夏夏" in denied


# ═══════════════════════════════════════════════════════════
# 5. 主链 prompt 注入：身份铁则 / 表情守则 / 用户称呼白名单段
# ═══════════════════════════════════════════════════════════


def _build_prompt(memory_context=None) -> str:
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    return ps.build_system_prompt(
        emotion_state=None,
        memory_context=memory_context or {},
        character_id=None,
        user_message="在吗",
    )


def test_main_chain_prompt_contains_identity_rule():
    """主链此前完全无身份锚（空缺即事故根因），必须出现身份铁则。"""
    prompt = _build_prompt()
    assert "用户身份铁则" in prompt
    assert "不是**角色卡世界观里的人物" in prompt or "不是角色卡世界观里的人物" in prompt
    assert "不得给用户起名字" in prompt
    assert "物理空间" in prompt
    # 拟人化修正（2026-10-14 用户质疑）：亲密呼语豁免必须显式在案
    assert "「笨蛋」「傻瓜」" in prompt
    assert "真实记得的事要自然地用" in prompt


def test_main_chain_prompt_contains_emoji_rule():
    prompt = _build_prompt()
    assert "微信表情使用守则" in prompt
    assert "禁止复读用户刚发的同一条表情" in prompt


def test_main_chain_prompt_renders_address_whitelist_segment(monkeypatch):
    """2026-10-15 称呼根治批：主链渲染【用户称呼】白名单段（替代黑名单独立段）。

    测试意图不变且更强：被否认的名字不得进 prompt —— 新段以「正面指定唯一合法
    称呼 + 否认清单补充行」满足（旧段只有禁令清单，无正面指定）。
    """
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: {"十七", "夏夏"})
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    prompt = _build_prompt({"user_key": "N:guard-batch"})
    assert "# 用户称呼" in prompt, "白名单段必须进主链（正面指定合法称呼）"
    seg = prompt.split("【被否认称呼】")[1][:200]
    assert "「十七」" in seg and "「夏夏」" in seg
    assert "不得再用这些名字称呼用户" in seg
    # 白名单段只渲染一次（双段 = 两个 owner 漂移）
    assert prompt.count("# 用户称呼") == 1


def test_main_chain_prompt_no_address_segment_without_user_key():
    """无会话键 → 整段不注入（宁缺毋串；旧 denied_nicknames 渲染已退役）。"""
    prompt = _build_prompt({"denied_nicknames": ["十七"]})
    assert "# 用户称呼" not in prompt
    assert "【被否认称呼】" not in prompt


def test_main_chain_prompt_no_denied_line_without_denials(monkeypatch):
    """有会话键但无否认记录 → 白名单段在位、否认清单行缺席。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    prompt = _build_prompt({"user_key": "N:guard-clean"})
    assert "# 用户称呼" in prompt
    assert "【被否认称呼】" not in prompt


def test_identity_rule_precedes_role_clarity():
    """身份铁则必须排在角色说明之前（身份锚批教训：前置对抗历史惯性）。"""
    prompt = _build_prompt()
    assert prompt.index("用户身份铁则") < prompt.index("对话角色说明")


# ═══════════════════════════════════════════════════════════
# 5b. 重复注入根治不变量（2026-10-14 用户质疑驱动）
#
# 生产实测：同一条 prompt 里身份拷问剧本 ×2、表情规则 ×5、回复长度
# 三种口径互斥（引擎 10-30字 / 卡文 10-40字 / reply_mode 10~80字）。
# 修法=按 owner 收口：长度唯一 owner=reply_mode；卡文自带剧本时关引擎
# 通用段；表情风格唯一 owner=角色卡。
# ═══════════════════════════════════════════════════════════


def test_prompt_has_single_identity_interrogation_segment():
    """内置卡（卡文自带剧本）：身份拷问段全 prompt 恰好 1 份。"""
    prompt = _build_prompt()
    assert prompt.count("身份拷问应答") + prompt.count("身份拷问应对") == 1


def test_prompt_has_no_hardcoded_length_table():
    """引擎层回复长度硬编码表已删（长度唯一 owner=reply_mode）。"""
    prompt = _build_prompt()
    for stale in ("日常对话：10-30字", "表达情感：20-50字", "最长不超过100字"):
        assert stale not in prompt, f"重复注入复发: {stale}"


def test_file_card_still_gets_generic_identity_defense():
    """文件卡（无剧本）：身份中性通用段必须保留（覆盖 42 卡防线不弱化）。"""
    from shisi.application.persona_service import PersonaService

    layer = PersonaService(config_loader=None, llm_gateway=None)._engine.build_constraint_layer()
    assert "身份拷问应对" in layer
    assert "十四" not in layer, "约束层身份中性：不得内置默认人格名"


def test_constraint_layer_switch_controls_identity_segment():
    """开关行为：include_identity_defense=False 只去掉身份段，其余约束保留。"""
    from shisi.application.persona_service import PersonaService

    engine = PersonaService(config_loader=None, llm_gateway=None)._engine
    off = engine.build_constraint_layer(include_identity_defense=False)
    on = engine.build_constraint_layer(include_identity_defense=True)
    assert "身份拷问应对" not in off and "身份拷问应对" in on
    assert "身份自指话术" in off and "身份自指话术" in on, "自指话术是另一 owner，不得连带删除"
    assert "禁止事项" in off


def test_emoji_rule_defers_count_to_card():
    """表情守则不再自带数量口径（数量归角色卡，防与卡文互斥）。"""
    from utils.prompt_sanitize import EMOJI_USE_RULE

    assert "至多一两个" not in EMOJI_USE_RULE
    assert "以角色卡设定为准" in EMOJI_USE_RULE
    assert "禁止复读" in EMOJI_USE_RULE


# ═══════════════════════════════════════════════════════════
# 6. 出站集成：_finalize_reply 走守卫（行为级）
# ═══════════════════════════════════════════════════════════


def test_finalize_reply_intercepts_error_sentinel(capsys):
    """终审位行为验证：错误哨兵进 _finalize_reply → 走兜底而非原样放行。"""
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    safety = SimpleNamespace(
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=lambda cat: "",
    )
    orch = SimpleNamespace(
        components={"persona": SimpleNamespace(), "safety": safety},
        _finalize_reply=OptimizedOrchestrator._finalize_reply.__get__(SimpleNamespace()),
    )
    # _finalize_reply 是实例方法；借 __get__ 绑定一个只含所需属性的载体
    out = OptimizedOrchestrator._finalize_reply(
        orch,
        "（所有 LLM 提供商均不可用，请检查配置。最后错误: 模型请求失败，未生成角色回复）",
        character_id="default",
        character_card={"name": "十四"},
        user_msg="不知道该怎么办",
    )
    assert "均不可用" not in out
    assert out  # 走到了角色化兜底句，不再是错误文案


def test_finalize_reply_strips_echo(capsys):
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    safety = SimpleNamespace(
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=lambda cat: "",
    )
    orch = SimpleNamespace(components={"persona": SimpleNamespace(), "safety": safety})
    out = OptimizedOrchestrator._finalize_reply(
        orch,
        "[流泪]\n别难过了，课业压力大是正常的。",
        character_id="default",
        character_card={"name": "十四"},
        user_msg="[流泪]",
    )
    assert out.startswith("别难过了")


def test_guard_and_finalize_share_source():
    """终审守卫必须单一真源（防再次分叉成两套哨兵/复读判定）。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._finalize_reply)
    assert "terminal_reply_guard" in src
