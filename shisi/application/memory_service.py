"""Shisi 记忆服务适配层 — 兼容 MemoryPipeline 接口。

shisi/memory 提供收藏/转发增强能力（FavoriteManager/ForwardManager），
核心记忆存储/检索复用 shisi/memory/legacy/（迁移自原根 memory/）。
适配层统一暴露 OptimizedOrchestrator 所需的方法，使 main.py 可以通过
配置开关在 shisi 适配层与 _legacy 兼容行为之间切换。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# shisi 自有 pipeline（迁移自原根 memory.memory_pipeline）。
# 根目录 memory/ 已物理删除，所有引用统一指向 shisi 自有位置。
# StructuredMemory / VectorMemory 改为在 _build_default_memory_backends 内 lazy import，
# 避免顶级导入未使用触发 ruff F401。
from shisi.memory.favorite_manager import FavoriteManager
from shisi.memory.forward_manager import ForwardManager
from shisi.memory.legacy.memory_pipeline import MemoryPipeline  # noqa: E402

logger = logging.getLogger("shisi.application.memory_service")


def _build_default_memory_backends(chroma_path, db_path, structured_memory, vector_memory):
    """Build defaults."""
    if vector_memory is None and chroma_path is not None:
        from shisi.memory.legacy.vector_memory import VectorMemory
        vector_memory = VectorMemory(chroma_path=str(chroma_path))
    if structured_memory is None and db_path is not None:
        from shisi.memory.legacy.structured_memory import StructuredMemory
        structured_memory = StructuredMemory(db_path=str(db_path))
    return structured_memory, vector_memory


class ShisiMemoryService:
    """兼容 MemoryPipeline 的十四记忆服务。

    对外暴露 MemoryPipeline 的核心接口，同时提供 shisi 的收藏/转发能力。
    默认行为与 MemoryPipeline 保持一致，仅在收藏/转发等增强功能上体现
    shisi 模块的差异。
    """

    def __init__(
        self,
        structured_memory=None,
        vector_memory=None,
        fact_extractor=None,
        diary_summarizer=None,
        emotion_engine=None,
        llm_gateway=None,
        db_path: str | Path | None = None,
        chroma_path: str | Path | None = None,
        working_limit: int = 20,
        retrieval_timeout: float = 1.0,
        forgetting_model: str = "exponential",
        lambda_low: float = 0.1,
        lambda_high: float = 0.01,
        conflict_similarity_threshold: float = 0.3,
        fact_extract_interval: int = 5,
    ):
        structured_memory, vector_memory = _build_default_memory_backends(
            chroma_path=chroma_path,
            db_path=db_path,
            structured_memory=structured_memory,
            vector_memory=vector_memory,
        )
        self._pipeline = MemoryPipeline(
            vector_memory=vector_memory,
            structured_memory=structured_memory,
            fact_extractor=fact_extractor,
            diary_summarizer=diary_summarizer,
            emotion_engine=emotion_engine,
            llm_gateway=llm_gateway,
            working_limit=working_limit,
            retrieval_timeout=retrieval_timeout,
            forgetting_model=forgetting_model,
            lambda_low=lambda_low,
            lambda_high=lambda_high,
            conflict_similarity_threshold=conflict_similarity_threshold,
            fact_extract_interval=fact_extract_interval,
        )

        # shisi 增强：收藏/转发
        self._favorite_manager = FavoriteManager(db_path=db_path)
        self._forward_manager = ForwardManager()

        logger.info(
            "ShisiMemoryService initialized (forgetting=%s, working_limit=%d)",
            forgetting_model, working_limit,
        )

    # ── 属性代理（保持与 MemoryPipeline 兼容）────────────────

    @property
    def session_id(self) -> str:
        return self._pipeline.session_id

    @property
    def vm(self):
        return self._pipeline.vm

    @property
    def sm(self):
        return self._pipeline.sm

    @property
    def vector_memory(self):
        return self._pipeline.vm

    @property
    def structured_memory(self):
        return self._pipeline.sm

    @property
    def working(self):
        return self._pipeline.working

    @property
    def episodic(self):
        return self._pipeline.episodic

    @property
    def semantic(self):
        return self._pipeline.semantic

    @property
    def fe(self):
        return self._pipeline.fe

    @property
    def ds(self):
        return self._pipeline.ds

    @property
    def reflection(self):
        return self._pipeline.reflection

    @property
    def summarizer(self):
        return self._pipeline.summarizer

    @property
    def favorite_manager(self) -> FavoriteManager:
        return self._favorite_manager

    @property
    def forward_manager(self) -> ForwardManager:
        return self._forward_manager

    # ── 核心兼容接口 ──────────────────────────────────────

    def after_chat(
        self,
        user_msg: str,
        reply: str,
        emotion_tag: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        return self._pipeline.after_chat(
            user_msg=user_msg,
            reply=reply,
            emotion_tag=emotion_tag,
            session_id=session_id,
        )

    def retrieve_context(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        return self._pipeline.retrieve_context(
            query=query, session_id=session_id, top_k=top_k,
        )

    async def retrieve_context_async(
        self,
        query: str,
        session_id: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        return await self._pipeline.retrieve_context_async(
            query=query, session_id=session_id, top_k=top_k,
        )

    def get_recent_context(self, n: int = 3) -> str:
        return self._pipeline.get_recent_context(n=n)

    def get_chat_context(
        self,
        session_id: str = "",
        keep_recent: int = 50,
        summary_trigger: int = 80,
    ) -> tuple[list, str]:
        return self._pipeline.get_chat_context(
            session_id=session_id,
            keep_recent=keep_recent,
            summary_trigger=summary_trigger,
        )

    def get_memory_context(self, n_chats: int = 10) -> dict[str, Any]:
        return self._pipeline.get_memory_context(n_chats=n_chats)

    def get_formatted_context(self, n_chats: int = 6) -> str:
        return self._pipeline.get_formatted_context(n_chats=n_chats)

    def store_episode(
        self,
        messages: list[dict],
        summary: str = "",
        importance: float = 0.5,
        session_id: str = "",
    ) -> str:
        return self._pipeline.episodic.store_episode(
            messages=messages,
            summary=summary,
            importance=importance,
            session_id=session_id,
        )

    def add_fact(
        self,
        fact: str,
        category: str = "general",
        confidence: float = 0.5,
        source: str = "",
    ) -> bool:
        return self._pipeline.semantic.add_fact(
            fact=fact,
            category=category,
            confidence=confidence,
            source=source,
        )

    def reset_session(self) -> None:
        self._pipeline.reset_session()

    def daily_maintenance(self) -> str | None:
        return self._pipeline.daily_maintenance()

    def health_check(self) -> dict:
        return {
            **self._pipeline.health_check(),
            "favorite_manager": {"available": True},
            "forward_manager": {"available": True},
        }

    def close(self) -> None:
        self._pipeline.close()

    # ── shisi 增强接口 ────────────────────────────────────

    def favorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.favorite(character_id, memory_id)

    def unfavorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.unfavorite(character_id, memory_id)

    def list_favorites(self, character_id: str) -> list[dict[str, Any]]:
        return self._favorite_manager.list_favorites(character_id)

    def is_favorite(self, character_id: str, memory_id: str) -> bool:
        return self._favorite_manager.is_favorite(character_id, memory_id)

    def forward(
        self,
        from_character: str,
        to_character: str,
        memory_id: str,
        memory_content: str = "",
    ) -> bool:
        return self._forward_manager.forward(
            from_character=from_character,
            to_character=to_character,
            memory_id=memory_id,
            memory_content=memory_content,
        )

    def get_forwards(self, character_id: str) -> list[dict[str, Any]]:
        return self._forward_manager.get_forwards(character_id)
