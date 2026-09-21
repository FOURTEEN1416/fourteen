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


def _card_path_by_name(name: str) -> Path | None:
    """按**展平后的角色名**定位卡文件。

    旧用例用文件名子串 "persona_林挽夏" 匹配，而目录里的文件全部按 id 命名
    （`06479d4c.json` 等）—— 该条件恒不成立，用例**从未真正执行**（静默 skip）。
    """
    for path in _all_character_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(normalize_character_card(data).get("name") or "") == name:
            return path
    return None


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


def test_persona_service_resolves_nested_card() -> None:
    """嵌套 SillyTavern 格式的角色卡应经唯一路径解析并进 prompt。

    2026-09-21 唯一身份路径：orchestrator 的第二身份段（`=== 角色卡人设 ===`）
    已删除，角色定义只由 `PersonaService` → `prompt_builder` 注入一次。
    """
    from shisi.application.persona_service import PersonaService

    path = _card_path_by_name("林挽夏")
    if path is None:
        pytest.skip("未找到林挽夏角色文件")

    normalized = normalize_character_card(json.loads(path.read_text(encoding="utf-8")))
    character_id = normalized["id"]

    ps = PersonaService(config_loader=None, llm_gateway=None)
    card = ps._resolve_character_card(character_id)
    assert card is not None
    assert card["name"] == normalized["name"]

    prompt = ps.build_system_prompt(character_id=character_id, user_message="你好")
    assert f"你是{normalized['name']}" in prompt
    # 完整描述只注入一次，且不再有第二身份段
    description = str(normalized.get("description") or "")
    if description:
        assert prompt.count(description) == 1
    assert "=== 角色卡人设 ===" not in prompt
    assert "# 当前必须扮演的角色" not in prompt


def test_builtin_default_card_is_not_empty() -> None:
    """default/demo/空 → 内置 persona.yaml 卡（与文件卡同构，且带人格散文）。"""
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    for cid in ("", "default", "demo", None):
        card = ps._resolve_character_card(cid)
        assert isinstance(card, dict), f"{cid!r} 应解析为内置卡"
        assert card["name"] == ps.engine.get_name()
        assert str(card.get("description") or "").strip()
    prompt = ps.build_system_prompt(character_id="default", user_message="你好")
    assert f"你是{ps.engine.get_name()}" in prompt


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


def test_character_card_cache_invalidation() -> None:
    """角色卡缓存失效应能清除指定角色或全部角色。

    2026-09-21：orchestrator 不再自建第二套人设缓存，缓存唯一 owner 是
    `PersonaService`；`OptimizedOrchestrator.invalidate_character_persona_cache`
    退化为转发入口（API 侧仍按原名调用）。
    """
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    ps._card_cache.clear()
    ps._card_cache["c1"] = (1.0, {"name": "one"})
    ps._card_cache["c2"] = (1.0, {"name": "two"})

    ps.invalidate_character_cache("c1")
    assert "c1" not in ps._card_cache
    assert "c2" in ps._card_cache

    ps.invalidate_character_cache()
    assert not ps._card_cache

    # 转发入口存在且不报错（无 orch 实例时亦安全）
    assert hasattr(OptimizedOrchestrator, "invalidate_character_persona_cache")


def test_knowledge_base_indexes_character_card() -> None:
    """角色卡数据应能被 CrawlerAdapter 索引为知识库，并支持检索。"""
    from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService
    from shisi.knowledge.crawler_adapter import CharacterCrawlerAdapter

    path = _card_path_by_name("林挽夏")
    if path is None:
        pytest.skip("未找到林挽夏角色文件")

    data = json.loads(path.read_text(encoding="utf-8"))
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


