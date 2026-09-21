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
from utils.project_paths import project_path

from .retriever import (
    BM25Retriever,
    KeywordRetriever,
    KnowledgeChunk,
    RetrievalResult,
)

logger = logging.getLogger("shisi.knowledge.character_knowledge_service")

# 默认 BM25 索引缓存目录（锚定项目根，避免依赖进程 CWD —— 2026-09 全仓扫描）
_DEFAULT_INDEX_DIR = project_path("data", "knowledge")


# ── 查询扩展表 ───────────────────────────────────────────────
# 口语化提问 → 领域关键词。依据：BM25 是 2-gram 关键词匹配，用户口语提问与
# 知识库原文措辞常无交集（实测见 search() 的 docstring 注释）。
# 只在命中信号词时触发，零额外依赖、零 API 调用、零延迟增加。
_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    # ⚠️ 扩展词必须是**领域实词**：泛词（"日常"/"平时"）会引入大量噪声
    #    （实测：把"日常 平时"加进爱好扩展后，top-1 由正确块变成无关的"互动场景"）。
    (("爱好", "喜欢做", "平时做", "兴趣", "消遣"), "爱好 兴趣 习惯 消遣"),
    (("家人", "家庭", "父母", "亲人", "出身", "亲戚", "家里", "家有"), "家庭 家人 父母 亲属 出身 家世"),
    (("性格", "什么样的人", "脾气", "脾气秉性"), "性格 特质 脾气 内里 底色 为人"),
    (("是谁", "叫什么", "名字", "称呼"), "名字 称呼 身份"),
    (("朋友", "同伴", "同学"), "朋友 同学 同伴"),
    (("经历", "过去", "以前", "背景", "故事"), "经历 过去 背景 往事 早年"),
    (("能力", "擅长", "会什么", "技能"), "能力 擅长 技能 天赋 特长"),
    (("外貌", "长相", "样子", "身高", "穿着"), "外貌 长相 身高 穿着 模样"),
    (("学校", "班级", "工作", "职业"), "学校 班级 职业 工作 单位"),
    (("讨厌", "不喜欢", "害怕", "弱点"), "讨厌 害怕 弱点 禁忌"),
)


def _expand_query(query: str) -> str:
    """命中领域信号词时，返回**纯领域关键词串**（不含原查询）。

    ⚠️ 为什么返回纯扩展词而非"原查询 + 扩展词"：后者会让扩展路**仍带着原查询的
    噪声词**（如「你家里有什么人」中的"有"/"人"会命中无关的"女生小团体"块），
    实测导致正确块被压到第 3 位。双路检索时两路必须**真正互补**：
    原路保精度、扩展路提召回（扩展词用知识库惯用措辞）。

    无命中信号词时返回空串，调用方据此跳过双路检索。
    """
    if not query:
        return ""
    extras: list[str] = []
    for signals, expansion in _QUERY_EXPANSIONS:
        if any(s in query for s in signals):
            extras.append(expansion)
    if not extras:
        return ""
    return " ".join(dict.fromkeys(" ".join(extras).split()))


