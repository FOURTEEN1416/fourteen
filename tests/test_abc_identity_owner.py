"""唯一身份路径 —— golden prompt 测试（2026-09-21 收口）。

规则：
- 身份只有一个来源：`PersonaService._resolve_character_card` 解析到的那张卡
  （内置 persona.yaml 卡 / `config/characters` 文件卡），两者**同构**。
- 不存在"先注入默认人格再剥离"的第二条路径，因此代码里不应再有
  `strip_default_identity` / 外部专属约束层 / 第二身份段。
- system prompt 必须含当前角色名；不得含另一张卡的身份散文。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from my_character.persona_engine import (
    DEFAULT_CHARACTER_IDS,
    PersonaEngine,
    is_external_character_id,
)
from shisi.application.persona_service import PersonaService

CHARACTERS_DIR = Path("config/characters")
DEFAULT_IDENTITY = "你叫十四"
# 内置十四的散文指纹（只允许出现在默认角色路径）
BUILTIN_PROSE = "表面傲娇，嘴硬心软"


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


class TestCardSelection:
    def test_default_ids_are_not_file_cards(self):
        for cid in ("", None, "default", "demo"):
            assert is_external_character_id(cid) is False

    def test_literary_id_is_file_card(self):
        assert is_external_character_id("62105bca") is True
        assert is_external_character_id("米彩") is True

    def test_default_resolves_to_builtin_card(self):
        """内置十四必须解析成**同构卡**，且带 description（旧路径缺这段）。"""
        ps = PersonaService(config_loader=None, llm_gateway=None)
        card = ps._resolve_character_card("default")
        assert isinstance(card, dict)
        assert card["name"] == ps.engine.get_name()
        assert str(card.get("description") or "").strip(), "persona.yaml 必须提供人格描述"

    def test_missing_external_card_resolves_to_none(self):
        ps = PersonaService(config_loader=None, llm_gateway=None)
        assert ps._resolve_character_card("no_such_card_xyz") is None


class TestSingleIdentityPath:
    def test_default_prompt_injects_persona_yaml_description(self):
        """根因证据：默认角色的 system 里真的有人格散文，而不只是名字+浮点数。"""
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id="default", user_message="你好")
        assert ps.engine.get_name() in prompt
        assert BUILTIN_PROSE in prompt

    def test_external_prompt_has_card_name_and_no_builtin_prose(self):
        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, card_name = picked
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id=character_id, user_message="你好")
        assert card_name in prompt
        assert DEFAULT_IDENTITY not in prompt
        assert BUILTIN_PROSE not in prompt, "内置十四的人格散文不得叠加到外部角色"

    def test_identity_asserted_once(self):
        """只有一处「你是X」身份断言 —— 双段并存才需要"以谁为准"的补丁。"""
        picked = _pick_literary_card()
        if not picked:
            pytest.skip("config/characters 无可用外部角色卡")
        character_id, card_name = picked
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id=character_id, user_message="在吗")
        assert prompt.count(f"你是{card_name}") == 1
        assert "# 当前必须扮演的角色" not in prompt

    def test_missing_card_does_not_fall_back_to_default_identity(self):
        ps = PersonaService(config_loader=None, llm_gateway=None)
        prompt = ps.build_system_prompt(character_id="no_such_card_xyz", user_message="hi")
        assert DEFAULT_IDENTITY not in prompt
        assert BUILTIN_PROSE not in prompt
        assert "no_such_card_xyz" in prompt

    def test_card_fields_reach_prompt_via_aggregate(self):
        """口头禅/开场白由聚合根注入（原 orchestrator 第二身份段已删除）。"""
        from shisi.core.models.character_aggregate import CharacterAggregate
        from shisi.core.services import prompt_builder

        agg = CharacterAggregate(
            id="cid",
            name="测试角色",
            description="描述",
            catchphrases=["哼", "才没有呢"],
            first_mes="初次见面，请多指教",
        )
        prompt = prompt_builder.build(agg, user_message="", chat_history="")
        assert "哼 / 才没有呢" in prompt
        assert "初次见面，请多指教" in prompt


class TestConstraintLayerIsIdentityNeutral:
    def test_constraint_layer_has_no_default_name(self):
        pe = PersonaEngine()
        layer = pe.build_constraint_layer()
        assert pe.get_name() not in layer
        assert "唯一的我" not in layer
        assert "身份自指原则" in layer


class TestLegacyPatchRemoved:
    """补丁层不得残留：这些符号的存在前提（双身份路径）已被拆除。"""

    def test_symbols_gone(self):
        import my_character.persona_engine as pe_mod

        for name in (
            "DEFAULT_PERSONA_DESC",
            "strip_default_identity",
            "build_external_constraint_layer",
            "EXTERNAL_SELF_REFERENCE_DIRECTIVES",
        ):
            assert not hasattr(pe_mod, name), f"{name} 应随双路径一起删除"

    def test_orchestrator_has_no_second_identity_block(self):
        src = Path("orchestrator/optimized_orchestrator.py").read_text(encoding="utf-8")
        assert "_load_character_persona_segment" not in src
        # 旧第二身份段的 f-string 模板（注释里的追述不算残留）
        assert '# 当前必须扮演的角色（最高优先级）\\n' not in src

    def test_persona_service_has_no_external_branch(self):
        src = Path("shisi/application/persona_service.py").read_text(encoding="utf-8")
        assert "strip_default_identity(" not in src
        assert "build_external_constraint_layer(" not in src
