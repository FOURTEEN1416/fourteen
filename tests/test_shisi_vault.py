"""shisi vault 模块回归测试 — PersonaAdapter + VaultCollector"""

from __future__ import annotations

from dataclasses import fields

from shisi.character.models import CharaCardV2, CharacterData
from shisi.knowledge.retriever import KnowledgeChunk
from shisi.vault import PersonaAdapter, PersonaFeatures, VaultCollector


def _minimal_card() -> CharaCardV2:
    """构建最小 CharaCardV2"""
    return CharaCardV2(
        data=CharacterData(
            name="测试角色",
            description="一个温柔体贴的女孩，总是为他人着想。",
            personality="她性格温和，说话轻声细语。\n喜欢帮助别人。\n有时会害羞。",
            scenario="在一个宁静的小镇上经营一家花店。",
            creator_notes="1. 说话带语气词\n2. 容易脸红\n3. 喜欢小动物",
        )
    )


class TestPersonaAdapter:
    """PersonaAdapter 接口测试"""

    def test_extract_returns_persona_features(self):
        """extract 返回 PersonaFeatures 实例"""
        card = _minimal_card()
        features = PersonaAdapter.extract(card)
        assert isinstance(features, PersonaFeatures)
        assert len(features.core_anchors) > 0
        assert len(features.background) > 0

    def test_core_anchors_extracted_from_personality(self):
        """personality 中的内容出现在 core_anchors 中"""
        card = _minimal_card()
        features = PersonaAdapter.extract(card)
        all_text = " ".join(features.core_anchors)
        assert "温和" in all_text or "温柔" in all_text

    def test_persona_features_to_dict_roundtrip(self):
        """PersonaFeatures to_dict / from_dict 往返"""
        card = _minimal_card()
        features = PersonaAdapter.extract(card)
        d = features.to_dict()
        restored = PersonaFeatures.from_dict(d)
        assert restored.core_anchors == features.core_anchors
        assert restored.speaking_style == features.speaking_style
        assert restored.background == features.background
        assert restored.relationship == features.relationship
        assert restored.behavior_rules == features.behavior_rules
        assert restored.raw_personality == features.raw_personality

    def test_extract_empty_card(self):
        """空角色卡提取不会崩溃"""
        card = CharaCardV2()
        features = PersonaAdapter.extract(card)
        assert isinstance(features, PersonaFeatures)


class TestVaultCollector:
    """VaultCollector 构造测试"""

    def test_construction_default(self):
        """默认构造成功"""
        collector = VaultCollector()
        assert collector is not None
        assert collector._service is not None

    def test_construction_with_service(self):
        """传入 knowledge_service 构造成功"""
        from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService
        service = CharacterKnowledgeService(use_bm25=True)
        collector = VaultCollector(knowledge_service=service)
        assert collector._service is service

    def test_collect_card_minimal(self):
        """collect_card 不抛出异常（空索引场景）"""
        collector = VaultCollector()
        card = _minimal_card()
        count = collector.collect_card("char_vault_1", card)
        assert count >= 0
        assert isinstance(count, int)


class TestKnowledgeChunkInVault:
    """KnowledgeChunk 字段在 vault 上下文中的正确性"""

    def test_knowledge_chunk_fields(self):
        """KnowledgeChunk 字段完整"""
        field_names = {f.name for f in fields(KnowledgeChunk)}
        expected = {"content", "source", "source_id", "score"}
        assert field_names == expected, f"字段不匹配: {field_names} vs {expected}"

    def test_knowledge_chunk_construction(self):
        """KnowledgeChunk 可正确构造"""
        kc = KnowledgeChunk(
            content="测试知识内容",
            source="vault.core_anchor",
            source_id="char_001_anchor_0",
            score=0.95,
        )
        assert kc.content == "测试知识内容"
        assert kc.source == "vault.core_anchor"
        assert kc.source_id == "char_001_anchor_0"
        assert kc.score == 0.95
