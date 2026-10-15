"""ASE 生成线角色化（2026-10-15 质量回调批 · 包B）。

生产实证：config/prompts/proactive.yaml 第一句写死「你现在是十四，一个傲娇的
AI 伙伴」+ 按时段剧本——42 张角色卡用户收到的 ASE 主动消息全是十四口吻且与
话题脱钩。修法：MessageGenerator 按当轮 character_id 走
proactive_context.load_full_persona_hint 角色驱动主路径；空人设不冒充任何
角色；所有分支注入沉浸式模式约束。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proactive.ase_engine import MessageGenerator, ProactiveType

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


class _Capture:
    """假 LLM 调用：记录 prompt，返回可通过清洗的固定句。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "哼，才不告诉你"


def _generator(capture: _Capture, monkeypatch) -> MessageGenerator:
    """MessageGenerator(None) + 假 _llm/_call（真实 yaml 已降级，模板路径关闭）。"""
    gen = MessageGenerator(None)
    monkeypatch.setattr(gen, "_llm", object(), raising=False)
    monkeypatch.setattr(gen, "_call", capture, raising=False)
    # proactive.yaml 已留空（generation_prompt: ""）；显式钉死防未来回填干扰
    monkeypatch.setattr(gen, "_proactive_prompt", "", raising=False)
    return gen


def _gen_kwargs() -> dict:
    return {
        "emotion_state": {"primary": {"type": "想念"}},
        "affinity_level": 3,
        "hours_since_chat": 2.5,
        "last_user_message": "明天要早起",
    }


def test_dual_character_prompts_distinct(monkeypatch):
    """双角色：两次生成的 prompt 互异且各含自卡人设（角色隔离 + 口吻不串）。"""
    cards = _two_real_cards()
    if len(cards) < 2:
        pytest.skip(f"config/characters 卡不足 2 张（现 {len(cards)}），双卡断言需 2 张")
    a, b = cards
    cap = _Capture()
    gen = _generator(cap, monkeypatch)

    gen.generate_with_llm(ProactiveType.MISS_YOU, character_id=str(a["id"]), **_gen_kwargs())
    gen.generate_with_llm(ProactiveType.MISS_YOU, character_id=str(b["id"]), **_gen_kwargs())

    assert len(cap.prompts) == 2
    pa, pb = cap.prompts
    assert pa != pb, "两张不同角色的生成 prompt 必须不同（旧版全走十四模板必相同）"
    assert str(a["name"]) in pa, "prompt 必须带卡 A 人设"
    assert str(b["name"]) in pb, "prompt 必须带卡 B 人设"
    # 写死的十四模板口吻必须绝迹
    for p in (pa, pb):
        assert "你现在是十四" not in p
        assert "傲娇的AI伙伴" not in p


def test_empty_character_no_impersonation(monkeypatch):
    """character_id 为空/不存在 → 中性兜底分支，不冒充十四。"""
    cap = _Capture()
    gen = _generator(cap, monkeypatch)

    gen.generate_with_llm(ProactiveType.MISS_YOU, character_id="", **_gen_kwargs())
    assert len(cap.prompts) == 1
    p = cap.prompts[0]
    assert "你想主动给用户发一条消息" in p, "空人设走中性兜底开场"
    assert "作为\"十四\"" not in p, "空人设不得冒充十四（旧兜底开场白已改）"
    assert "你是十四" not in p
    assert "你现在是十四" not in p


def test_prompt_carries_immersive_constraints(monkeypatch):
    """所有分支注入沉浸式/小说式模式约束段（禁客服腔——两种模式共有）。"""
    cap = _Capture()
    gen = _generator(cap, monkeypatch)

    # 角色驱动主路径（有卡用卡；无卡环境退空 id 走中性分支，两分支都必须带约束）
    cards = _two_real_cards()
    cid = str(cards[0]["id"]) if cards else ""
    gen.generate_with_llm(ProactiveType.MISS_YOU, character_id=cid, **_gen_kwargs())
    gen.generate_with_llm(ProactiveType.MISS_YOU, character_id="", **_gen_kwargs())

    assert len(cap.prompts) == 2
    for p in cap.prompts:
        assert "客服腔" in p, "prompt 必须含禁客服腔模式约束"
        assert ("物理空间" in p) or ("动作" in p), "非共处/动作约束语义必须在场"


def test_grounded_state_survives_character_path(monkeypatch):
    """角色化主路径不得丢接地材料：真实小时数/用户最后一句仍在 prompt。"""
    cap = _Capture()
    gen = _generator(cap, monkeypatch)
    cards = _two_real_cards()
    if not cards:
        pytest.skip("config/characters 无可用卡")
    gen.generate_with_llm(
        ProactiveType.MISS_YOU,
        character_id=str(cards[0]["id"]),
        hours_since_chat=5.0,
        user_profile="生日：腊月初一",
        last_user_message="明天要军训",
        emotion_state={"primary": {"type": "想念"}},
        affinity_level=3,
    )
    p = cap.prompts[0]
    assert "距上次聊天：5.0 小时" in p
    assert "明天要军训" in p
    assert "生日：腊月初一" in p
