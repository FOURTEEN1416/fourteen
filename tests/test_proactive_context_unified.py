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


# ── 5. 决策线上下文：否认称呼双源否决 ────────────────────────


def test_build_context_denied_dual_source(monkeypatch):
    """带 session_key → runtime.denied_names_for 双源否决（窗口检测 + ledger 持久否决）。"""
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "denied_names_for", lambda sk: ["十七"])

    ctx = build_proactive_context(
        session_key="N:iso-denied",
        local_time="2026-10-15 12:00 Thursday",
        recent_messages=[],  # 窗口内无否认——命中的只能是持久否决源
    )
    assert "【被否认称呼】" in ctx
    seg = ctx.split("【被否认称呼】")[1][:300]
    assert "十七" in seg, "持久否决源的名字必须进禁令段（滑出窗口后不得复犯）"


def test_build_context_denied_fallback_single_source():
    """无 session_key（runtime 双源不可达）→ 旧单源窗口检测仍工作。"""
    ctx = build_proactive_context(
        local_time="2026-10-15 12:00 Thursday",
        recent_messages=[
            {"role": "assistant", "content": "十七，早呀"},
            {"role": "user", "content": "我不是十七，别这么叫我"},
        ],
    )
    assert "【被否认称呼】" in ctx
    assert "十七" in ctx.split("【被否认称呼】")[1][:300]