class CharacterKnowledgeService:
    """角色知识服务 — 知识提取 + 检索 + 上下文注入。"""

    def __init__(self, use_bm25: bool = True, index_dir: str | Path | None = None):
        self._use_bm25 = use_bm25
        self._retrievers: dict[str, KeywordRetriever | BM25Retriever] = {}
        self._chunk_counts: dict[str, int] = {}
        # 内存缓存条目对应的磁盘索引 mtime_ns（P0-7：改卡/重建脚本后必须核对，
        # 否则进程内命中缓存即永久用旧索引）
        self._index_mtimes: dict[str, int] = {}
        self._index_dir = Path(index_dir) if index_dir else _DEFAULT_INDEX_DIR

    # ── 索引持久化 ──

    def _index_path(self, character_id: str) -> Path:
        return self._index_dir / f"{character_id}.json"

    def _disk_mtime(self, character_id: str) -> int:
        """磁盘索引文件的 mtime_ns；文件不存在或不可读返回 0（视为无需核对）。"""
        try:
            return self._index_path(character_id).stat().st_mtime_ns
        except OSError:
            return 0

    def save_index(self, character_id: str) -> None:
        """将角色 BM25 索引保存到磁盘。"""
        retriever = self._retrievers.get(character_id)
        if not retriever or not isinstance(retriever, BM25Retriever):
            return
        path = self._index_path(character_id)
        retriever.save(path)
        try:
            self._index_mtimes[character_id] = path.stat().st_mtime_ns
        except OSError:
            self._index_mtimes.pop(character_id, None)
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
            self._index_mtimes[character_id] = path.stat().st_mtime_ns
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

        P0-7：进程内命中缓存前先核对磁盘 mtime——改卡或跑过
        rebuild_knowledge_index.py 之后，旧缓存必须失效而不是永久使用。
        """
        if character_id in self._retrievers:
            disk_mtime = self._disk_mtime(character_id)
            if disk_mtime and disk_mtime != self._index_mtimes.get(character_id):
                logger.info("磁盘索引已更新，丢弃内存缓存: %s", character_id)
                del self._retrievers[character_id]
                self._chunk_counts.pop(character_id, None)
                self._index_mtimes.pop(character_id, None)
            else:
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
        """检索角色知识（带查询扩展）。

        2026-09-18 新增查询扩展：BM25 是 2-gram 关键词匹配，对**口语化提问**召回很差。
        实测（阿哈 166 块）：「你家里有什么人」top-1 命中 4.18 分的**无关内容**
        （"女生小团体楠楠"，只因同含"人"字）；而扩展为
        「家庭 家人 父母 亲属 出身 早年」后，命中正确块（"早年家庭经历"）且分数升至 13.46。
        → 做法：命中领域信号词时，用「原查询 ∪ 扩展查询」双路检索并**交错合并**，
          首位给扩展路（其措辞更接近知识库原文），两路头部块交替进入注入窗口。
        """
        retriever = self._retrievers.get(character_id)
        if not retriever:
            return RetrievalResult()

        expanded = _expand_query(query)
        if not expanded:
            return retriever.search(query, top_k=top_k)

        # 双路互补检索后**交错合并**（扩展路占奇数位、原路占偶数位）。
        # 2026-09-20 修复：此前 ext+base 顺序拼接再截断，扩展路命中多时（如「X是谁」
        # 一路命中 8 个"身份锚点"块）会把原路的高 idf 块整体挤出注入窗口——实测
        # 米彩卡「昭阳是谁」top-8 完全丢掉含"昭阳"的原作知识块。交错保证两路
        # 各自的头部块都进入窗口，首位仍是扩展路（其措辞更贴近知识库原文）。
        ext = list(retriever.search(expanded, top_k=top_k).chunks)
        base = list(retriever.search(query, top_k=top_k).chunks)

        # 按 content 去重
        seen: set[str] = set()
        merged: list[Any] = []
        for i in range(max(len(ext), len(base))):
            for chunk in (ext[i] if i < len(ext) else None,
                          base[i] if i < len(base) else None):
                if chunk is None:
                    continue
                key = (chunk.content or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    merged.append(chunk)
        return RetrievalResult(chunks=merged[:top_k])

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
            self._index_mtimes.pop(character_id, None)
        else:
            self._retrievers.clear()
            self._chunk_counts.clear()
            self._index_mtimes.clear()

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

        # 0. 角色名作为可检索知识块，确保"她叫什么名字"类查询能命中
        if character.name:
            chunks.append(KnowledgeChunk(
                content=f"她的名字是{character.name}，你可以称呼她{character.name}。",
                source="character_name",
            ))

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

        # 3. personality 长文本
        #    优先取聚合根字段（2026-09-18 新增 personality_text），回退 source_data
        #    以兼容按卡直建、未传 source_data 的调用方
        #    （此前仅读 source_data → 重建索引时该段恒为空，148 块掉到 136 块）。
        personality_text = (
            getattr(character, "personality_text", "")
            or source_data.get("personality", "")
            or source_data.get("data", {}).get("personality", "")
        )
        if personality_text:
            for i, paragraph in enumerate(self._split_paragraphs(personality_text)):
                if paragraph.strip() and len(paragraph) > 10:
                    chunks.append(KnowledgeChunk(
                        content=paragraph.strip(),
                        source="personality",
                        source_id=f"personality_{i}",
                    ))

        # 4. scenario（同上：优先聚合根字段）
        scenario = (
            getattr(character, "scenario", "")
            or source_data.get("scenario", "")
            or source_data.get("data", {}).get("scenario", "")
        )
        if scenario:
            chunks.append(KnowledgeChunk(
                content=scenario,
                source="scenario",
            ))

        # 5. creator_notes（同上：优先聚合根字段）
        creator_notes = (
            getattr(character, "creator_notes", "")
            or source_data.get("creator_notes", "")
            or source_data.get("data", {}).get("creator_notes", "")
        )
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

        # 0. 角色名作为可检索知识块，确保"她叫什么名字"类查询能命中
        if data.name:
            chunks.append(KnowledgeChunk(
                content=f"她的名字是{data.name}，你可以称呼她{data.name}。",
                source="card_name",
            ))

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
