"""人设注入贴合测试。

验证角色卡数据能够正确展平、清洗并注入到 system prompt 中，
确保 LLM 在回复时能够使用角色名、性格、说话风格、场景设定等人设信息。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from main import OptimizedOrchestrator
from utils.character_helpers import normalize_character_card, sanitize_character_name, sanitize_character_text

CHARACTERS_DIR = Path("config/characters")


def _all_character_files() -> list[Path]:
    if not CHARACTERS_DIR.exists():
        return []
    return sorted(CHARACTERS_DIR.glob("*.json"))


@pytest.mark.parametrize("path", _all_character_files(), ids=lambda p: p.name)
def test_character_name_is_cleaned(path: Path) -> None:
    """所有角色文件的名称都应被清洗，去除作者、定制、时间戳等噪声。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_character_card(data)
    name = normalized["name"]

    assert name, f"{path.name}: 角色名称不能为空"
    assert "persona_" not in name, f"{path.name}: 名称中仍包含 persona_ 前缀"
    assert not any(k in name for k in ("by", "BY", "定制", "作者", "著", "听得见", "银子")), (
        f"{path.name}: 名称中仍包含作者/署名信息: {name}"
    )


@pytest.mark.parametrize("path", _all_character_files(), ids=lambda p: p.name)
def test_character_description_is_present_and_clean(path: Path) -> None:
    """角色描述应被正确提取（含嵌套格式），且不含作者署名。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_character_card(data)
    description = normalized.get("description", "")
    creator_notes = normalized.get("creator_notes", "")

    assert description or creator_notes, f"{path.name}: description 与 creator_notes 不能同时为空"

    for field, value in [("description", description), ("creator_notes", creator_notes)]:
        if not value:
            continue
        assert not any(k in value for k in ("by", "BY", "定制", "作者", "著", "听得见", "银子")), (
            f"{path.name}: {field} 中仍包含作者/署名信息: {value[:60]}"
        )


def test_load_character_persona_segment_extracts_nested_data() -> None:
    """_load_character_persona_segment 应能正确提取嵌套 SillyTavern 格式中的人设。"""
    # 使用已知的嵌套格式角色文件
    paths = [p for p in _all_character_files() if "persona_林挽夏" in p.name]
    if not paths:
        pytest.skip("未找到林挽夏角色文件")

    data = json.loads(paths[0].read_text(encoding="utf-8"))
    normalized = normalize_character_card(data)
    character_id = normalized["id"]

    segment = OptimizedOrchestrator._load_character_persona_segment(character_id)

    assert "=== 角色卡人设 ===" in segment
    assert normalized["name"] in segment
    assert normalized["description"][:30] in segment
    assert "场景设定" in segment


def test_load_character_persona_segment_no_default() -> None:
    """default/demo/空角色 ID 不注入额外人设。"""
    assert OptimizedOrchestrator._load_character_persona_segment("") == ""
    assert OptimizedOrchestrator._load_character_persona_segment("default") == ""
    assert OptimizedOrchestrator._load_character_persona_segment("demo") == ""


def test_sanitize_character_text_removes_author_marks() -> None:
    """文本清洗函数应去除作者署名与定制标记。"""
    raw = "她是上杉绘梨衣（作者：听得见），性格温柔。"
    cleaned = sanitize_character_text(raw)
    assert "作者" not in cleaned
    assert "听得见" not in cleaned
    assert "（）" not in cleaned
    assert "她是上杉绘梨衣，性格温柔。" in cleaned


def test_sanitize_character_name_removes_metadata() -> None:
    """名称清洗函数应去除人设前缀、作者署名与时间戳。"""
    assert sanitize_character_name("persona_林挽夏_1774701604527") == "林挽夏"
    assert sanitize_character_name("(人设)上杉绘梨衣(by诗)") == "上杉绘梨衣"
    assert sanitize_character_name("年上偏s女朋友林初夏(定制by诗)") == "年上偏s女朋友林初夏"


def test_character_persona_cache_invalidation() -> None:
    """缓存失效方法应能清除指定角色或全部角色缓存。"""
    OptimizedOrchestrator._character_persona_cache.clear()
    OptimizedOrchestrator._character_persona_cache["c1"] = "segment1"
    OptimizedOrchestrator._character_persona_cache["c2"] = "segment2"

    OptimizedOrchestrator.invalidate_character_persona_cache("c1")
    assert "c1" not in OptimizedOrchestrator._character_persona_cache
    assert "c2" in OptimizedOrchestrator._character_persona_cache

    OptimizedOrchestrator.invalidate_character_persona_cache()
    assert not OptimizedOrchestrator._character_persona_cache


def test_knowledge_base_indexes_character_card() -> None:
    """角色卡数据应能被 CrawlerAdapter 索引为知识库，并支持检索。"""
    from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService
    from shisi.knowledge.crawler_adapter import CharacterCrawlerAdapter

    paths = [p for p in _all_character_files() if "persona_林挽夏" in p.name]
    if not paths:
        pytest.skip("未找到林挽夏角色文件")

    data = json.loads(paths[0].read_text(encoding="utf-8"))
    card = normalize_character_card(data)
    character_id = card["id"]

    adapter = CharacterCrawlerAdapter()
    service = CharacterKnowledgeService(use_bm25=True)
    adapter._index_card(service, character_id, card)

    stats = service.get_stats(character_id)
    assert stats["indexed"] is True
    assert stats["total_chunks"] > 0

    context = service.get_knowledge_context(character_id, "她叫什么名字", top_k=2)
    assert context
    assert card["name"] in context or "林挽夏" in context


def test_prompt_builder_includes_knowledge_context() -> None:
    """PersonaService 构建的 system prompt 应包含角色知识库上下文。"""
    from shisi.application.persona_service import PersonaService
    from shisi.knowledge.character_knowledge_service import get_knowledge_service

    paths = [p for p in _all_character_files() if "persona_林挽夏" in p.name]
    if not paths:
        pytest.skip("未找到林挽夏角色文件")

    data = json.loads(paths[0].read_text(encoding="utf-8"))
    card = normalize_character_card(data)
    character_id = card["id"]

    ps = PersonaService(config_loader=None, llm_gateway=None)
    character = ps._build_character_from_card(character_id, ps._map_emotional_state(None))
    assert character is not None

    # 确保知识索引存在
    svc = get_knowledge_service()
    if not svc.has_index(character_id):
        svc.index_character(character_id, character)

    prompt = ps.build_system_prompt(character_id=character_id, user_message="她叫什么名字")
    assert character.name in prompt
    assert "角色知识库" in prompt or "知识" in prompt
