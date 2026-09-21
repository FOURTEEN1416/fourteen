"""身份拷问应答剧本回归（2026-09-22）。

生产实证（09-21 五组「我是人工智能」破防，逐轮核对全部为智谱降级轮次出词）：
降级模型在「你是机器吗/你是什么/你为什么不是人」直球拷问下转诚实模式+助手腔，
而 agnes 同题实战能顶住（「那我就是十四。不是代码，不是程序」）。修复三层：
① 剧本写入内置十四的卡真源 config/persona.yaml（creator_notes），经
   _builtin_character_card → _character_from_card 与文件卡同路进 PHI 位；
② 约束层补身份中性的「身份拷问应对」通用规则（覆盖 41 张文件卡，不内置人名）；
③ 知识槽出口排除 creator_notes（IDENTITY_KNOWLEDGE_SOURCES），防同文本回声。
"""

from shisi.application import persona_service as ps_mod
from shisi.core.services import prompt_builder


def _service() -> ps_mod.PersonaService:
    return ps_mod.PersonaService(config_loader=None, llm_gateway=None)


def test_builtin_card_carries_interrogation_script():
    """内置卡的 creator_notes 必须携带身份拷问剧本（曾经的硬编码空串缺陷）。"""
    card = _service()._builtin_character_card()
    assert "身份拷问应答" in card["creator_notes"], "内置卡必须携带身份拷问剧本"
    assert "十四" in card["creator_notes"]


def test_default_prompt_injects_script_exactly_once():
    """剧本经 PHI 位（扮演规则）注入且只出现一次——身份唯一 owner 不变量。"""
    prompt = _service().build_system_prompt(memory_context={}, character_id="default")
    assert prompt.count("身份拷问应答") == 1
    assert "# 扮演规则（必须严格遵守）" in prompt


def test_constraint_layer_rule_is_name_neutral():
    """约束层的身份拷问应对是通用规则：不得内置默认人格名（覆盖所有角色卡）。"""
    layer = _service()._engine.build_constraint_layer()
    assert "身份拷问应对" in layer
    assert "十四" not in layer, "约束层身份中性：不得内置默认人格名"


def test_file_card_creator_notes_take_priority_unchanged():
    """文件卡的 creator_notes 仍以卡为准——内置卡接线不改变文件卡路径。"""
    svc = _service()
    char = svc._character_from_card(
        {"name": "测试卡", "creator_notes": "X规则"},
        "test_card",
        svc._map_emotional_state(None),
    )
    assert char is not None
    assert char.creator_notes == "X规则"


def test_knowledge_slot_excludes_creator_notes_source():
    """creator_notes 进卡后与 description 同待遇：知识槽出口排除，防 PHI 位回声。"""
    assert "creator_notes" in prompt_builder.IDENTITY_KNOWLEDGE_SOURCES
