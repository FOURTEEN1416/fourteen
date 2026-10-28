"""CSP 行为蒸馏移植批 · 行为规则段与写卡路径回归（2026-10-28）。

全部使用**合成角色卡**与临时目录，不读 `config/characters` 真实卡、不写真实 `data/`。

三组契约：
1. **渲染**：`# 行为规则` 段位于 `# 性格` 之后 / `# 开场情境` 之前，边界行前置且豁免，
   整段 ≤ 400 字符，满长 7 条不被淘汰，段头中性（无性别代词、无第二人称称谓）。
2. **形态**：字符串条目按「→」切分、dict 条目只取 trigger/behavior/why 三键、
   上限 7 条、单字段截断、非字符串 honesty 不得 repr 进 prompt、空值不落键。
3. **写卡路径**：生成 prompt 声明同一套上限（卡上看到的 = 模型看到的）、preview 与
   落盘同形、PUT 合并保留字段、普通建卡不凭空多键。

⚠️ 本批**不**覆盖主动线：`proactive/proactive_context.py` 与
`proactive/llm_proactive.py` 不走 `CharacterAggregate.build_system_prompt`，
honesty_boundary 对主动消息零生效（已在批注中登记为下一批范围）。
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from shisi.core.models.character_aggregate import CharacterAggregate
from utils.character_helpers import (
    BEHAVIOR_SECTION_GUARDRAIL,
    BEHAVIOR_SECTION_HEADER,
    BEHAVIOR_SECTION_MAX_CHARS,
    HONESTY_BOUNDARY_MAX_CHARS,
    MAX_BEHAVIOR_PATTERNS,
    MAX_BEHAVIOR_TEXT_CHARS,
    MAX_BEHAVIOR_TRIGGER_CHARS,
    MAX_BEHAVIOR_WHY_CHARS,
    normalize_behavior_patterns,
    normalize_character_card,
    render_behavior_section,
    sanitize_honesty_boundary,
)

MARK_A = "专属触发点甲"
MARK_B = "专属做法乙"
MARK_C = "专属动机丙"

PATTERNS_SAMPLE: list[dict[str, str]] = [
    {"trigger": "被夸", "behavior": "嘴上否认，其实开心", "why": "不擅长承认"},
    {"trigger": "被追问私事", "behavior": "先岔开话题", "why": "不想被看穿"},
    {"trigger": "被要求做不想做的事", "behavior": "直说不行", "why": "讨厌勉强"},
]


@pytest.fixture(autouse=True)
def _isolate_knowledge_index_dir(tmp_path, monkeypatch) -> None:
    """知识索引目录指向临时根 —— 合成卡索引禁止落进真实 `data/knowledge`。

    同 `tests/test_w8_character_expression.py` 的实证教训：`build_system_prompt`
    经 prompt_builder 会触发知识服务按默认目录落盘（gitignored，git 发现不了）。
    """
    from shisi.knowledge import character_knowledge_service as ks

    monkeypatch.setattr(ks, "_DEFAULT_INDEX_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(ks, "_knowledge_service", None)


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


@pytest.fixture
def char_sandbox(tmp_path, monkeypatch):
    """路由级沙箱：临时卡目录 + 写路径副作用钩子全部打桩。"""
    from api.routers import character_routes as cr

    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()
    monkeypatch.setattr(cr, "CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr(cr, "MEMORY_FACTS_DIR", tmp_path / "character_memory")
    monkeypatch.setattr(cr, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(cr, "_schedule_character_crawl", lambda *a, **k: None)
    monkeypatch.setattr(cr, "_invalidate_knowledge_index", lambda *a, **k: None)
    # BYOK 闸归 F2 专项（tests/test_sec_p0_character_card.py）；此处不重复消费
    monkeypatch.setattr(cr, "_enforce_byok_for_generation", lambda *a, **k: None)
    return chars_dir


# ── 1. 生成 prompt 与渲染共用同一套上限 ─────────────────────────


def test_generation_prompt_declares_caps_and_schema() -> None:
    """写卡侧 prompt 必须声明与渲染侧相同的上限（否则「卡上看到的 ≠ 模型看到的」）。"""
    from api.routers import character_routes as cr

    prompt = cr._character_generation_prompt("猫系少女", "温柔")

    assert "behavior_patterns" in prompt
    assert "honesty_boundary" in prompt
    assert f"trigger ≤{MAX_BEHAVIOR_TRIGGER_CHARS} 字" in prompt
    assert f"behavior ≤{MAX_BEHAVIOR_TEXT_CHARS} 字" in prompt
    assert f"why ≤{MAX_BEHAVIOR_WHY_CHARS} 字" in prompt
    assert f"honesty_boundary ≤{HONESTY_BOUNDARY_MAX_CHARS} 字" in prompt
    assert "3–7 条" in prompt
    assert "禁止括号、星号与舞台指示" in prompt

    # max_tokens 1024 → 1536：7 条三段式 JSON 在 1024 下会被截断
    source = inspect.getsource(cr._generate_persona_preview)
    assert "max_tokens=1536" in source
    assert "max_tokens=1024" not in source


# ── 2. 落盘构造规范化 ───────────────────────────────────────


def test_build_character_data_normalizes_fields() -> None:
    from api.routers import character_routes as cr

    data = cr._build_character_data(
        name="测试甲",
        behavior_patterns="被夸 -> 嘴上否认（害羞）",
        honesty_boundary=["不知道天气", "不编造行程"],
    )
    assert data["behavior_patterns"] == [
        {"trigger": "被夸", "behavior": "嘴上否认（害羞）", "why": ""}
    ]
    assert data["honesty_boundary"] == "不知道天气；不编造行程"


# ── 3/4. 归一化无损且幂等（扁平 / 嵌套）───────────────────────


def test_flat_card_normalize_is_idempotent() -> None:
    card = {
        "id": "bd-flat",
        "name": "测试甲",
        "description": "她是测试甲。",
        "behavior_patterns": "被夸 -> 嘴上否认（害羞）",
        "honesty_boundary": ["不知道就不编"],
    }
    once = normalize_character_card(copy.deepcopy(card))
    assert once["behavior_patterns"] == [
        {"trigger": "被夸", "behavior": "嘴上否认（害羞）", "why": ""}
    ]
    assert once["honesty_boundary"] == "不知道就不编"
    assert normalize_character_card(copy.deepcopy(once)) == once, "归一化必须幂等"


def test_nested_card_flatten_and_write_back() -> None:
    card = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": "测试乙",
            "description": "她是测试乙。",
            "behavior_patterns": [
                {"trigger": "被追问", "behavior": "岔开话题", "why": "避讳"}
            ],
            "honesty_boundary": "不知道的事不编造",
        },
    }
    once = normalize_character_card(copy.deepcopy(card))
    assert once["behavior_patterns"] == [
        {"trigger": "被追问", "behavior": "岔开话题", "why": "避讳"}
    ]
    assert once["honesty_boundary"] == "不知道的事不编造"
    # 嵌套块同步为同一份规范值（否则下次阅读回退旧值 = 编辑静默丢失）
    nested = once["data"]
    assert nested["behavior_patterns"] == once["behavior_patterns"]
    assert nested["honesty_boundary"] == once["honesty_boundary"]
    assert normalize_character_card(copy.deepcopy(once)) == once


# ── 5. 字符串 / 超条数 / 超长 / 脚本残片容错 ─────────────────


def test_string_and_overflow_values_are_tolerated() -> None:
    raw: list[Any] = [
        "被夸 -> 嘴上否认",
        {"trigger": "x" * 40, "behavior": "<script>alert(1)</script>说没事", "why": "y" * 30},
        "javascript:alert(1)",
        42,
        {"trigger": "只有触发没有做法", "behavior": ""},  # behavior 必需 → 丢
        "当" * 50 + " → " + "做" * 60,
        *[f"场景{i} → 做法{i}" for i in range(5)],
    ]
    out = normalize_behavior_patterns(raw)

    assert len(out) <= MAX_BEHAVIOR_PATTERNS
    assert out, "合法条目不得被整体丢弃"
    for pattern in out:
        assert set(pattern) == {"trigger", "behavior", "why"}
        assert pattern["behavior"], "behavior 为必需项"
        assert len(pattern["trigger"]) <= MAX_BEHAVIOR_TRIGGER_CHARS
        assert len(pattern["behavior"]) <= MAX_BEHAVIOR_TEXT_CHARS
        assert len(pattern["why"]) <= MAX_BEHAVIOR_WHY_CHARS
        assert "<script" not in pattern["behavior"]
        assert "javascript:" not in pattern["behavior"]
    # 超条数：9 条输入只保留前 7 条
    assert len(normalize_behavior_patterns([f"a{i} → b{i}" for i in range(9)])) == (
        MAX_BEHAVIOR_PATTERNS
    )
    # 无分隔符 → 整串当 behavior
    assert normalize_behavior_patterns("只说这一句")[0]["behavior"] == "只说这一句"


# ── 6. 空值不新增键 ─────────────────────────────────────────


def test_empty_fields_add_no_keys() -> None:
    plain = normalize_character_card({"name": "测试丙", "description": "她是测试丙。"})
    assert "behavior_patterns" not in plain
    assert "honesty_boundary" not in plain

    explicit_empty = normalize_character_card(
        {"name": "测试丁", "behavior_patterns": [], "honesty_boundary": ""}
    )
    assert "behavior_patterns" not in explicit_empty
    assert "honesty_boundary" not in explicit_empty


# ── 7/8/9. 段落位置、整段省略、仅边界 ───────────────────────


def test_section_sits_after_personality_and_boundary_first() -> None:
    char = CharacterAggregate(
        name="测试甲",
        description="她是测试甲。",
        personality_text="性格文本甲",
        behavior_patterns=PATTERNS_SAMPLE,
        honesty_boundary="不知道就不编",
        scenario="开场情境甲",
    )
    prompt = char.build_system_prompt("你好", "历史")
    lines = prompt.splitlines()

    idx_persona = lines.index("# 性格")
    idx_behavior = next(i for i, line in enumerate(lines) if line.startswith("# 行为规则"))
    idx_scenario = next(i for i, line in enumerate(lines) if line.startswith("# 开场情境"))

    assert idx_persona < idx_behavior < idx_scenario
    assert prompt.count("# 行为规则") == 1, "行为段不得重复注入"
    # 边界行前置且豁免：排在全部条目之前
    assert prompt.index("边界：不知道就不编") < prompt.index("- 被夸")
    # 条目按 trigger → behavior（why）渲染
    assert "- 被夸 → 嘴上否认，其实开心（不擅长承认）" in prompt
    assert BEHAVIOR_SECTION_GUARDRAIL in prompt


def test_section_omitted_when_both_fields_empty() -> None:
    char = CharacterAggregate(name="测试乙", description="她是测试乙。")
    prompt = char.build_system_prompt("你好")
    assert "# 行为规则" not in prompt
    assert BEHAVIOR_SECTION_HEADER not in prompt
    assert render_behavior_section([], "") == ""
    assert render_behavior_section(None, None) == ""


def test_boundary_only_still_renders_section() -> None:
    char = CharacterAggregate(name="测试丙", description="她是测试丙。", honesty_boundary="不知道就不编")
    prompt = char.build_system_prompt()
    assert "# 行为规则" in prompt
    assert "边界：不知道就不编" in prompt
    section = render_behavior_section([], "不知道就不编")
    assert section.splitlines() == [
        BEHAVIOR_SECTION_HEADER,
        BEHAVIOR_SECTION_GUARDRAIL,
        "边界：不知道就不编",
    ]


# ── 10. 跨卡不得串味 ───────────────────────────────────────


def test_card_markers_do_not_leak_into_other_card_prompt() -> None:
    card_a = CharacterAggregate(
        name="甲卡",
        description="她是甲卡。",
        catchphrases=["甲卡口头禅"],
        behavior_patterns=[{"trigger": MARK_A, "behavior": MARK_B, "why": MARK_C}],
        honesty_boundary="甲卡边界：不知道就不编",
    )
    card_b = CharacterAggregate(name="乙卡", description="她是乙卡。")
    prompt_a = card_a.build_system_prompt()
    prompt_b = card_b.build_system_prompt()

    for marker in (MARK_A, MARK_B, MARK_C, "甲卡口头禅", "甲卡边界"):
        assert marker in prompt_a
        assert marker not in prompt_b


# ── 11. PersonaService 冷加载把字段映射进聚合根 ──────────────


def test_persona_service_cold_load_maps_distilled_fields(tmp_path, monkeypatch) -> None:
    card = {
        "id": "bd-cold",
        "name": "冷加载卡",
        "description": "她是冷加载卡。",
        "behavior_patterns": [
            {"trigger": "被夸", "behavior": "嘴上否认", "why": "害羞"}
        ],
        "honesty_boundary": "不知道就不编",
    }
    ps = _persona_service_for(tmp_path, {"bd-cold": card}, monkeypatch)

    character = ps._build_character(ps._engine.emotion.state, character_id="bd-cold")
    assert character.behavior_patterns == [
        {"trigger": "被夸", "behavior": "嘴上否认", "why": "害羞"}
    ]
    assert character.honesty_boundary == "不知道就不编"

    prompt = ps.build_system_prompt(character_id="bd-cold", user_message="早")
    assert "- 被夸 → 嘴上否认（害羞）" in prompt
    assert "边界：不知道就不编" in prompt


# ── 12. 行为段在 topic_state untrusted 信封之前 ───────────────


def test_section_precedes_topic_state_envelope(tmp_path, monkeypatch) -> None:
    card = {
        "id": "bd-topic",
        "name": "话题卡",
        "description": "她是话题卡。",
        "behavior_patterns": [
            {"trigger": "被夸", "behavior": "嘴上否认", "why": "害羞"}
        ],
        "honesty_boundary": "不知道就不编",
    }
    ps = _persona_service_for(tmp_path, {"bd-topic": card}, monkeypatch)
    prompt = ps.build_system_prompt(
        character_id="bd-topic",
        user_message="早",
        memory_context={"topic_state_text": "话题状态：住在通勤城市"},
    )

    envelope_open = '<context trust="untrusted" source="topic_state">'
    assert envelope_open in prompt, "topic_state 信封缺失（前置条件不成立）"
    assert prompt.count(envelope_open) == 1
    env_start = prompt.index(envelope_open)
    env_end = prompt.index("</context>", env_start)
    assert env_end > env_start, "信封必须闭合"
    assert prompt.index("# 行为规则") < env_start
    assert prompt.index("- 被夸 → 嘴上否认（害羞）") < env_start
    # 行为文本不得落进信封区间（信封里只有动态话题状态）
    assert "# 行为规则" not in prompt[env_start:env_end]


# ── 13. generate 端点落盘含两字段 ───────────────────────────


def _fake_preview_payload() -> dict[str, Any]:
    return {
        "name": "蒸馏卡",
        "description": "她是蒸馏卡。",
        "behavior_patterns": [f"场景{i} → 做法{i}" for i in range(9)] + ["字符串形态条目"],
        "honesty_boundary": ["不知道天气", "不编造行程"],
        "catchphrases": ["嗯"],
    }


def _patch_fake_preview(monkeypatch) -> None:
    from api.routers import character_routes as cr

    async def _fake(_req):
        return _fake_preview_payload()

    monkeypatch.setattr(cr, "_generate_persona_preview", _fake)


def test_generate_endpoint_persists_distilled_fields(char_sandbox, monkeypatch) -> None:
    from api.routers import character_routes as cr

    _patch_fake_preview(monkeypatch)
    resp = asyncio.run(
        cr.generate_character_from_description(
            cr.CharacterGenerateRequest(description="猫系少女"), _auth=True, principal=None
        )
    )
    saved = json.loads((char_sandbox / f"{resp['id']}.json").read_text(encoding="utf-8"))

    assert 1 <= len(saved["behavior_patterns"]) <= MAX_BEHAVIOR_PATTERNS
    assert saved["honesty_boundary"] == "不知道天气；不编造行程"
    assert all(set(p) == {"trigger", "behavior", "why"} for p in saved["behavior_patterns"])


# ── 14. PUT 合并保留两字段 ─────────────────────────────────


def test_put_merge_preserves_distilled_fields(char_sandbox) -> None:
    from api.routers import character_routes as cr

    card = cr._build_character_data(
        name="保留卡",
        description="她是保留卡。",
        behavior_patterns=PATTERNS_SAMPLE,
        honesty_boundary="不知道就不编",
    )
    cid = card["id"]
    (char_sandbox / f"{cid}.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8"
    )

    asyncio.run(
        cr.update_character(
            cid,
            cr.UnifiedCharacterUpdate(name="改过名的保留卡"),
            _auth=True,
            _owned=json.loads(json.dumps(card)),
        )
    )
    after = json.loads((char_sandbox / f"{cid}.json").read_text(encoding="utf-8"))

    assert after["name"] == "改过名的保留卡"
    assert after["behavior_patterns"] == card["behavior_patterns"]
    assert after["honesty_boundary"] == card["honesty_boundary"]


# ── 15. 普通建卡不凭空多键 ──────────────────────────────────


def test_plain_create_writes_no_distilled_keys(char_sandbox) -> None:
    from api.routers import character_routes as cr

    resp = asyncio.run(
        cr.create_character(cr.UnifiedCharacterCreate(name="普通卡"), _auth=True, principal=None)
    )
    saved = json.loads((char_sandbox / f"{resp['id']}.json").read_text(encoding="utf-8"))

    assert "behavior_patterns" not in saved
    assert "honesty_boundary" not in saved


# ── 16. preview 与落盘同形 ─────────────────────────────────


def test_preview_shape_matches_persisted_card(char_sandbox, monkeypatch) -> None:
    from api.routers import character_routes as cr

    _patch_fake_preview(monkeypatch)
    preview = asyncio.run(
        cr.preview_character_from_description(
            cr.CharacterGenerateRequest(description="猫系少女"), _auth=True, principal=None
        )
    )
    generated = asyncio.run(
        cr.generate_character_from_description(
            cr.CharacterGenerateRequest(description="猫系少女"), _auth=True, principal=None
        )
    )
    saved = json.loads(
        (char_sandbox / f"{generated['id']}.json").read_text(encoding="utf-8")
    )

    persona = preview["persona"]
    assert len(persona["behavior_patterns"]) <= MAX_BEHAVIOR_PATTERNS
    assert persona["behavior_patterns"] == saved["behavior_patterns"]
    assert persona["honesty_boundary"] == saved["honesty_boundary"]


# ── 17. 满长 7 条不被淘汰且整段 ≤ 400 ─────────────────────


def test_worst_case_seven_entries_within_budget() -> None:
    full = [
        {
            "trigger": "甲" * MAX_BEHAVIOR_TRIGGER_CHARS,
            "behavior": "乙" * MAX_BEHAVIOR_TEXT_CHARS,
            "why": "丙" * MAX_BEHAVIOR_WHY_CHARS,
        }
        for _ in range(MAX_BEHAVIOR_PATTERNS)
    ]
    section = render_behavior_section(full, "丁" * HONESTY_BOUNDARY_MAX_CHARS)

    assert len(section) <= BEHAVIOR_SECTION_MAX_CHARS, "整段超上限：常量被改而未重算"
    assert len(section.splitlines()) == 3 + MAX_BEHAVIOR_PATTERNS
    # 7 条全在场（不做运行时淘汰）：每条占一行
    assert section.count("甲" * MAX_BEHAVIOR_TRIGGER_CHARS) == MAX_BEHAVIOR_PATTERNS
    assert section.splitlines()[0] == BEHAVIOR_SECTION_HEADER
    assert section.splitlines()[1] == BEHAVIOR_SECTION_GUARDRAIL
    assert section.splitlines()[2].startswith("边界：")

    # 经聚合根渲染同样不超预算
    char = CharacterAggregate(name="满长卡", description="她是满长卡。",
                              behavior_patterns=full, honesty_boundary="丁" * HONESTY_BOUNDARY_MAX_CHARS)
    prompt = char.build_system_prompt()
    start = prompt.index(BEHAVIOR_SECTION_HEADER)
    assert len(prompt[start:].split("\n\n")[0]) <= BEHAVIOR_SECTION_MAX_CHARS


# ── 18. 段头无性别代词、无第二人称 ──────────────────────────


def test_section_header_has_no_gender_pronoun() -> None:
    male = CharacterAggregate(
        name="李信",
        description="23 岁男性角色。",
        behavior_patterns=[{"trigger": "被夸", "behavior": "笑一下"}],
        honesty_boundary="不知道就不编",
    )
    prompt = male.build_system_prompt()
    header_line = next(line for line in prompt.splitlines() if line.startswith("# 行为规则"))

    assert header_line == BEHAVIOR_SECTION_HEADER
    for pronoun in ("她", "他", "她/他", "你", "您"):
        assert pronoun not in BEHAVIOR_SECTION_HEADER
        assert pronoun not in BEHAVIOR_SECTION_GUARDRAIL


# ── 19. honesty 非字符串容错（不得 repr 进 prompt）───────────


def test_honesty_boundary_non_string_inputs() -> None:
    assert sanitize_honesty_boundary(["不知道天气", "不编造行程"]) == "不知道天气；不编造行程"
    assert sanitize_honesty_boundary({"a": "不编造"}) == "不编造"
    assert sanitize_honesty_boundary(None) == ""
    assert sanitize_honesty_boundary(42) == "42"

    char = CharacterAggregate(
        name="容错卡",
        description="她是容错卡。",
        honesty_boundary=["不知道天气", "不编造行程"],
        behavior_patterns=[{"trigger": "被问", "behavior": "直说不知道"}],
    )
    section = char.build_system_prompt()
    section = section[section.index(BEHAVIOR_SECTION_HEADER):].split("\n\n")[0]
    for repr_mark in ("[", "]", "'", "{", "}"):
        assert repr_mark not in section, f"prompt 里出现 repr 痕迹：{repr_mark}"

    normalized = normalize_character_card(
        {"name": "容错卡二", "honesty_boundary": ["不知道天气", "不编造行程"]}
    )
    assert normalized["honesty_boundary"] == "不知道天气；不编造行程"
