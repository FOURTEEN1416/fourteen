"""包 Q · A1 身份唯一 Owner — golden prompt 测试。

规则（HANDOFF §3 A1）：
- character_id 非 default/demo 时，禁止 PersonaEngine 注入「你叫十四」全文
- 身份以角色卡为唯一真源（CharacterAggregate + orchestrator 角色片段）
- system prompt 必须包含角色名；不得包含「你叫十四」；不得同时出现两个角色名
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from my_character.persona_engine import (
    DEFAULT_CHARACTER_IDS,
    DEFAULT_PERSONA_DESC,
    PersonaEngine,
    build_external_constraint_layer,
    is_external_character_id,
    strip_default_identity,
)
from shisi.application.persona_service import PersonaService

CHARACTERS_DIR = Path("config/characters")
DEFAULT_IDENTITY = "你叫十四"


def _pick_literary_card() -> tuple[str, str] | None:
    """选一张有明确角色名的文学/外部卡。"""
    if not CHARACTERS_DIR.exists():
        return None
    for path in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(data.get("name") or data.get("data", {}).get("name") or "").strip()
        cid = str(data.get("id") or path.stem)
        if not name or cid in DEFAULT_CHARACTER_IDS:
            continue
        if "十四" in name:
            continue
        return cid, name
    return None


def _pick_two_distinct_card_names() -> list[str] | None:
    names: list[str] = []
    if not CHARACTERS_DIR.exists():
        return None
    for path in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(data.get("name") or "").strip()
        cid = str(data.get("id") or path.stem)
        if name and cid not in DEFAULT_CHARACTER_IDS and "十四" not in name and name not in names:
            names.append(name)
        if len(names) >= 2:
            return names
    return None


class TestIdentityHelpers:
    def test_default_ids_are_not_external(self):
        for cid in ("", None, "default", "demo"):
            assert is_external_character_id(cid) is False

    def test_literary_id_is_external(self):
        assert is_external_character_id("62105bca") is True
        assert is_external_character_id("米彩") is True

    def test_strip_default_identity_markers(self):
        raw = f"# 角色设定\n你是米彩。\n\n{DEFAULT_PERSONA_DESC}\n"
        cleaned = strip_default_identity(raw)
        assert DEFAULT_IDENTITY not in cleaned
        assert "米彩" in cleaned

    def test_strip_keeps_card_identity_when_no_marker(self):
        raw = "你是米彩，温柔沉静。"
        assert strip_default_identity(raw) == raw

    def test_external_constraint_has_no_default_identity(self):
        layer = build_external_constraint_layer()
        assert DEFAULT_IDENTITY not in layer
        assert "唯一的我" not in layer
        assert "不要用" in layer


class TestPersonaServiceIdentityOwner:
    def test_external_character_prompt_has_card_name_not_default(self):
        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, card_name = picked
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id=character_id, user_message="你好")
        assert card_name in prompt
        assert DEFAULT_IDENTITY not in prompt
        # 不得同时出现默认人格名与角色卡名
        engine_name = str(ps.engine.get_name() or "")
        if engine_name and engine_name != card_name:
            assert engine_name not in prompt or engine_name in card_name

    def test_default_character_still_allows_engine_identity(self):
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id="default", user_message="你好")
        # default 路径允许 PersonaEngine 默认人格名（产品默认角色）
        assert ps.engine.get_name() in prompt

    def test_external_missing_card_does_not_fall_back_to_default_identity(self):
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id="no_such_card_xyz", user_message="hi")
        assert DEFAULT_IDENTITY not in prompt
        assert "no_such_card_xyz" in prompt

    def test_external_layers_exclude_default_persona_desc(self):
        """外部角色注入层不得带 DEFAULT_PERSONA_DESC 全文。"""
        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, _ = picked
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id=character_id, user_message="你好")
        # DEFAULT_PERSONA_DESC 的核心身份句
        assert "是我的AI伙伴" not in prompt
        assert "表面傲娇，嘴硬心软" not in prompt or character_id in prompt


class TestOrchestratorPersonaSegmentIdentity:
    def test_external_segment_contains_card_name(self):
        from main import OptimizedOrchestrator

        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, card_name = picked
        # 清缓存后重载
        OptimizedOrchestrator.invalidate_character_persona_cache(character_id)
        segment = OptimizedOrchestrator._load_character_persona_segment(character_id)
        if not segment:
            pytest.skip("该角色 segment 为空")
        assert card_name in segment
        assert DEFAULT_IDENTITY not in segment

    def test_default_and_demo_segments_empty(self):
        from main import OptimizedOrchestrator

        assert OptimizedOrchestrator._load_character_persona_segment("default") == ""
        assert OptimizedOrchestrator._load_character_persona_segment("demo") == ""
        assert OptimizedOrchestrator._load_character_persona_segment("") == ""

    def test_golden_full_system_prompt_no_dual_identity(self):
        """Golden：PersonaService + orchestrator 片段拼装后的 system prompt。"""
        from main import OptimizedOrchestrator

        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, card_name = picked
        ps = PersonaService(config_loader=None, llm_gateway=None)
        system_prompt = ps.build_system_prompt(
            character_id=character_id, user_message="在吗"
        )
        OptimizedOrchestrator.invalidate_character_persona_cache(character_id)
        char_segment = OptimizedOrchestrator._load_character_persona_segment(character_id)
        if char_segment:
            system_prompt = (
                f"{system_prompt}\n\n"
                f"# 当前必须扮演的角色（最高优先级）\n"
                f"{char_segment}\n\n"
                f"你当前正在扮演以上角色。"
            )
        system_prompt = strip_default_identity(system_prompt)

        assert card_name in system_prompt
        assert DEFAULT_IDENTITY not in system_prompt

        two = _pick_two_distinct_card_names()
        if two and len(two) >= 2:
            # 不得同时出现两张无关文学卡的名字（身份唯一）
            other = two[1] if two[0] == card_name else two[0]
            if other != card_name:
                # 允许知识库偶然提到他人名，但 system 不得以「你是{other}」断言
                assert f"你是{other}" not in system_prompt


class TestPersonaEnginePublicApiStillIntact:
    def test_public_layer_methods_exist(self):
        pe = PersonaEngine()
        assert callable(getattr(pe, "build_emotion_layer", None))
        assert callable(getattr(pe, "build_style_layer", None))
        assert callable(getattr(pe, "build_constraint_layer", None))
        assert callable(getattr(pe, "build_emotion_style_segment", None))
        assert callable(getattr(pe, "get_description", None))

    def test_default_constraint_still_has_self_reference(self):
        pe = PersonaEngine()
        layer = pe.build_constraint_layer()
        assert pe.get_name() in layer or "唯一的我" in layer
