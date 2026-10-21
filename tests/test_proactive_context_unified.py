"""主动消息上下文统一真源（2026-10-15 质量回调批 · 包B）。

生产实证驱动：
1. ASE 生成线模板写死「十四傲娇」——42 张角色卡用户收到的主动消息全是十四口吻；
2. 决策链画像投影只取 nickname/birthday/occupation/location/commitments，
   preferences 没进 prompt——画像明确记「不喜欢被一直催」照催吃早饭；
3. 用户 7 次否认「十七」称呼，主动链只扫最近消息窗口，滑出窗口后照叫。

本文件钉死统一后的三件：
- proactive_context.load_full_persona_hint：完整版人设（default 含
  【回复示例】【通用禁令】，卡含全部字段段），双角色互异（用户隔离）；
- immersive_proactive_constraints：沉浸式/小说式双模式约束段；
- build_proactive_context：画像走主链同款 get_profile_prompt_block（含
  preferences）、否认称呼走 denied_names_for 双源否决 + 单源回退。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proactive.llm_proactive import build_proactive_context
from proactive.proactive_context import (
    immersive_proactive_constraints,
    load_full_persona_hint,
)

_CHAR_ROOT = Path(__file__).resolve().parent.parent / "config" / "characters"


def _two_real_cards() -> list[dict]:
    cards: list[dict] = []
    for f in sorted(_CHAR_ROOT.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if d.get("id") and d.get("name"):
            cards.append(d)
        if len(cards) == 2 and cards[0]["id"] != cards[1]["id"]:
            return cards
    if len(cards) >= 2 and cards[0]["id"] != cards[1]["id"]:
        return cards[:2]
    return []


# ── 1. 完整版人设：内置十四 ─────────────────────────────────


def test_full_persona_default_builtin():
    """default → persona.yaml：description 全量 + 通用禁令小节；身份拷问段排除。"""
    hint = load_full_persona_hint("default")
    assert hint, "内置十四人设不得为空（旧 400 裁剪版同因恒空的回归）"
    # 性格段（description 全量进来）
    assert "傲娇" in hint
    # 回复示例段（description 自带【回复示例】，旧版只取 description[:400] 会截掉）
    assert "回复示例" in hint or "哼" in hint
    # creator_notes 的「## 通用禁令」小节必须进来
    assert "通用禁令" in hint
    # 「身份拷问应答」段只服务主链被直球拷问场景，主动消息排除
    assert "身份拷问" not in hint


def test_full_persona_empty_id():
    """空 id → ""（绝不退化成读某个全局角色——那是把别人的角色装进她的嘴）。"""
    assert load_full_persona_hint("") == ""
    assert load_full_persona_hint("   ") == ""


# ── 2. 完整版人设：角色卡 + 用户隔离 ────────────────────────


def test_full_persona_cards_distinct_isolation():
    """两张真实卡的人设互异且各含自卡 name（用户隔离断言：角色不得串内容）。"""
    cards = _two_real_cards()
    if len(cards) < 2:
        pytest.skip(f"config/characters 卡不足 2 张（现 {len(cards)}），隔离断言需双卡")
    a, b = cards
    hint_a = load_full_persona_hint(str(a["id"]))
    hint_b = load_full_persona_hint(str(b["id"]))
    assert hint_a and hint_b
    assert hint_a != hint_b, "两张不同角色的完整人设不得相同"
    assert str(a["name"]) in hint_a, "人设必须带自卡 name"
    assert str(b["name"]) in hint_b, "人设必须带自卡 name"
    # 卡分支小标题段（完整版结构标志，区别于旧 400+200 裁剪版）
    assert "【角色设定】" in hint_a


def test_full_persona_card_truncation_cap():
    """总长上限 1200：超长卡不得把整卡文本原样灌进主动上下文。"""
    cards = _two_real_cards()
    if not cards:
        pytest.skip("config/characters 无可用卡")
    for c in cards:
        assert len(load_full_persona_hint(str(c["id"]))) <= 1200


# ── 3. 沉浸式/小说式模式约束段 ──────────────────────────────


def test_immersive_constraints_default_mode(monkeypatch, tmp_path):
    """默认（沉浸式）：非共处 + 禁客服腔约束在场。"""
    import utils.reply_mode as rm

    # 隔离到临时配置文件：不读也不写宿主真实 data/scheduler_config.json，
    # 缺文件 → read_reply_mode 回落默认 immersive，断言与宿主状态解耦
    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "reply_mode_test.json")
    original = rm.read_reply_mode()
    try:
        assert rm.read_reply_mode() == "immersive"
        text = immersive_proactive_constraints()
        assert text, "约束段不得为空"
        assert ("非共处" in text) or ("同一物理空间" in text), "必须含非共处约束"
        assert "客服腔" in text, "必须禁客服腔"
        assert "括号" in text, "沉浸式必须禁括号动作/神态"

        # 切小说式：允许括号动作/神态的语义必须出现（模式语义随真源切换）
        rm.write_reply_mode("novel")
        text_novel = immersive_proactive_constraints()
        assert text_novel != text, "两种模式的约束段必须不同"
        assert ("动作" in text_novel) or ("神态" in text_novel), "小说式须允许动作/神态"
        assert "客服腔" in text_novel, "小说式仍禁客服腔"
    finally:
        # 恢复原模式（沙箱文件内闭环，宿主真实配置不受影响）
        rm.write_reply_mode(original)


def test_immersive_constraints_dynamic_no_cache(monkeypatch, tmp_path):
    """约束段每次动态读真源：写入即生效，无模块级缓存（模式切换即时性）。"""
    import utils.reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "reply_mode_test.json")
    original = rm.read_reply_mode()
    try:
        first = immersive_proactive_constraints()
        rm.write_reply_mode("novel")
        second = immersive_proactive_constraints()
        assert first != second, "切模式后约束段必须立刻变化（禁止缓存）"
    finally:
        rm.write_reply_mode(original)


# ── 4. 决策线上下文：画像统一读入口 ─────────────────────────


def test_build_context_profile_block_upgrade(monkeypatch):
    """画像走 get_profile_prompt_block（含 preferences）；旧【用户画像投影】退役。"""
    from shisi.agent_plane import runtime as apruntime

    block = "# 用户画像（稳定事实，以本段为准；段外信息禁止编造）\n- 偏好：不喜欢被一直催（如催吃早点）"
    monkeypatch.setattr(apruntime, "get_profile_prompt_block", lambda sk: block)

    ctx = build_proactive_context(
        session_key="N:iso-profile",
        local_time="2026-10-15 12:00 Thursday",
        hours_since_last_chat=3.0,
    )
    assert block in ctx, "主链画像段（含 preferences）必须原样进决策上下文"
    assert "不喜欢被一直催" in ctx
    assert "【用户画像投影】" not in ctx, "旧 bits 投影（无 preferences）必须退役"
    # 不带 session_key 时读不到画像 → 整段不注入（宁缺毋串）
    ctx_no_key = build_proactive_context(local_time="2026-10-15 12:00 Thursday")
    assert "# 用户画像" not in ctx_no_key


def test_build_context_profile_runtime_unavailable_fallback(monkeypatch):
    """runtime 读画像抛错 → 不炸、不留半段（suppress 兜住，上下文照常组装）。"""
    from shisi.agent_plane import runtime as apruntime

    def _boom(sk):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(apruntime, "get_profile_prompt_block", _boom)
    ctx = build_proactive_context(
        session_key="N:iso-boom",
        local_time="2026-10-15 12:00 Thursday",
        persona_hint="角色：测试",
    )
    assert "角色：测试" in ctx, "画像源故障不得拖垮整段上下文组装"
    assert "# 用户画像" not in ctx


# ── 5. 决策线上下文：称呼段（2026-10-15 称呼根治批：白名单唯一真源）────


def test_build_context_denied_dual_source(monkeypatch):
    """带 session_key → 白名单段含持久否决源（滑出窗口后不得复犯）。

    2026-10-15 称呼根治批：渲染从黑名单式【被否认称呼】独立段换为
    `utils.address_terms.address_prompt_block`（正面指定 + 角色名派生自动禁用
    + 否认清单补充行）。测试意图不变：**持久否决源的名字必须被拦住**。
    """
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "denied_names_for", lambda sk: ["十七"])

    ctx = build_proactive_context(
        session_key="N:iso-denied",
        local_time="2026-10-15 12:00 Thursday",
        recent_messages=[],  # 窗口内无否认——命中的只能是持久否决源
    )
    assert "# 用户称呼" in ctx, "白名单段必须在位（正面指定唯一合法称呼）"
    seg = ctx.split("【被否认称呼】")[1][:300]
    assert "十七" in seg, "持久否决源的名字必须进否认清单行（滑出窗口后不得复犯）"


def test_build_context_denied_fallback_single_source():
    """无 session_key（白名单段画不出合法称呼）→ 降级保留旧单源窗口检测禁令行。"""
    ctx = build_proactive_context(
        local_time="2026-10-15 12:00 Thursday",
        recent_messages=[
            {"role": "assistant", "content": "十七，早呀"},
            {"role": "user", "content": "我不是十七，别这么叫我"},
        ],
    )
    assert "【被否认称呼】" in ctx
    assert "十七" in ctx.split("【被否认称呼】")[1][:300]


def test_build_context_whitelist_block_positive_directive(monkeypatch):
    """带会话键的正常形态：正面指定唯一合法称呼（不是只有禁令清单）。"""
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "denied_names_for", lambda sk: [])
    ctx = build_proactive_context(
        session_key="N:iso-whitelist",
        local_time="2026-10-15 12:00 Thursday",
        character_id="default",
    )
    assert "# 用户称呼" in ctx
    assert "只能" in ctx
    assert "你自己的名字" in ctx


# ── 6. FIX-2（2026-10-21）：人设段按模式剥括号动作用 ─────────────
#
# 缺陷：load_full_persona_hint 把卡片的 mes_example[:600] / creator_notes[:400]
# 原样拼进【回复示例】【扮演规则】（proactive_context.py:109-114），yaml 分支的
# description 同样原样（:65-69）。无视 read_reply_mode() —— 生产 42 张卡里
# 37 张 mes_example、23 张 creator_notes 含括号动作，沉浸式主动消息的
# **第一手 few-shot 就是括号动作**，与同一 prompt 里的 _IMMERSIVE_CONSTRAINTS
# 「严禁括号动作/神态/旁白」自相矛盾（「把带括号的示例喂进 prompt 又不拦」）。
# 与首轮 R1 主链缺陷同机理（character_aggregate 已修），主动线被漏掉。
#
# 修法：按 read_reply_mode()==immersive 先 strip_stage_directions 再拼段，
# 剥空则该段不注入（与 character_aggregate 同款策略：剥空不注入，不留空标题）。
# 每次调用动态读真源，不缓存。

import re as _re  # noqa: E402

# 与 utils.reply_mode 单一真源同形的括号/星号动作段判据
_STAGE_RE = _re.compile(r"[（(][^（）()]{0,200}?[）)]|\*[^*\n]{1,200}\*")


def _force_mode(monkeypatch, mode: str) -> None:
    """把回复模式真源钉到指定值（生产代码函数内局部 import → 打模块属性即生效）。"""
    import utils.reply_mode as rm

    # 2026-10-21 R7：签名加 `user_key` 后 mock 必须接受实参（语义不变：
    # 仍把模式钉死，不读宿主真实配置）。
    monkeypatch.setattr(rm, "read_reply_mode", lambda *a, **k: mode)


def _write_card(root: Path, cid: str, **fields) -> None:
    card = {
        "id": cid,
        "name": fields.pop("name", "测试角色"),
        **fields,
    }
    (root / f"{cid}.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8",
    )


def _section(hint: str, title: str) -> str:
    """取人设块里 ``【标题】\\n正文`` 那一段（到下一个 ``\\n\\n【…】`` 标题为止）。

    段内允许空行（示例对话常带空行），因此不能用 ``split("\\n\\n")``——那会在
    段内空行处截断，令断言假通过。标题行以 ``^【…】$`` 形态出现，据此定界。
    """
    m = _re.search(
        rf"(?m)^{_re.escape(title)}\n(.*?)(?=\n\n【[^】]+】\n|\Z)", hint, _re.S,
    )
    return m.group(1) if m else ""


def test_persona_hint_strips_stage_directions_in_immersive(monkeypatch, tmp_path):
    """沉浸式：示例/规则段（【回复示例】【扮演规则】）无括号动作。"""
    from proactive import proactive_context as pctx

    _write_card(
        tmp_path, "cid-imm",
        description="（她站在窗边）傲娇的测试角色。",
        personality_text="（挑眉）嘴硬心软。",
        speaking_style="简短、带刺。",
        mes_example="用户：早\n测试角色：（想了想）早呀，今天降温了。",
        creator_notes="（微笑）保持傲娇；不许客服腔。",
    )
    _force_mode(monkeypatch, "immersive")
    hint = pctx._persona_from_card(tmp_path, "cid-imm")

    assert hint, "人设不得为空"
    assert not _STAGE_RE.search(_section(hint, "【回复示例】")), (
        f"沉浸式【回复示例】仍含括号动作: {_section(hint, '【回复示例】')!r}"
    )
    assert not _STAGE_RE.search(_section(hint, "【扮演规则】")), (
        f"沉浸式【扮演规则】仍含括号动作: {_section(hint, '【扮演规则】')!r}"
    )
    # 剥的是动作段，正文必须留存（剥空 ≠ 整段丢弃）
    assert "早呀，今天降温了" in hint
    assert "不许客服腔" in hint


def test_persona_hint_keeps_role_definition_segments(monkeypatch, tmp_path):
    """作用面收窄：description/personality_text 的括号限定语**不得**被剥。

    实测卡文这两段的括号绝大多数是规则限定语而非动作（李信「（优先级从高到低，
    不可越级违背）」、上杉绘梨衣「（凌驾于所有规则之上…）」），剥掉等于扣角色规则。
    """
    from proactive import proactive_context as pctx

    _write_card(
        tmp_path, "cid-def",
        description="（优先级从高到低，不可越级违背）测试角色。",
        personality_text="（强制不可修改）",
        mes_example="（想了想）早呀。",
    )
    _force_mode(monkeypatch, "immersive")
    hint = pctx._persona_from_card(tmp_path, "cid-def")

    assert "（优先级从高到低，不可越级违背）" in hint, "角色设定段的规则限定语不得被剥"
    assert "（强制不可修改）" in hint, "性格段的规则限定语不得被剥"


def test_persona_hint_keeps_stage_directions_in_novel(monkeypatch, tmp_path):
    """小说式：括号动作是设计的一部分，示例/规则段原样保留（不误伤）。"""
    from proactive import proactive_context as pctx

    _write_card(
        tmp_path, "cid-novel",
        description="（她站在窗边）傲娇的测试角色。",
        mes_example="测试角色：（想了想）早呀。",
        creator_notes="（微笑）保持傲娇。",
    )
    _force_mode(monkeypatch, "novel")
    hint = pctx._persona_from_card(tmp_path, "cid-novel")

    assert "（她站在窗边）" in hint
    assert "（想了想）" in hint, "小说式不得剥 mes_example 括号"
    assert "（微笑）" in hint, "小说式不得剥 creator_notes 括号"


def test_persona_hint_empty_segment_removed_not_empty_title(monkeypatch, tmp_path):
    """沉浸式剥空 → 该段**不注入**，而非留一个空标题行。"""
    from proactive import proactive_context as pctx

    _write_card(
        tmp_path, "cid-empty",
        description="正常角色描述。",
        mes_example="（想了想）",           # 整段都是动作 → 剥空
        creator_notes="（微笑）",            # 整段都是动作 → 剥空
    )
    _force_mode(monkeypatch, "immersive")
    hint = pctx._persona_from_card(tmp_path, "cid-empty")

    assert "【回复示例】" not in hint, "剥空段不得留空标题（【回复示例】）"
    assert "【扮演规则】" not in hint, "剥空段不得留空标题（【扮演规则】）"
    assert "【角色设定】" in hint, "非空段必须留存"


def test_persona_hint_yaml_branch_mode_boundary(monkeypatch, tmp_path):
    """yaml 分支（内置十四）同样按模式剥：description + 通用禁令段。"""
    from proactive import proactive_context as pctx

    yml = tmp_path / "persona.yaml"
    yml.write_text(
        "name: 十四\n"
        "description: |\n"
        "  （她抱着猫）傲娇的十四。\n"
        "creator_notes: |\n"
        "  ## 通用禁令\n"
        "  - 不许客服腔（最高优先级）。\n",
        encoding="utf-8",
    )
    _force_mode(monkeypatch, "immersive")
    hint_imm = pctx._persona_from_yaml(yml)
    assert not _STAGE_RE.search(hint_imm), f"沉浸式 yaml 人设仍含括号: {hint_imm!r}"
    assert "傲娇的十四" in hint_imm and "通用禁令" in hint_imm
    assert "不许客服腔" in hint_imm

    _force_mode(monkeypatch, "novel")
    hint_novel = pctx._persona_from_yaml(yml)
    assert "（她抱着猫）" in hint_novel and "（最高优先级）" in hint_novel


def test_load_full_persona_hint_wires_mode_through_public_api(monkeypatch, tmp_path):
    """公共入口 load_full_persona_hint 必须真正接线（不只是两个内部函数改了）。"""
    from utils import project_paths

    card_root = tmp_path / "characters"
    card_root.mkdir()
    _write_card(
        card_root, "cid-pub",
        description="（她站在窗边）傲娇。",
        mes_example="（想了想）早呀。",
    )
    yml = tmp_path / "persona.yaml"
    yml.write_text("name: 十四\ndescription: |\n  （抱着猫）傲娇十四。\n", encoding="utf-8")

    def _fake_project_path(*parts):
        if parts == ("config", "characters"):
            return card_root
        if parts == ("config", "persona.yaml"):
            return yml
        return tmp_path.joinpath(*parts)

    monkeypatch.setattr(project_paths, "project_path", _fake_project_path)

    _force_mode(monkeypatch, "immersive")
    assert not _STAGE_RE.search(_section(load_full_persona_hint("cid-pub"), "【回复示例】"))
    assert not _STAGE_RE.search(load_full_persona_hint("default"))

    _force_mode(monkeypatch, "novel")
    assert _STAGE_RE.search(_section(load_full_persona_hint("cid-pub"), "【回复示例】"))
    assert _STAGE_RE.search(load_full_persona_hint("default"))


def test_load_full_persona_hint_mode_read_dynamically_no_cache(monkeypatch, tmp_path):
    """模式每次动态读真源：同一 cid 连调两次，切模式即变（禁止模块级缓存）。"""
    from utils import project_paths

    card_root = tmp_path / "characters"
    card_root.mkdir()
    _write_card(card_root, "cid-dyn", mes_example="（想了想）早呀。")
    monkeypatch.setattr(
        project_paths, "project_path",
        lambda *parts: card_root if parts == ("config", "characters") else tmp_path.joinpath(*parts),
    )

    _force_mode(monkeypatch, "immersive")
    first = load_full_persona_hint("cid-dyn")
    _force_mode(monkeypatch, "novel")
    second = load_full_persona_hint("cid-dyn")
    assert first != second, "切模式后人设必须立刻变化（禁止缓存）"
    assert not _STAGE_RE.search(first) and _STAGE_RE.search(second)


def test_persona_hint_real_cards_mode_boundary(monkeypatch):
    """真实卡批量语义下界：沉浸式下示例/规则段无括号；小说式多数卡保留括号。"""
    from proactive import proactive_context as pctx

    cards: list[dict] = []
    for f in sorted(_CHAR_ROOT.glob("*.json")):
        try:
            cards.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    if not cards:
        pytest.skip(f"config/characters 无可用卡（现 {len(cards)}）")

    bracketed_raw = sum(
        1
        for d in cards
        if _STAGE_RE.search(str(d.get("mes_example") or ""))
        or _STAGE_RE.search(str(d.get("creator_notes") or ""))
    )
    assert bracketed_raw >= 30, (
        f"前提不成立：mes_example/creator_notes 含括号动作的真实卡仅 {bracketed_raw} 张"
    )

    _force_mode(monkeypatch, "immersive")
    for d in cards:
        cid = str(d.get("id") or "")
        hint = pctx.load_full_persona_hint(cid)
        for title in ("【回复示例】", "【扮演规则】"):
            seg = _section(hint, title)
            assert not _STAGE_RE.search(seg), (
                f"沉浸式下卡 {cid} 的 {title} 仍含括号动作: {seg!r}"
            )

    _force_mode(monkeypatch, "novel")
    novel_bracketed = sum(
        1
        for d in cards
        if _STAGE_RE.search(
            _section(pctx.load_full_persona_hint(str(d.get("id") or "")), "【回复示例】")
        )
    )
    # 下界取 20（而非原始 37）：人设总长上限 1200 字，【回复示例】排在
    # 角色设定/性格/说话风格之后，长卡截断后示例段整个不可见（实测 42 卡里
    # 只有 28 张能看到该段、其中 25 张含括号）。断言目的只是证明「剥离非空操作」。
    assert novel_bracketed >= 20, (
        f"小说式下【回复示例】含括号的卡仅 {novel_bracketed} 张——剥离非空操作验证失败"
    )
