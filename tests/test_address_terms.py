"""用户称呼白名单机制回归测试（2026-10-15 称呼根治批）。

**根因**（用户报障「夏夏」复犯 + 用户裁决「想办法解决这种称呼问题，进行根治」）：
「怎么称呼用户」这个槽位此前没有唯一权威来源——模型从上下文里**看见什么用什么**。三批
旧防线（10-13 身份锚 / 10-14 画像否决门 / 10-14 出站守卫）全是**黑名单**结构，三重
结构性依赖：①依赖用户**先发现**错误并否认；②依赖检测窗口**不滑出**；③依赖模型**听话**。

本批翻转为**白名单唯一真源**（`utils/address_terms`，唯一 owner）：
  1. `resolve_address` → ``(唯一合法称呼, 非法称呼集)``；forbidden 含**角色名派生词**
     （林挽夏 → 夏夏/挽挽/林林/小夏…，无需用户发现即兜住「夏夏」根因）；
  2. `address_prompt_block` → 注入段（正面指定，替代禁令清单）；
  3. `strip_forbidden_vocatives` → 出站/历史确定性剥除（词边界，不误伤「十七岁」）。

覆盖：解析优先级 / 角色名派生 / 根因回归 / 误伤保护 / 幂等 / 用户隔离 / 注入段关键词 /
主链与决策线接线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.address_terms import (
    address_prompt_block,
    character_name_derivatives,
    resolve_address,
    strip_forbidden_vocatives,
    strip_illegal_vocative,
)

CHARACTERS_DIR = Path(__file__).resolve().parent.parent / "config" / "characters"
#: 生产事故角色（「夏夏」= 林挽夏的叠字被用来称呼用户）
ACCIDENT_CHARACTER_ID = "62105bca"


def _patch_sources(monkeypatch, *, nickname: str = "", denied: set[str] | None = None,
                   char_name: str = "十四") -> None:
    """把称呼真源三要素置为确定性替身（模块内按名字调用，patch 模块属性即可）。

    - 画像昵称 / 否认称呼：只替换**数据源**，被测的优先级裁决与渲染逻辑不走替身；
    - 角色展示名：'林挽夏' 等由测试指定，不依赖 gitignore 的角色卡目录是否在位。
    """
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: nickname)
    monkeypatch.setattr(at, "_denied_names", lambda sk: set(denied or set()))
    monkeypatch.setattr(cr, "display_name", lambda cid: char_name)


# ═══════════════════════════════════════════════════════════
# 1. resolve_address：裁决优先级（否认 > 昵称 > 角色名派生自动禁用）
# ═══════════════════════════════════════════════════════════


def test_resolve_address_defaults_to_you_without_nickname(monkeypatch):
    """无画像昵称 → legal 恒为「你」（一个确定值，不是清单）。"""
    _patch_sources(monkeypatch, nickname="", char_name="十四")
    legal, forbidden = resolve_address("N:no-nick", "default")
    assert legal == "你"
    assert "十四" in forbidden, "角色名本身必须自动禁用（永远只能指角色自己）"


def test_resolve_address_uses_profile_nickname(monkeypatch):
    """有画像昵称且未被否认 → legal = 昵称，且该昵称从 forbidden 中移除。"""
    _patch_sources(monkeypatch, nickname="阿树", char_name="十四")
    legal, forbidden = resolve_address("N:nick", "default")
    assert legal == "阿树"
    assert "阿树" not in forbidden


def test_resolve_address_denied_nickname_falls_back_to_you(monkeypatch):
    """昵称被用户否认 → legal 回落「你」，且该昵称进 forbidden（用户否决 > 画像）。"""
    _patch_sources(monkeypatch, nickname="十七", denied={"十七"}, char_name="十四")
    legal, forbidden = resolve_address("N:denied", "default")
    assert legal == "你"
    assert "十七" in forbidden


def test_resolve_address_return_types_are_frozen(monkeypatch):
    """forbidden 必须是不可变集（下游只读消费，防被就地改坏）。"""
    _patch_sources(monkeypatch, nickname="", char_name="林挽夏")
    _, forbidden = resolve_address("N:types", ACCIDENT_CHARACTER_ID)
    assert isinstance(forbidden, frozenset)


# ═══════════════════════════════════════════════════════════
# 2. character_name_derivatives：角色名派生词（「夏夏」根因的结构性修复）
# ═══════════════════════════════════════════════════════════


def test_character_name_derivatives_linwanxia(monkeypatch):
    """林挽夏 → 叠字/相邻双字/小X变体全部自动禁用；**不含单字**（防误伤正常语言）。"""
    _patch_sources(monkeypatch, char_name="林挽夏")
    derivs = character_name_derivatives(ACCIDENT_CHARACTER_ID)
    for expected in ("夏夏", "挽挽", "林林", "小夏", "林挽", "挽夏", "林挽夏"):
        assert expected in derivs, f"派生集必须含「{expected}」（宁多勿漏）"
    assert "夏" not in derivs, "单字不进集（「夏天」「下雨」是正常语言，不能剥）"


def test_character_name_derivatives_real_accident_card_skips_when_absent():
    """真实卡在位时复验：62105bca == 林挽夏 → 派生集含「夏夏」。

    角色卡目录被 .gitignore（§9.4，不随 git 复现）→ 卡缺失时跳过而非失败。
    """
    card = CHARACTERS_DIR / f"{ACCIDENT_CHARACTER_ID}.json"
    if not card.exists():
        pytest.skip("config/characters 无 62105bca 卡（gitignore 目录，不进公开仓）")
    try:
        name = str(json.loads(card.read_text(encoding="utf-8")).get("name") or "")
    except (OSError, json.JSONDecodeError):  # noqa: PERF203
        pytest.skip("角色卡不可读")
    if name != "林挽夏":
        pytest.skip(f"62105bca 的展示名已变化（{name}），生产事故卡口径需复查")
    derivs = character_name_derivatives(ACCIDENT_CHARACTER_ID)
    assert "夏夏" in derivs, "「夏夏」事故名必须自动禁用（无需用户发现）"


def test_character_name_derivatives_needs_two_cjk_chars(monkeypatch):
    """单字/非中文角色名 → 空集（无法可靠派生，宁缺勿滥）。"""
    _patch_sources(monkeypatch, char_name="夏")
    assert character_name_derivatives("x") == set()
    _patch_sources(monkeypatch, char_name="Agnes")
    assert character_name_derivatives("x") == set()


# ═══════════════════════════════════════════════════════════
# 3. 根因回归：出站/历史剥除（不依赖模型配合）
# ═══════════════════════════════════════════════════════════


def test_strip_xiaxia_vocative_regression():
    """生产原文回放：「夏夏，看到你深夜祈祷的消息…」→ 称呼剥除、正文完整保留。"""
    out = strip_forbidden_vocatives(
        "夏夏，看到你深夜祈祷的消息，我一直都在。", "你", frozenset({"夏夏"})
    )
    assert not out.startswith("夏夏")
    assert out == "看到你深夜祈祷的消息，我一直都在。"


def test_strip_does_not_touch_word_internal_occurrences():
    """误伤保护（词边界）：词内成分不剥——「十七岁」「无名指」「我是十四」。"""
    forbidden = frozenset({"十七", "无名", "十四"})
    assert strip_forbidden_vocatives("十七岁的雨季", "你", forbidden) == "十七岁的雨季"
    assert strip_forbidden_vocatives("无名指疼", "你", forbidden) == "无名指疼"
    assert strip_forbidden_vocatives("我是十四", "你", forbidden) == "我是十四"


def test_strip_keeps_affectionate_vocative():
    """亲密呼语是语气不是称呼：「笨蛋十七」→「笨蛋」（呼语保留，名字剥除）。"""
    assert strip_forbidden_vocatives("笨蛋十七", "你", frozenset({"十七"})) == "笨蛋"


def test_strip_never_removes_legal_address():
    """合法称呼与代词链不被触碰。"""
    forbidden = frozenset({"十七", "夏夏"})
    assert strip_forbidden_vocatives("阿树，早", "阿树", forbidden) == "阿树，早"
    assert strip_forbidden_vocatives("你，十七。", "你", forbidden).strip() == "你，"


def test_strip_is_idempotent():
    """出站守卫集成：剥除后再跑一次结果不变（守卫可重复挂载不叠加副作用）。"""
    forbidden = frozenset({"夏夏", "十七"})
    once = strip_forbidden_vocatives("夏夏，十七，早呀", "你", forbidden)
    twice = strip_forbidden_vocatives(once, "你", forbidden)
    assert once == twice


def test_strip_returns_empty_for_pure_vocative():
    """整条只剩非法称呼 → 空串（调用方走 empty_reply 兜底，绝不复活原文）。"""
    assert strip_forbidden_vocatives("夏夏", "你", frozenset({"夏夏"})) == ""


def test_strip_illegal_vocative_leading_only():
    """行首原语：只剥称呼位（「林林，早」→「早」），句中提及不动。"""
    forbidden = frozenset({"林林"})
    assert strip_illegal_vocative("林林，早", "你", forbidden) == "早"
    assert strip_illegal_vocative("今天林林来了", "你", forbidden) == "今天林林来了"


# ═══════════════════════════════════════════════════════════
# 4. 注入段：正面指定（替代禁令清单）
# ═══════════════════════════════════════════════════════════


def test_address_prompt_block_positive_directive(monkeypatch):
    """注入段必须是**正面指定**：「只能」用唯一合法称呼 + 自己的名字不得称呼用户。"""
    _patch_sources(monkeypatch, nickname="阿树", char_name="林挽夏")
    block = address_prompt_block("N:block", ACCIDENT_CHARACTER_ID)
    assert block
    assert "只能" in block, "必须是正面指定（白名单），不是禁令清单"
    assert "你自己的名字" in block, "必须显式说明角色名/变体只能指自己"
    assert "「阿树」" in block, "合法称呼必须写进段内（模型有唯一可用的值）"


def test_address_prompt_block_no_nickname_uses_you(monkeypatch):
    _patch_sources(monkeypatch, nickname="", char_name="林挽夏")
    block = address_prompt_block("N:block2", ACCIDENT_CHARACTER_ID)
    assert "「你」" in block
    assert "「夏夏」" not in block.split("【被否认称呼】")[0], "无否认记录时不列否认清单"


def test_address_prompt_block_lists_denials(monkeypatch):
    """否认清单仍在（黑名单语义降级为白名单段的补充行），且措辞含禁令。"""
    _patch_sources(monkeypatch, nickname="", denied={"十七"}, char_name="十四")
    block = address_prompt_block("N:block3", "default")
    seg = block.split("【被否认称呼】")[1]
    assert "「十七」" in seg
    assert "不得再用" in seg


def test_address_prompt_block_empty_without_session_key(monkeypatch):
    """无会话键 → 空串（无身份可挂，整段不注入，宁缺毋串）。"""
    _patch_sources(monkeypatch, nickname="阿树", char_name="十四")
    assert address_prompt_block("", "default") == ""


# ═══════════════════════════════════════════════════════════
# 5. 用户隔离（多用户不串扰，动态读不缓存）
# ═══════════════════════════════════════════════════════════


def test_resolve_address_isolates_between_sessions(monkeypatch):
    """两个会话键解析结果互异：一个有名可用，一个只能称「你」。"""
    import utils.address_terms as at

    monkeypatch.setattr(
        at,
        "_profile_nickname",
        lambda sk: "阿树" if sk == "N:a" else "",
    )
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    import utils.character_resolver as cr

    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    legal_a, _ = resolve_address("N:a", "default")
    legal_b, _ = resolve_address("N:b", "default")
    assert legal_a == "阿树"
    assert legal_b == "你"
    assert legal_a != legal_b, "称呼解析必须按会话键隔离，禁止跨用户复用"


def test_resolve_address_reads_live_sources_not_cached(tmp_path, monkeypatch):
    """动态读铁律：真源变化后**同一会话键**的下一次解析必须立刻反映（零缓存）。

    端到端走真实 ledger（画像事件 + 用户否认消息），不 patch address_terms 内部。
    """
    from shisi.agent_plane import runtime as apruntime
    from shisi.agent_plane.event_ledger import EventLedger

    led = EventLedger(tmp_path / "address_e2e.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    import utils.character_resolver as cr

    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    sk = "N:e2e"
    assert resolve_address(sk, "default")[0] == "你", "初始无昵称 → 「你」"

    led.append(session_key=sk, event_type="profile_update", payload={"nickname": "阿树"})
    legal, _ = resolve_address(sk, "default")
    assert legal == "阿树", "画像写入后下一次解析必须立刻可见（动态读）"

    led.append(session_key=sk, event_type="user_message", payload={"text": "我不是阿树"})
    legal2, forbidden2 = resolve_address(sk, "default")
    assert legal2 == "你", "用户否认后必须回落「你」"
    assert "阿树" in forbidden2, "被否认的昵称必须进禁用集"


def test_address_prompt_block_e2e_from_real_ledger(tmp_path, monkeypatch):
    """端到端：真实 ledger 的否认记录（经 runtime 双源）必须进注入段。"""
    from shisi.agent_plane import runtime as apruntime
    from shisi.agent_plane.event_ledger import EventLedger

    led = EventLedger(tmp_path / "address_block.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    import utils.character_resolver as cr

    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    sk = "N:e2e-block"
    # 用户否认（源①窗口检测：ledger 全量 user_message 扫描）
    led.append(session_key=sk, event_type="user_message", payload={"text": "谁是十七"})
    block = address_prompt_block(sk, "default")
    assert "「十七」" in block, "ledger 里的否认必须进白名单段的否认行"


# ═══════════════════════════════════════════════════════════
# 6. 接线：主链注入 / 出站定稿 / 决策线上下文
# ═══════════════════════════════════════════════════════════


def test_persona_service_injects_address_block(monkeypatch):
    """主链 system prompt：白名单段在位，且排在身份铁则之后（历史惯性对抗顺序）。"""
    import utils.address_terms as at
    import utils.character_resolver as cr
    from shisi.application.persona_service import PersonaService

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "阿树")
    monkeypatch.setattr(at, "_denied_names", lambda sk: {"十七"})
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    ps = PersonaService(config_loader=None, llm_gateway=None)
    prompt = ps.build_system_prompt(
        emotion_state=None,
        memory_context={"user_key": "N:main"},
        character_id="default",
        user_message="在吗",
    )
    assert "# 用户称呼" in prompt
    assert "「阿树」" in prompt
    assert "「十七」" in prompt, "否认清单（白名单段的补充行）必须进主链"
    assert prompt.index("用户身份铁则") < prompt.index("# 用户称呼")
    # 旧黑名单**独立段**退役：不得出现第二份渲染（双段 = 两个 owner 漂移）
    assert "【被否认称呼】以下名字已被用户明确否认" not in prompt.split("# 用户称呼")[0]


def test_finalize_reply_strips_forbidden_vocative(monkeypatch):
    """主链出站定稿：非法称呼在返回前被确定性剥除（不依赖模型配合）。"""
    from types import SimpleNamespace

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    _patch_sources(monkeypatch, nickname="", char_name="林挽夏")
    safety = SimpleNamespace(
        check_output=lambda text: SimpleNamespace(is_safe=True, category=""),
        safe_alternative=lambda cat: "",
    )
    orch = SimpleNamespace(components={"persona": SimpleNamespace(), "safety": safety})
    out = OptimizedOrchestrator._finalize_reply(
        orch,
        "夏夏，看到你深夜祈祷的消息，我一直都在。",
        character_id=ACCIDENT_CHARACTER_ID,
        character_card={"name": "林挽夏"},
        user_msg="在吗",
        session_id="N:finalize",
    )
    assert not out.startswith("夏夏")
    assert "我一直都在" in out, "正文必须完整保留，只剥称呼"


def test_finalize_reply_signature_accepts_session_id():
    """`session_id` 必须是**可选**形参（其他调用点不传也不破）。"""
    import inspect

    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    params = inspect.signature(OptimizedOrchestrator._finalize_reply).parameters
    assert params["session_id"].default == ""


def test_proactive_context_renders_address_block(monkeypatch):
    """决策线上下文：白名单段在位（替代黑名单式否认段）。"""
    import utils.address_terms as at
    import utils.character_resolver as cr
    from proactive.llm_proactive import build_proactive_context

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: {"十七"})
    monkeypatch.setattr(cr, "display_name", lambda cid: "十四")

    ctx = build_proactive_context(
        session_key="N:proactive",
        local_time="2026-10-15 12:00 Thursday",
        character_id="default",
    )
    assert "# 用户称呼" in ctx
    seg = ctx.split("【被否认称呼】")[1]
    assert "「十七」" in seg
    assert "不得再用" in seg


def test_proactive_context_accepts_character_id_kwarg():
    """规格缺口补齐：`character_id` 必须是带默认值的可选形参（旧调用点不破）。"""
    import inspect

    from proactive.llm_proactive import build_proactive_context

    sig = inspect.signature(build_proactive_context)
    assert sig.parameters["character_id"].default == ""


def test_proactive_context_survives_address_source_failure(monkeypatch):
    """称呼真源抛错 → 上下文照常组装、不 NameError（fail-open 记录在案）。

    防复发点：降级分支读 `_addr_block` —— 若异常路径下该变量未预置，会抛
    `UnboundLocalError` 把整条决策线打挂（比不注入称呼严重得多）。
    """
    import proactive.llm_proactive as lp
    import utils.address_terms as at

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(at, "address_prompt_block", _boom)

    ctx = lp.build_proactive_context(
        session_key="N:boom",
        local_time="2026-10-15 12:00 Thursday",
        character_id="default",
    )
    assert "会话键：N:boom" in ctx, "上下文必须照常组装（fail-open）"
    assert "# 用户称呼" not in ctx, "真源失败 → 该段缺席（不得注入半段）"


def test_scheduler_passes_character_id_into_context():
    """🔴 钉调用来源：`_llm_proactive_one_user` 必须把角色 id 传进 context。

    不传则决策线拿不到角色名派生禁用集（「夏夏」根因在决策线复活）。
    AST 级断言（字符串包含会假绿：同函数内 `append_proactive_event(...)`
    也带 `character_id=char_id`）。
    """
    import ast
    import inspect
    import textwrap

    from proactive import scheduler as sched_mod

    src = textwrap.dedent(
        inspect.getsource(sched_mod.ProactiveScheduler._llm_proactive_one_user)
    )
    calls = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "build_proactive_context"
    ]
    assert calls, "未找到 build_proactive_context 调用（结构性变更需同步本用例）"
    for call in calls:
        kwargs = {kw.arg: kw for kw in call.keywords}
        assert "character_id" in kwargs, "决策线必须传当前角色 id（派生禁用依赖它）"
        assert getattr(kwargs["character_id"].value, "id", "") == "char_id", (
            "必须传局部变量 char_id（角色切换竞态已在其上下游校验）"
        )


# ═══════════════════════════════════════════════════════════
# 7. 历史净化接线（防「错误称呼回灌自我强化」——行为级）
# ═══════════════════════════════════════════════════════════


async def test_prepare_context_scrubs_history_vocatives(monkeypatch):
    """`_prepare_context` 的 chat_history 出参必须已净化（错误称呼回灌链路切断）。

    根因链路：历史行里残留「夏夏，…」→ 模型从历史里"看见什么学什么" →
    下一轮继续叫「夏夏」→ 落库再进历史 = 自我强化闭环。此断言钉死净化**发生**，
    且**净化后空行被丢弃**（不得留空 content 行进 messages）。
    """
    from types import SimpleNamespace  # noqa: F401

    import utils.address_terms as at
    import utils.character_resolver as cr
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: "")
    monkeypatch.setattr(at, "_denied_names", lambda sk: set())
    monkeypatch.setattr(cr, "display_name", lambda cid: "林挽夏")

    history = [
        {"role": "assistant", "content": "夏夏，看到你深夜祈祷的消息"},
        {"role": "user", "content": "谁是夏夏？我不是夏夏"},  # 词内成分（「是夏夏」不剥）
        {"role": "assistant", "content": "夏夏"},  # 纯呼语 → 剥成空 → 整行丢弃
    ]

    class _Memory:
        structured_memory = None

        def get_recent_context(self, n, session_id="", character_id=""):
            return ""

        def retrieve_context(self, **kw):
            return {}

        def get_chat_context(self, **kw):
            return list(history), ""

    class _Rag:
        def retrieve(self, query, character_id=None):
            return {}

    orch = OptimizedOrchestrator()
    orch.components = {
        "persona_extractor": None,
        "emotion": SimpleNamespace(analyze=lambda m, r: SimpleNamespace(affinity=0)),
        "memory": _Memory(),
        "rag": _Rag(),
        "persona": SimpleNamespace(build_system_prompt=lambda **kw: "sys"),
        "world_info": None,
        "llm": None,
    }

    out = await orch._prepare_context("在吗", "N:prep-scrub", ACCIDENT_CHARACTER_ID)
    rows = out["chat_history"]
    assert rows, "历史不得整体消失"
    for row in rows:
        assert not str(row.get("content") or "").startswith("夏夏"), (
            f"出参历史仍带非法称呼开头（回灌链路未切断）: {row!r}"
        )
    assert any("看到你深夜祈祷的消息" in str(r.get("content") or "") for r in rows), (
        "正文必须保留（只剥称呼）"
    )
    assert len(rows) == 2, f"纯呼语行净化后为空必须整行丢弃，实得 {rows!r}"
