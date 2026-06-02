"""轻量知识检索器 — 关键词+TF 评分，零外部依赖。

先用关键词匹配 + TF 评分，后续可升级为 BM25 或向量检索。
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KnowledgeChunk:
    """知识块 — 检索的最小单元"""
    content: str
    source: str = ""  # 来源字段名: personality / scenario / creator_notes / world_entry / mes_example
    source_id: str = ""  # 来源标识（如 WorldInfoEntry.id）
    score: float = 0.0


@dataclass
class RetrievalResult:
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    total_chunks: int = 0

    def get_top(self, k: int = 3) -> list[KnowledgeChunk]:
        return sorted(self.chunks, key=lambda c: c.score, reverse=True)[:k]

    def to_prompt_context(self, k: int = 3) -> str:
        top = self.get_top(k)
        if not top:
            return ""
        sections = []
        for i, chunk in enumerate(top, 1):
            header = f"【知识 {i}】"
            if chunk.source:
                header += f"（来自: {chunk.source}）"
            sections.append(f"{header}\n{chunk.content}")
        return "\n\n".join(sections)


class KeywordRetriever:
    """关键词检索器 — 基于词频 + 长度归一化的轻量检索。"""

    _STOP_WORDS: set[str] = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
        "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
        "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
        "它", "们", "那", "什么", "怎么", "啊", "吧", "呢", "吗", "呀",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "i", "you", "he", "she", "it", "we", "they", "this", "that",
    }

    def __init__(self):
        self._chunks: list[KnowledgeChunk] = []
        self._chunk_token_counts: list[Counter] = []
        self._total_chunks: int = 0

    def index(self, chunks: list[KnowledgeChunk]) -> None:
        """建索引"""
        self._chunks = chunks
        self._chunk_token_counts = []
        for chunk in chunks:
            tokens = self._tokenize(chunk.content)
            self._chunk_token_counts.append(Counter(tokens))
        self._total_chunks = len(chunks)

    def add_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        """追加知识块到已有索引（合并后重建 TF/IDF 参数）。

        基类默认实现：合并后重建索引。子类可覆盖以优化增量更新。
        """
        if not chunks:
            return
        self.index(self._chunks + chunks)

    def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        """检索：基于查询词在各块中的 TF 评分。"""
        if not self._chunks or not query.strip():
            return RetrievalResult(chunks=self._chunks, total_chunks=self._total_chunks)

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return RetrievalResult(chunks=self._chunks, total_chunks=self._total_chunks)

        query_counter = Counter(query_tokens)

        scored: list[tuple[float, int]] = []
        for idx, chunk_tokens in enumerate(self._chunk_token_counts):
            score = self._compute_score(query_counter, chunk_tokens, self._chunks[idx].content)
            scored.append((score, idx))

        scored.sort(key=lambda x: x[0], reverse=True)

        result_chunks: list[KnowledgeChunk] = []
        for score, idx in scored[:top_k]:
            chunk = self._chunks[idx]
            chunk.score = score
            result_chunks.append(chunk)

        return RetrievalResult(chunks=result_chunks, total_chunks=self._total_chunks)

    def _compute_score(self, query_tokens: Counter, chunk_tokens: Counter, content: str) -> float:
        """计算查询与块的相关性分数。"""
        if not chunk_tokens:
            return 0.0

        # 词频匹配分
        match_score = 0.0
        for q_word, q_count in query_tokens.items():
            if q_word in chunk_tokens:
                # TF * IDF-like 权重（罕见词权重更高）
                tf = chunk_tokens[q_word] / max(chunk_tokens.total(), 1)
                idf_like = 1.0 / max(q_count, 1)
                match_score += tf * idf_like

        # 长度归一化（惩罚过长块）
        length_penalty = 1.0 / math.sqrt(max(len(content), 1))

        # 精确短语匹配奖励
        phrase_bonus = self._compute_phrase_bonus(query_tokens, content)

        return match_score * 10.0 + length_penalty * 2.0 + phrase_bonus * 5.0

    def _compute_phrase_bonus(self, query_tokens: Counter, content: str) -> float:
        """短语匹配奖励 — 连续多词出现在内容中。"""
        query_words = list(query_tokens.keys())
        if len(query_words) < 2:
            return 0.0

        bonus = 0.0
        for i in range(len(query_words) - 1):
            phrase = query_words[i] + query_words[i + 1]
            if phrase in content:
                bonus += 1.0
        return bonus / max(len(query_words) - 1, 1)

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        """分词：中文按字 + 英文按空格。"""
        if not text:
            return []

        # 清除特殊字符
        text = re.sub(r'[^\w\u4e00-\u9fff]', ' ', text)

        tokens: list[str] = []

        # 英文 token
        for en_token in text.split():
            en_clean = en_token.strip().lower()
            if en_clean and en_clean not in cls._STOP_WORDS and len(en_clean) > 1:
                tokens.append(en_clean)

        # 中文：2-gram（双字词）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        for i in range(len(chinese_chars) - 1):
            bigram = chinese_chars[i] + chinese_chars[i + 1]
            if bigram not in cls._STOP_WORDS:
                tokens.append(bigram)

        return tokens


class BM25Retriever(KeywordRetriever):
    """BM25 变体检索器 — 带 IDF 和长度归一化的 Okapi BM25 风格。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        super().__init__()
        self._k1 = k1
        self._b = b
        self._avg_doc_len: float = 0.0
        self._idf_cache: dict[str, float] = {}

    def save(self, path: str | Path) -> None:
        """将 BM25 索引序列化为 JSON 文件。"""
        data = {
            "k1": self._k1,
            "b": self._b,
            "avg_doc_len": self._avg_doc_len,
            "idf_cache": self._idf_cache,
            "chunks": [
                {"content": c.content, "source": c.source, "source_id": c.source_id, "score": c.score}
                for c in self._chunks
            ],
            "token_counts": [dict(ct) for ct in self._chunk_token_counts],
            "total_chunks": self._total_chunks,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def from_file(cls, path: str | Path) -> BM25Retriever:
        """从 JSON 文件加载 BM25 索引。"""
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        retriever = cls(k1=data.get("k1", 1.5), b=data.get("b", 0.75))
        retriever._avg_doc_len = data.get("avg_doc_len", 0.0)
        retriever._idf_cache = data.get("idf_cache", {})
        retriever._total_chunks = data.get("total_chunks", 0)
        retriever._chunks = [
            KnowledgeChunk(content=c["content"], source=c.get("source", ""),
                           source_id=c.get("source_id", ""), score=c.get("score", 0.0))
            for c in data.get("chunks", [])
        ]
        from collections import Counter
        retriever._chunk_token_counts = [Counter(ct) for ct in data.get("token_counts", [])]
        return retriever

    def index(self, chunks: list[KnowledgeChunk]) -> None:
        super().index(chunks)
        if not self._chunks:
            return

        # 计算平均文档长度
        doc_lengths = [len(self._tokenize(c.content)) for c in self._chunks]
        self._avg_doc_len = sum(doc_lengths) / max(len(doc_lengths), 1)

        # 计算 IDF
        total_docs = len(self._chunks)
        for chunk_tokens in self._chunk_token_counts:
            for token in chunk_tokens:
                if token not in self._idf_cache:
                    docs_with_token = sum(1 for ct in self._chunk_token_counts if token in ct)
                    idf = math.log((total_docs - docs_with_token + 0.5) / (docs_with_token + 0.5) + 1.0)
                    self._idf_cache[token] = idf

    def add_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        """追加知识块到已有索引（合并后重建 BM25 参数）。"""
        self.index(self._chunks + chunks)

    def _compute_score(self, query_tokens: Counter, chunk_tokens: Counter, content: str) -> float:
        if not chunk_tokens or not self._avg_doc_len:
            return 0.0

        doc_len = chunk_tokens.total()
        score = 0.0

        for q_word in query_tokens:
            if q_word not in chunk_tokens:
                continue

            tf = chunk_tokens[q_word]
            idf = self._idf_cache.get(q_word, 1.0)

            # Okapi BM25
            numerator = tf * (self._k1 + 1)
            denominator = tf + self._k1 * (1 - self._b + self._b * doc_len / self._avg_doc_len)
            score += idf * numerator / max(denominator, 0.001)

        return score