def test_prompt_builder_retrieves_knowledge_for_query() -> None:
    """prompt_builder 自身的检索能力：带 user_message 时必须注入知识块。"""
    from shisi.application.persona_service import PersonaService
    from shisi.core.services import prompt_builder
    from shisi.knowledge.character_knowledge_service import get_knowledge_service

    path = _card_path_by_name("林挽夏")
    if path is None:
        pytest.skip("未找到林挽夏角色文件")

    data = json.loads(path.read_text(encoding="utf-8"))
    card = normalize_character_card(data)
    character_id = card["id"]

    ps = PersonaService(config_loader=None, llm_gateway=None)
    character = ps._build_character_from_card(character_id, ps._map_emotional_state(None))
    assert character is not None

    # 确保知识索引存在
    svc = get_knowledge_service()
    if not svc.has_index(character_id):
        svc.index_character(character_id, character)

    prompt = prompt_builder.build(character, user_message="她叫什么名字", chat_history="")
    assert character.name in prompt
    assert "# 角色知识库" in prompt, "带查询时知识块必须注入"


def test_persona_service_injects_rag_exactly_once() -> None:
    """PersonaService 路由的知识注入只出现一次（v1.15 去重的真实契约）。

    该路由给 prompt_builder 传 user_message=""（当前消息只走 messages），
    所以本轮检索结果由 `rag_context` 承载；两处同时注入即为重复。
    """
    from shisi.application.persona_service import PersonaService

    path = _card_path_by_name("林挽夏")
    if path is None:
        pytest.skip("未找到林挽夏角色文件")
    character_id = normalize_character_card(
        json.loads(path.read_text(encoding="utf-8"))
    )["id"]

    ps = PersonaService(config_loader=None, llm_gateway=None)
    prompt = ps.build_system_prompt(
        character_id=character_id,
        user_message="她叫什么名字",
        rag_context="她的名字是林挽夏，住在巷子尽头的四楼。",
    )
    assert prompt.count("# 角色知识库") == 1
    assert "她的名字是林挽夏" in prompt


class TestSystemPromptStructure:
    """行业对齐的 system prompt 结构回归（2026-09-20）。

    依据：SillyTavern 默认序列与 chara-card-spec-v2 —— 历史之后的指令
    （post_history_instructions）对生成的约束力远高于历史之前；
    mes_example（对话示例）注入位置在对话历史之前。
    """

    @staticmethod
    def _aggregate(**kw) -> object:
        from shisi.core.models.character_aggregate import CharacterAggregate

        defaults: dict = dict(
            id="struct01",
            name="测试角色",
            description="她是测试角色。",
            personality_text="她温柔坚韧。",
            scenario="夏日傍晚的小巷。",
            creator_notes="【输出限制】每次回复不超过三句。",
            source_data={"mes_example": "<START>\n用户：你好\n测试角色：嗯。"},
        )
        defaults.update(kw)
        return CharacterAggregate(**defaults)

    def test_creator_notes_after_history(self):
        """扮演规则必须出现在对话历史之后（post-history 位置）。"""
        prompt = self._aggregate().build_system_prompt(
            user_message="在吗", chat_history="用户：早\n测试角色：早。",
        )
        assert prompt.index("# 对话历史") < prompt.index("# 扮演规则")
        assert prompt.index("# 扮演规则") < prompt.index("用户: 在吗")

    def test_dialogue_examples_before_history(self):
        """mes_example 注入为对话示例段，位于对话历史之前。"""
        prompt = self._aggregate().build_system_prompt(
            user_message="在吗", chat_history="用户：早\n测试角色：早。",
        )
        assert "# 对话示例" in prompt
        assert prompt.index("# 对话示例") < prompt.index("# 对话历史")
        assert "仅示范语气与格式" in prompt
        assert "用户：你好" in prompt

    def test_scenario_guarded_when_present(self):
        """scenario 存在时（导入卡）必须带开场氛围守卫，防永久锚定。"""
        prompt = self._aggregate().build_system_prompt(user_message="嗨")
        assert "开场情境" in prompt
        assert "不代表当前正在发生" in prompt
        assert "# 场景\n" not in prompt

    def test_no_scenario_section_when_absent(self):
        """卡无 scenario（现役 41 卡状态）时不渲染场景相关段落。"""
        prompt = self._aggregate(scenario="").build_system_prompt(user_message="嗨")
        assert "开场情境" not in prompt
        assert "# 场景" not in prompt

    def test_dialogue_examples_empty_when_no_mes(self):
        """无 mes_example 时不渲染对话示例段。"""
        prompt = self._aggregate(source_data={}).build_system_prompt(user_message="嗨")
        assert "# 对话示例" not in prompt
