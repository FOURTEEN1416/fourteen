"""Shisi 知识服务适配层 — 角色知识检索。

基于角色卡/角色聚合的知识索引进行 BM25 检索，返回兼容的字典。
角色 ID 由每次检索显式传入；set_character_id 仅为旧调用保留。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService, get_knowledge_service
from shisi.knowledge.retriever import KnowledgeChunk

logger = logging.getLogger("shisi.application.knowledge_service")

_DEFAULT_QUERY_TIMEOUT = 5.0


class ShisiKnowledgeAdapter:
    """十四知识检索适配层。

    基于角色卡/角色聚合的知识索引进行 BM25 检索，返回与 RAGEngineV2 兼容的字典。
    由于 shisi knowledge 是角色维度的，新调用方应给 retrieve/retrieve_async
    显式传入 character_id。模块仍保留旧游标以兼容管理/诊断调用。
    """

    def __init__(
        self,
        knowledge_service: CharacterKnowledgeService | None = None,
        vector_memory=None,
        structured_memory=None,
        semantic_memory=None,
        tone_mimic=None,
        default_character_id: str = "default",
        query_timeout: float = _DEFAULT_QUERY_TIMEOUT,
    ):
        self._service = knowledge_service or get_knowledge_service()
        self._vm = vector_memory
        self._sm = structured_memory
        self._semantic = semantic_memory
        self._tone_mimic = tone_mimic
        self._default_character_id = default_character_id
        self._current_character_id = default_character_id
        self._query_timeout = query_timeout

    # ── 属性代理（保持与 RAGEngineV2 兼容）────────────────

    @property
    def vector_memory(self):
        return self._vm

    @property
    def structured_memory(self):
        return self._sm

    @property
    def semantic_memory(self):
        return self._semantic

    @property
    def tone_mimic(self):
        return self._tone_mimic

    @property
    def knowledge_service(self) -> CharacterKnowledgeService:
        return self._service

    # ── 角色隔离 ──

    def set_character_id(self, character_id: str) -> None:
        """设置当前检索的角色 ID。"""
        self._current_character_id = character_id or self._default_character_id

    def ensure_character_index(
        self,
        character_id: str | None = None,
        card=None,
        character=None,
    ) -> bool:
        """确保指定角色索引就绪；默认使用当前角色 ID。"""
        cid = character_id or self._current_character_id
        return self._service.ensure_index(cid, card=card, character=character)

    # ── RAGEngineV2 兼容接口 ────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        character_id: str | None = None,
    ) -> dict[str, Any]:
        """基于请求指定角色检索知识，返回兼容字典。"""
        cid = character_id or self._current_character_id or self._default_character_id
        result = self._service.search(cid, query, top_k=top_k)

        results: list[dict[str, Any]] = []
        for chunk in result.get_top(top_k):
            results.append({
                "content": chunk.content,
                "source": chunk.source or "knowledge",
                "source_id": chunk.source_id,
                "category": chunk.source or "",
                "confidence": chunk.score if chunk.score else 0.5,
                "score": chunk.score,
            })

        style_examples: list[Any] = []
        if self._tone_mimic is not None:
            try:
                style_examples = self._tone_mimic.retrieve_style_examples(query, top_k=3)
            except Exception as e:  # noqa: BLE001
                logger.debug("Style example retrieval failed: %s", e)

        return {
            "results": results,
            "style_examples": style_examples,
            "total_vector": 0,
            "total_keyword": len(result.chunks),
            "total_facts": result.total_chunks,
            "total_chats": 0,
        }

    async def retrieve_async(
        self,
        query: str,
        top_k: int = 5,
        character_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.retrieve,
                    query,
                    top_k,
                    character_id=character_id,
                ),
                timeout=self._query_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "ShisiKnowledgeAdapter query timed out after %.1fs: %s",
                self._query_timeout, query[:100],
            )
            return {
                "results": [],
                "style_examples": [],
                "total_vector": 0,
                "total_keyword": 0,
                "total_facts": 0,
                "total_chats": 0,
            }

    def mark_dirty(self) -> None:
        """结构化记忆变更时调用。shisi 索引基于角色卡，此方法目前为空操作。"""

    def validate_reply(self, reply: str) -> tuple[bool, str]:
        """回复校验。shisi 适配层暂不做幻觉检查，始终通过。"""
        return True, ""

    def health_check(self) -> dict[str, Any]:
        stats = self._service.get_stats(self._current_character_id)
        return {
            "available": True,
            "use_shisi_rag": True,
            "character_id": self._current_character_id,
            "indexed": self._service.has_index(self._current_character_id),
            "bm25_available": True,
            "stats": stats,
        }

    # ── 便捷方法 ──

    def index_character(self, character_id: str, character) -> None:
        """从 CharacterAggregate 构建角色知识索引。"""
        self._service.index_character(character_id, character)

    def index_from_card(self, character_id: str, card) -> None:
        """从 CharaCardV2 构建角色知识索引。"""
        self._service.index_from_card(character_id, card)

    def add_knowledge_chunks(self, character_id: str, chunks: list[KnowledgeChunk]) -> None:
        """直接向指定角色追加知识块。"""
        self._service.add_knowledge_chunks(character_id, chunks)
