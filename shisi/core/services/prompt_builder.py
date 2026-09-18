"""提示词构建领域服务 — 支持 RAG + 剧情线上下文注入。"""

from __future__ import annotations

import logging

from shisi.knowledge.character_knowledge_service import get_knowledge_service

from ..models.character_aggregate import CharacterAggregate

logger = logging.getLogger("shisi.core.services.prompt_builder")


def build(
    character: CharacterAggregate,
    user_message: str = "",
    chat_history: str = "",
    use_knowledge: bool = True,
    use_storyline: bool = True,
) -> str:
    """构建系统提示词。

    按顺序：
    1. 基础角色设定 + 人设
    2. 剧情线上下文（时间、阶段规则）
    3. RAG 知识库上下文
    4. 对话历史 + 用户消息
    """
    # 1. 先获取剧情线上下文（需要推进时间）
    storyline_context = _get_storyline_context(character, use_storyline)

    # 2. 获取 RAG 知识库上下文
    knowledge_context = _get_knowledge_context(character, user_message, use_knowledge)

    # 3. 构建完整 prompt
    prompt = character.build_system_prompt(
        user_message=user_message,
        chat_history=chat_history,
        knowledge_context=knowledge_context,
        storyline_context=storyline_context,
    )

    return prompt


def merge_with_persona_prompt(base_prompt: str, character: CharacterAggregate) -> str:
    persona_segment = character.persona.to_prompt_segment()
    return f"{base_prompt}\n\n{persona_segment}"


def _get_storyline_context(character: CharacterAggregate, enabled: bool) -> str:
    """获取剧情线上下文，同时推进时间。"""
    if not enabled or not character.storyline_config:
        return ""

    from shisi.storyline.config import StorylineConfig
    from shisi.storyline.engine import get_storyline_engine

    config = StorylineConfig.from_dict(character.storyline_config)
    if not config.enabled:
        return ""

    try:
        engine = get_storyline_engine()

        # 确保引擎有配置
        if not engine.has_config(character.id):
            engine.set_config(character.id, config)
            engine.get_or_init_state(character.id)

        # 检查是否结束
        state = engine.get_state(character.id)
        if state and state.is_ended:
            # 已结束 → 返回结局旁白 + 空回复指令
            ctx = engine.get_storyline_context(character.id)
            return ctx

        # 推进时间
        engine.tick(character.id)

        # 获取上下文
        ctx = engine.get_storyline_context(character.id)
        return ctx
    except Exception:
        logger.warning("剧情线处理失败（非阻塞）", exc_info=True)
        return ""


def _get_knowledge_context(character: CharacterAggregate, user_message: str, enabled: bool) -> str:
    """获取 RAG 知识库上下文。"""
    if not enabled or not character.id or not user_message:
        return ""

    try:
        svc = get_knowledge_service()
        if not svc.has_index(character.id):
            svc.index_character(character.id, character)

        # top_k 由 3 提到 8（2026-09-18）：知识库规模实测 5~166 块、均值约 40，
        # 而此前仅注入 3 块（对 166 块的角色只用到 1.8%）。BM25 为 2-gram 关键词
        # 匹配、召回排序本就弱于语义检索，top_k 过小会把创作者写的人设细节挡在
        # prompt 之外。8 块在上下文预算内（单块为段落级，数十至数百字）。
        return svc.get_knowledge_context(character.id, user_message, top_k=8)
    except Exception:
        logger.warning("RAG 知识检索失败（非阻塞）", exc_info=True)
        return ""
