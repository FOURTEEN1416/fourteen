"""CharacterKnowledgeService — 从角色卡提取知识并检索增强。

职责：
1. 从 CharaCardV2 / CharacterAggregate 提取所有可索引的知识
2. 建索引并支持检索
3. 索引持久化（缓存到磁盘，避免重复建索引）
4. 注入 LLM 上下文
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from shisi.character.models import CharaCardV2
from shisi.core.models.character_aggregate import CharacterAggregate

from .retriever import (
    BM25Retriever,
    KeywordRetriever,
    KnowledgeChunk,
    RetrievalResult,
)

logger = logging.getLogger("shisi.knowledge.character_knowledge_service")

# 默认 BM25 索引缓存目录
_DEFAULT_INDEX_DIR = Path("data") / "knowledge"


class CharacterKnowledgeService:
    """角色知识服务 — 知识提取 + 检索 + 上下文注入。"""

    def __init__(self, use_bm25: bool = True, index_dir: str | Path | None = None):
        self._use_bm25 = use_bm25
        self._retrievers: dict[str, KeywordRetriever | BM25Retriever] = {}
        self._chunk_counts: dict[str, int] = {}
        self._index_dir = Path(index_dir) if index_dir else _DEFAULT_INDEX_DIR

    # ── 索引持久化 ──

    def _index_path(self, character_id: str) -> Path:
        return self._index_dir / f"{character_id}.json"

    def save_index(self, character_id: str) -> None:
        """将角色 BM25 索引保存到磁盘。"""
        retriever = self._retrievers.get(character_id)
        if not retriever or not isinstance(retriever, BM25Retriever):
            return
        path = self._index_path(character_id)
        retriever.save(path)
        logger.info("BM25 索引已保存: %s (%d 块)", path, self._chunk_counts.get(character_id, 0))

    def load_index(self, character_id: str) -> bool:
        """从磁盘加载角色 BM25 索引。成功返回 True。"""
        path = self._index_path(character_id)
        if not path.exists():
            return False
        try:
            retriever = BM25Retriever.from_file(path)
            self._retrievers[character_id] = retriever
            self._chunk_counts[character_id] = len(retriever._chunks)  # type: ignore[attr-defined]
            logger.info("BM25 索引已加载: %s (%d 块)", path, self._chunk_counts[character_id])
            return True
        except Exception as e:
            logger.warning("BM25 索引加载失败，将重新构建: %s — %s", path, e)
            return False

    def ensure_index(self, character_id: str, card: CharaCardV2 | None = None,
                     character: CharacterAggregate | None = None) -> bool:
        """确保角色索引就绪。
        - 先尝试磁盘加载
        - 失败则从 card/character 建索引
        - 建索引后自动保存到磁盘
        返回是否索引可用。
        """
        if character_id in self._retrievers:
            return True
        # 尝试从磁盘加载
        if self.load_index(character_id):
            return True
        # 从 card 建索引
        if card is not None:
            self.index_from_card(character_id, card)
            self.save_index(character_id)
            return True
        # 从 character 建索引
        if character is not None:
            self.index_character(character_id, character)
            self.save_index(character_id)
            return True
        return False

    # ── 索引构建 ──

    def index_character(self, character_id: str, character: CharacterAggregate) -> None:
        """为角色建知识索引。"""
        chunks = self._extract_from_character(character)
        retriever = BM25Retriever() if self._use_bm25 else KeywordRetriever()
        retriever.index(chunks)
        self._retrievers[character_id] = retriever
        self._chunk_counts[character_id] = len(chunks)
        logger.info("角色知识索引完成: %s → %d 知识块", character_id, len(chunks))

    def index_from_card(self, character_id: str, card: CharaCardV2) -> None:
        """从 CharaCardV2 建索引。"""
        chunks = self._extract_from_card(card)
        retriever = BM25Retriever() if self._use_bm25 else KeywordRetriever()
        retriever.index(chunks)
        self._retrievers[character_id] = retriever
        self._chunk_counts[character_id] = len(chunks)
        logger.info("角色知识索引完成(卡): %s → %d 知识块", character_id, len(chunks))

    def search(self, character_id: str, query: str, top_k: int = 3) -> RetrievalResult:
        """检索角色知识。"""
        retriever = self._retrievers.get(character_id)
        if not retriever:
            return RetrievalResult()
        return retriever.search(query, top_k=top_k)

    def get_knowledge_context(self, character_id: str, query: str, top_k: int = 3) -> str:
        """获取格式化的知识上下文，直接用于 prompt 注入。"""
        result = self.search(character_id, query, top_k=top_k)
        context = result.to_prompt_context(k=top_k)
        return context

    def has_index(self, character_id: str) -> bool:
        return character_id in self._retrievers

    def get_stats(self, character_id: str) -> dict[str, Any]:
        return {
            "indexed": character_id in self._retrievers,
            "total_chunks": self._chunk_counts.get(character_id, 0),
            "retriever_type": "bm25" if self._use_bm25 else "keyword",
        }

    def clear(self, character_id: str | None = None) -> None:
        if character_id:
            self._retrievers.pop(character_id, None)
            self._chunk_counts.pop(character_id, None)
        else:
            self._retrievers.clear()
            self._chunk_counts.clear()

    def add_knowledge_chunks(self, character_id: str, chunks: list[KnowledgeChunk]) -> None:
        """向指定角色追加知识块；若索引不存在则自动创建。"""
        if character_id not in self._retrievers:
            retriever = BM25Retriever() if self._use_bm25 else KeywordRetriever()
            self._retrievers[character_id] = retriever
            self._chunk_counts[character_id] = 0
        retriever = self._retrievers[character_id]
        retriever.add_chunks(chunks)
        self._chunk_counts[character_id] = len(retriever._chunks)  # type: ignore[attr-defined]
        logger.info("角色 %s 追加知识块: +%d → %d 块", character_id, len(chunks), self._chunk_counts[character_id])

    # ── 知识提取 ──

    def _extract_from_character(self, character: CharacterAggregate) -> list[KnowledgeChunk]:
        """从 CharacterAggregate 提取知识块。"""
        chunks: list[KnowledgeChunk] = []
        source_data = character.source_data or {}

        # 1. personality — 分段提取
        if character.persona.core_anchors:
            for i, anchor in enumerate(character.persona.core_anchors):
                if anchor.strip():
                    chunks.append(KnowledgeChunk(
                        content=anchor.strip(),
                        source="personality.core_anchors",
                        source_id=f"anchor_{i}",
                    ))

        # 2. description
        if character.description:
            for i, paragraph in enumerate(self._split_paragraphs(character.description)):
                chunks.append(KnowledgeChunk(
                    content=paragraph,
                    source="description",
                    source_id=f"desc_{i}",
                ))

        # 3. source_data 中的 personality 长文本
        personality_text = source_data.get("personality", "") or source_data.get("data", {}).get("personality", "")
        if personality_text:
            for i, paragraph in enumerate(self._split_paragraphs(personality_text)):
                if paragraph.strip() and len(paragraph) > 10:
                    chunks.append(KnowledgeChunk(
                        content=paragraph.strip(),
                        source="personality",
                        source_id=f"personality_{i}",
                    ))

        # 4. scenario
        scenario = source_data.get("scenario", "") or source_data.get("data", {}).get("scenario", "")
        if scenario:
            chunks.append(KnowledgeChunk(
                content=scenario,
                source="scenario",
            ))

        # 5. creator_notes
        creator_notes = source_data.get("creator_notes", "") or source_data.get("data", {}).get("creator_notes", "")
        if creator_notes:
            for i, section in enumerate(self._split_sections(creator_notes)):
                if section.strip() and len(section) > 20:
                    chunks.append(KnowledgeChunk(
                        content=section.strip(),
                        source="creator_notes",
                        source_id=f"note_{i}",
                    ))

        # 6. example dialogues (mes_example)
        mes_example = source_data.get("mes_example", "") or source_data.get("data", {}).get("mes_example", "")
        if mes_example:
            chunks.append(KnowledgeChunk(
                content=mes_example,
                source="mes_example",
            ))

        return chunks

    def _extract_from_card(self, card: CharaCardV2) -> list[KnowledgeChunk]:
        """从 CharaCardV2 提取知识块。"""
        chunks: list[KnowledgeChunk] = []
        data = card.data

        # 1. personality 分段
        if data.personality:
            for i, paragraph in enumerate(self._split_paragraphs(data.personality)):
                if paragraph.strip() and len(paragraph) > 10:
                    chunks.append(KnowledgeChunk(
                        content=paragraph.strip(),
                        source="personality",
                        source_id=f"personality_{i}",
                    ))

        # 2. scenario
        if data.scenario:
            chunks.append(KnowledgeChunk(
                content=data.scenario,
                source="scenario",
            ))

        # 3. creator_notes 分段
        if data.creator_notes:
            for i, section in enumerate(self._split_sections(data.creator_notes)):
                if section.strip() and len(section) > 20:
                    chunks.append(KnowledgeChunk(
                        content=section.strip(),
                        source="creator_notes",
                        source_id=f"note_{i}",
                    ))

        # 4. WorldInfoBook entries
        if data.character_book:
            for entry in data.character_book.entries:
                if entry.enabled and entry.content.strip():
                    chunks.append(KnowledgeChunk(
                        content=entry.content.strip(),
                        source="world_info",
                        source_id=f"world_{entry.id}",
                    ))

        # 5. mes_example
        if data.mes_example:
            chunks.append(KnowledgeChunk(
                content=data.mes_example,
                source="mes_example",
            ))

        # 6. description
        if data.description:
            for i, paragraph in enumerate(self._split_paragraphs(data.description)):
                chunks.append(KnowledgeChunk(
                    content=paragraph,
                    source="description",
                    source_id=f"desc_{i}",
                ))

        return chunks

    @staticmethod
    def _split_paragraphs(text: str, max_len: int = 500) -> list[str]:
        """按段落分割，长段进一步切分。"""
        paragraphs = []
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            if len(para) <= max_len:
                paragraphs.append(para)
            else:
                # 长段按句号切分
                import re
                sentences = re.split(r'(?<=[。！？!?])', para)
                current = ""
                for sent in sentences:
                    if not sent.strip():
                        continue
                    if len(current) + len(sent) < max_len:
                        current += sent
                    else:
                        if current:
                            paragraphs.append(current.strip())
                        current = sent
                if current:
                    paragraphs.append(current.strip())
        return paragraphs

    @staticmethod
    def _split_sections(text: str, min_len: int = 30) -> list[str]:
        """按编号标题分段（如 1. xxx / 2. xxx）。"""
        import re
        # "数字. " 或 "数字、"
        parts = re.split(r'\n(?:[\d]+[.、．]\s*)', text)
        result = []
        for part in parts:
            part = part.strip()
            if part and len(part) >= min_len:
                result.append(part)
        if not result:
            # fallback: 按段落
            result = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) >= min_len]
        return result


# ── 单例 ──

_knowledge_service: CharacterKnowledgeService | None = None


def get_knowledge_service() -> CharacterKnowledgeService:
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = CharacterKnowledgeService(use_bm25=True)
    return _knowledge_service
