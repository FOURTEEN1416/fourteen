"""shisi knowledge 模块回归测试 — KeywordRetriever + BM25Retriever + CharacterKnowledgeService"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from shisi.knowledge.retriever import (
    BM25Retriever,
    KeywordRetriever,
    KnowledgeChunk,
    RetrievalResult,
)
from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService


def _make_chunks() -> list[KnowledgeChunk]:
    """构建一组测试用知识块"""
    return [
        KnowledgeChunk(content="她是一个性格开朗、活泼可爱的女孩，总是充满正能量。", source="personality", source_id="p0"),
        KnowledgeChunk(content="开心的时候会哈哈大笑，难过的时候也会偷偷哭泣。", source="personality", source_id="p1"),
        KnowledgeChunk(content="她喜欢在雨天读书，享受宁静的时光。", source="personality", source_id="p2"),
        KnowledgeChunk(content="每当朋友遇到困难，她都会热心帮助。", source="behavior", source_id="b0"),
        KnowledgeChunk(content="她的梦想是环游世界，体验不同的文化。", source="background", source_id="bg0"),
    ]


class TestKeywordRetriever:
    """KeywordRetriever 基础检索功能测试"""

    def test_index_and_search(self):
        """索引后检索含"开心"的 chunks"""
        retriever = KeywordRetriever()
        chunks = _make_chunks()
        retriever.index(chunks)

        result = retriever.search("开心", top_k=3)
        assert isinstance(result, RetrievalResult)
        assert result.total_chunks == 5
        assert len(result.chunks) > 0
        hit_contents = [c.content for c in result.chunks]
        assert any("开心" in c for c in hit_contents), f"应检索到含'开心'的块: {hit_contents}"

    def test_search_empty_index(self):
        """空索引检索返回空结果"""
        retriever = KeywordRetriever()
        result = retriever.search("开心", top_k=3)
        assert isinstance(result, RetrievalResult)
        assert result.chunks == []
        assert result.total_chunks == 0

    def test_search_empty_query(self):
        """空查询返回全部 chunk"""
        retriever = KeywordRetriever()
        chunks = _make_chunks()
        retriever.index(chunks)
        result = retriever.search("", top_k=5)
        assert len(result.chunks) == 5

    def test_add_chunks(self):
        """追加知识块后索引扩展"""
        retriever = KeywordRetriever()
        retriever.index(_make_chunks())
        new_chunk = KnowledgeChunk(content="她最近开始学习弹钢琴。", source="personality", source_id="p_new")
        retriever.add_chunks([new_chunk])
        assert retriever._total_chunks == 6


class TestBM25Retriever:
    """BM25Retriever 索引建立 + 检索 + 序列化测试"""

    def test_index_and_search(self):
        """BM25 索引后可检索"""
        retriever = BM25Retriever(k1=1.5, b=0.75)
        chunks = _make_chunks()
        retriever.index(chunks)

        result = retriever.search("雨天 读书", top_k=2)
        assert isinstance(result, RetrievalResult)
        assert len(result.chunks) <= 2

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        """save + from_file 持久化往返"""
        orig = BM25Retriever(k1=1.2, b=0.6)
        orig.index(_make_chunks())

        save_path = tmp_path / "bm25_index.json"
        orig.save(str(save_path))

        assert save_path.exists()
        loaded = BM25Retriever.from_file(str(save_path))
        assert loaded._k1 == 1.2
        assert loaded._b == 0.6
        assert loaded._total_chunks == 5

        result = loaded.search("开心", top_k=3)
        assert len(result.chunks) > 0
        assert any("开心" in c.content for c in result.chunks)

    def test_from_file_nonexistent(self):
        """加载不存在的文件应抛出异常"""
        with pytest.raises(FileNotFoundError):
            BM25Retriever.from_file("/nonexistent/path.json")

    def test_empty_index_search(self):
        """空 BM25 索引检索"""
        retriever = BM25Retriever()
        result = retriever.search("test", top_k=3)
        assert len(result.chunks) == 0


class TestCharacterKnowledgeService:
    """CharacterKnowledgeService 索引 + 检索 + 持久化测试"""

    def test_build_index_and_search(self):
        """构建索引后可检索"""
        service = CharacterKnowledgeService(use_bm25=True)
        chunks = _make_chunks()

        retriever = BM25Retriever()
        retriever.index(chunks)
        service._retrievers["char_001"] = retriever
        service._chunk_counts["char_001"] = len(chunks)

        result = service.search("char_001", "开心", top_k=3)
        assert isinstance(result, RetrievalResult)
        assert len(result.chunks) > 0
        top = result.get_top(1)
        assert len(top) == 1

    def test_search_nonexistent_character(self):
        """检索不存在的角色返回空结果"""
        service = CharacterKnowledgeService()
        result = service.search("no_such_char", "开心")
        assert isinstance(result, RetrievalResult)
        assert result.chunks == []

    def test_save_and_load_index(self, tmp_path: Path):
        """save_index + load_index 持久化往返"""
        index_dir = tmp_path / "knowledge_index"
        service = CharacterKnowledgeService(use_bm25=True, index_dir=index_dir)
        chunks = _make_chunks()

        retriever = BM25Retriever()
        retriever.index(chunks)
        service._retrievers["char_002"] = retriever
        service._chunk_counts["char_002"] = len(chunks)

        service.save_index("char_002")
        expected_path = index_dir / "char_002.json"
        assert expected_path.exists()

        service.clear("char_002")
        assert not service.has_index("char_002")

        loaded_ok = service.load_index("char_002")
        assert loaded_ok is True
        assert service.has_index("char_002")

        result = service.search("char_002", "雨天")
        assert len(result.chunks) > 0

    def test_load_index_nonexistent(self):
        """加载不存在的索引返回 False"""
        service = CharacterKnowledgeService()
        ok = service.load_index("no_such_char")
        assert ok is False

    def test_has_index_and_clear(self):
        """has_index / clear 接口"""
        service = CharacterKnowledgeService()
        assert not service.has_index("char_003")

        retriever = BM25Retriever()
        retriever.index(_make_chunks())
        service._retrievers["char_003"] = retriever
        assert service.has_index("char_003")

        service.clear("char_003")
        assert not service.has_index("char_003")

    def test_get_stats(self):
        """get_stats 返回正确统计"""
        service = CharacterKnowledgeService(use_bm25=True)
        stats = service.get_stats("char_004")
        assert stats == {"indexed": False, "total_chunks": 0, "retriever_type": "bm25"}

        chunks = _make_chunks()
        retriever = BM25Retriever()
        retriever.index(chunks)
        service._retrievers["char_004"] = retriever
        service._chunk_counts["char_004"] = len(chunks)

        stats = service.get_stats("char_004")
        assert stats["indexed"] is True
        assert stats["total_chunks"] == 5
        assert stats["retriever_type"] == "bm25"