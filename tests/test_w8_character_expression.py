"""W8 · 角色表达域回归（缺陷 A / C）。

全部使用**合成角色卡**与临时数据根，不读 config/characters 真实卡正文。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from utils.character_helpers import normalize_character_card

MARK_FLAT = "特征标记甲：从不主动道晚安"
MARK_NESTED = "特征标记乙：只在被问时才说地址"

FLAT_CARD: dict[str, Any] = {
    "id": "w8flat",
    "name": "测试甲",
    "description": "她是测试甲。",
    "personality": {"warmth": 0.7, "stubbornness": 0.0, "custom_axis": 0.42},
    "personality_text": MARK_FLAT,
    "speaking_style": {"emoji_freq": 0.0, "humor": 1.0, "sentence_length": 0.1, "formality": 0.9},
    "core_anchors": ["锚点一"],
    "mes_example": "<START>\n用户：早\n测试甲：早。",
}

NESTED_CARD: dict[str, Any] = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {
        "name": "测试乙",
        "description": "她是测试乙。",
        "personality_text": MARK_NESTED,
        "personality": {"warmth": 0.3},
        "speaking_style": {"emoji_freq": 0.2},
        "creator_notes": "只回一句。",
        "mes_example": "<START>\n用户：你好\n测试乙：嗯。",
    },
}


@pytest.fixture(autouse=True)
def _isolate_knowledge_index_dir(tmp_path, monkeypatch) -> None:
    """知识索引目录指向临时根 —— 禁止合成卡写进真实 `data/knowledge`。

    2026-09-27 本窗实测自伤：`build_system_prompt` 会经 prompt_builder 触发
    `get_knowledge_service()` 按 `_DEFAULT_INDEX_DIR`（= 项目根 `data/knowledge`）
    落盘，合成卡 `w8flat/w8long/w8n/w8short` 的索引与源存储就此进了开发机真实
    知识真源（gitignored，git 发现不了）。
    """
    from shisi.knowledge import character_knowledge_service as ks

    monkeypatch.setattr(ks, "_DEFAULT_INDEX_DIR", tmp_path / "knowledge")
    # 单例可能在同进程内已被别有用例以真实目录建好 —— 一并重置，否则重定向不生效
    monkeypatch.setattr(ks, "_knowledge_service", None)


# ── 缺陷 A：归一化必须无损且幂等 ──────────────────────────────


def test_normalize_keeps_personality_text_alongside_numeric_dict() -> None:
    """数值 personality 与长文本 personality_text 并存时，两者都不得被删除。

    现役 41 卡中 40 卡同时具备两者，旧实现在数值分支把文本置空并 pop，
    导致固定性格段在进入 prompt 前被删除。
    """
    once = normalize_character_card(copy.deepcopy(FLAT_CARD))
    assert once.get("personality_text") == MARK_FLAT
    assert once["personality"]["warmth"] == pytest.approx(0.7)
    assert once["personality"]["stubbornness"] == pytest.approx(0.0)
    assert once["personality"]["custom_axis"] == pytest.approx(0.42)

    twice = normalize_character_card(copy.deepcopy(once))
    assert twice == once, "归一化必须幂等"


def test_normalize_preserves_zero_valued_style_and_text() -> None:
    """0 与 0.0 是有效设定，不得被当作假值丢弃。"""
    normalized = normalize_character_card(copy.deepcopy(FLAT_CARD))
    assert normalized["speaking_style"]["emoji_freq"] == pytest.approx(0.0)
    assert normalized["speaking_style"]["sentence_length"] == pytest.approx(0.1)
    assert normalized["personality_text"]


def test_normalize_nested_flattens_text_and_examples() -> None:
    """嵌套（chara_card_v2）卡的文本与示例必须展平到顶层规范位。"""
    once = normalize_character_card(copy.deepcopy(NESTED_CARD))
    assert once.get("personality_text") == MARK_NESTED
    assert once.get("mes_example"), "mes_example 需按白名单展平，供固定示例位使用"
    assert "嗯。" in str(once["mes_example"])
    assert once["speaking_style"]["emoji_freq"] == pytest.approx(0.2)
    assert normalize_character_card(copy.deepcopy(once)) == once


def test_nested_and_flat_conflict_prefers_flat_edit() -> None:
    """编辑路径写顶层扁平字段：顶层与残留嵌套块冲突时以扁平为准。"""
    card = copy.deepcopy(NESTED_CARD)
    card["personality_text"] = "扁平编辑后的性格"
    card["description"] = "扁平编辑后的描述。"
    normalized = normalize_character_card(card)
    assert normalized["personality_text"] == "扁平编辑后的性格"
    assert normalized["description"] == "扁平编辑后的描述。"
    # 嵌套冲突必须被同步（否则下一次读取又回退到旧值 = 编辑丢失）
    nested = normalized.get("data") or {}
    assert nested.get("personality_text") == "扁平编辑后的性格"
    assert normalize_character_card(copy.deepcopy(normalized))["personality_text"] == "扁平编辑后的性格"


# ── 缺陷 C：0 值变默认、维度未渲染、base_style 未传 ──────────

EXPECTED_PROMPT_LINES = ("- 固执度:", "- 幽默感:", "- 句长:")


def _persona_service_for(tmp_path: Path, cards: dict[str, dict[str, Any]], monkeypatch):
    """把 PersonaService 的角色卡目录指向临时根（合成卡，不触真实卡）。"""
    from shisi.application import persona_service as ps_module

    chars_dir = tmp_path / "config" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    for cid, payload in cards.items():
        (chars_dir / f"{cid}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    root = tmp_path

    def _fake_project_path(*parts: str) -> Path:
        return root.joinpath(*parts)

    monkeypatch.setattr(ps_module, "project_path", _fake_project_path)
    return ps_module.PersonaService(config_loader=None, llm_gateway=None)


def test_prompt_keeps_personality_text_through_cold_load(tmp_path, monkeypatch) -> None:
    """冷加载（真实归一化路径）后，固定性格段必须仍在 prompt 中。"""
    ps = _persona_service_for(tmp_path, {"w8flat": copy.deepcopy(FLAT_CARD)}, monkeypatch)
    prompt = ps.build_system_prompt(character_id="w8flat", user_message="早")
    assert MARK_FLAT in prompt, "归一化删除了现役性格长文本"


def test_prompt_renders_all_configured_dimensions(tmp_path, monkeypatch) -> None:
    """stubbornness/humor/sentence_length 必须在人设数值段可见，且 0 不得变默认。"""
    ps = _persona_service_for(tmp_path, {"w8flat": copy.deepcopy(FLAT_CARD)}, monkeypatch)
    prompt = ps.build_system_prompt(character_id="w8flat", user_message="早")
    for line in EXPECTED_PROMPT_LINES:
        assert line in prompt, f"未渲染维度：{line}"
    assert "- 固执度: 0.0" in prompt, "0 值被默认值吞掉"
    assert "- 幽默感: 1.0" in prompt
    assert "不使用表情符号" in prompt, "emoji_freq=0 应渲染为不使用的指令"


def test_zero_traits_survive_mapping() -> None:
    """映射层必须区分「显式 0」与「缺失」。"""
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    persona = ps._map_persona_from_traits(
        {"warmth": 0.0, "jealousy": 0.01, "stubbornness": 1},
        style={"emoji_freq": 0.0, "humor": 0.0, "sentence_length": 1.0},
    )
    assert persona.warmth == pytest.approx(0.0)
    assert persona.jealousy == pytest.approx(0.01)
    assert persona.stubbornness == pytest.approx(1.0)
    assert persona.emoji_frequency == pytest.approx(0.0)
    assert persona.humor == pytest.approx(0.0)
    assert persona.sentence_length == pytest.approx(1.0)


def test_missing_trait_still_uses_documented_default() -> None:
    """缺失键走默认值（与显式 0 不同路径）。"""
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    persona = ps._map_persona_from_traits({}, style={})
    assert persona.warmth == pytest.approx(0.7)
    assert persona.emoji_frequency == pytest.approx(0.6)


def test_coupler_receives_card_base_style(tmp_path, monkeypatch) -> None:
    """情绪-风格耦合器必须以卡设为 base_style 起点，而非硬编码缺省。"""
    from my_character import consistency_checker

    captured: dict[str, Any] = {}
    real = consistency_checker.couple_style_for

    def _record(emotion_state, coupler=None, base_style=None):
        captured["base_style"] = base_style
        return real(emotion_state, coupler, base_style)

    monkeypatch.setattr(consistency_checker, "couple_style_for", _record)
    ps = _persona_service_for(tmp_path, {"w8flat": copy.deepcopy(FLAT_CARD)}, monkeypatch)
    prompt = ps.build_system_prompt(character_id="w8flat", user_message="早")
    base = captured.get("base_style")
    assert isinstance(base, dict) and base, "coupler 未收到卡设基准"
    assert base["warmth"] == pytest.approx(0.7)
    assert base["formality"] == pytest.approx(0.9)
    assert base["emoji_freq"] == pytest.approx(0.0)
    assert base["sentence_length"] == "short", "0.1 句长应映射为 short 档位"
    assert "[当前风格指导]" in prompt


def _style_card(sentence_length: float) -> dict[str, Any]:
    card = copy.deepcopy(FLAT_CARD)
    card["speaking_style"] = dict(card["speaking_style"], sentence_length=sentence_length)
    return card


def test_card_sentence_length_survives_neutral_emotion(tmp_path, monkeypatch) -> None:
    """卡设句长档位不得被情绪矩阵的「medium（无偏移）」抹平。

    验收口径：改变句长滑杆必须改变当轮指导输出。情绪矩阵里的 medium 语义是
    「该情绪不推拉句长」，因此保留卡设基线；只有 short/long 才当轮覆盖。
    """
    long_ps = _persona_service_for(tmp_path, {"w8long": _style_card(0.9)}, monkeypatch)
    short_ps = _persona_service_for(tmp_path, {"w8short": _style_card(0.1)}, monkeypatch)
    emotion = {"primary": {"type": "平常"}, "affinity": 3}

    long_prompt = long_ps.build_system_prompt(
        emotion_state=emotion, character_id="w8long", user_message="早"
    )
    short_prompt = short_ps.build_system_prompt(
        emotion_state=emotion, character_id="w8short", user_message="早"
    )
    assert "[当前风格指导]" in long_prompt
    assert "可以把一句话说完整" in long_prompt, "句长=长 未体现在当轮指导"
    assert "回复简短有力" in short_prompt, "句长=短 未体现在当轮指导"
    assert "可以把一句话说完整" not in short_prompt


def test_emotion_forces_short_sentence_over_long_card_baseline(tmp_path, monkeypatch) -> None:
    """情绪明确偏移（生气=short）时覆盖卡设基线 —— 卡设与情绪覆盖的优先级。"""
    ps = _persona_service_for(tmp_path, {"w8long": _style_card(0.9)}, monkeypatch)
    prompt = ps.build_system_prompt(
        emotion_state={"primary": {"type": "生气"}, "affinity": 3},
        character_id="w8long",
        user_message="早",
    )
    assert "回复简短有力" in prompt
    assert "可以把一句话说完整" not in prompt


def test_import_edit_save_restart_roundtrip(tmp_path, monkeypatch) -> None:
    """导入→编辑→保存→重启→prompt 往返一致，示例稳定进固定示例位。"""
    ps1 = _persona_service_for(tmp_path, {"w8n": copy.deepcopy(NESTED_CARD)}, monkeypatch)
    before = ps1.build_system_prompt(character_id="w8n", user_message="你好")
    assert MARK_NESTED in before
    assert "# 对话示例" in before
    assert "用户：你好" in before
    assert "仅示范语气与格式" in before

    # 编辑（PUT /characters/{id}/persona 语义：合并顶层扁平字段）后落盘
    path = tmp_path / "config" / "characters" / "w8n.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["personality"] = {"warmth": 0.3, "stubbornness": 0.9}
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    # 重启：新建服务实例读同一磁盘文件
    ps2 = _persona_service_for(tmp_path, {}, monkeypatch)
    after = ps2.build_system_prompt(character_id="w8n", user_message="你好")
    assert "- 固执度: 0.9" in after, "编辑后重启丢失"
    assert MARK_NESTED in after
    assert "# 对话示例" in after
    assert after.count("# 对话示例") == 1


def test_persona_service_cache_roundtrip_is_stable(tmp_path, monkeypatch) -> None:
    """同一磁盘卡在缓存命中/未命中两条路径下产出的 prompt 必须一致。"""
    ps = _persona_service_for(tmp_path, {"w8flat": copy.deepcopy(FLAT_CARD)}, monkeypatch)
    first = ps.build_system_prompt(character_id="w8flat", user_message="早")
    ps._card_cache.clear()
    second = ps.build_system_prompt(character_id="w8flat", user_message="早")
    assert first == second


# ── 真实 41 卡的**结构性**核查（只断言有无与长度，不输出卡正文）──


def test_all_shipped_cards_are_lossless_and_idempotent() -> None:
    """磁盘上每张现役卡：显式文本/数值/示例在归一化后必须都还在，且二次归一化不变。

    只读结构与长度，断言消息不含卡正文（41 卡为 gitignored 私有资产）。
    """
    from utils.character_helpers import normalize_character_card as norm

    chars_dir = Path("config/characters")
    if not chars_dir.exists():
        pytest.skip("角色卡目录不在位（config/characters 被 gitignore，需单独投递）")
    paths = sorted(chars_dir.glob("*.json"))
    if not paths:
        # 公开 clone / CI checkout 后目录存在但无卡（gitignore 不投递内容）——缺件即 skip，
        # 不进断言（曾致 GitHub CI backend 岗红，run 36308183821）。
        pytest.skip("角色卡目录存在但无卡文件（CI/公开 clone 不投递 gitignored 卡）")

    text_kept = 0
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        once = norm(copy.deepcopy(raw))
        twice = norm(copy.deepcopy(once))
        assert twice == once, f"{path.name}: 归一化不幂等"
        for field in ("personality_text", "speaking_style_text", "mes_example",
                      "creator_notes", "first_mes", "scenario"):
            original = raw.get(field) if isinstance(raw.get(field), str) else None
            if original and original.strip():
                assert once.get(field), f"{path.name}: 显式 {field} 被归一化删除"
                text_kept += 1
        assert isinstance(once.get("personality"), dict)
        assert isinstance(once.get("speaking_style"), dict)
        for key, value in (raw.get("personality") or {}).items():
            if isinstance(value, (int, float)):
                assert once["personality"].get(key) == value, f"{path.name}: 数值维度 {key} 丢失"

    # 现役 41 卡中 40 卡带显式性格长文本 —— 回归到 0 说明删除缺陷复发
    assert text_kept >= 40, f"显式文本字段保留数异常偏低：{text_kept}"
