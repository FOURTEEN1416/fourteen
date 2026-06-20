"""人物爬虫适配器 — 将 character_crawler_tool 抓取结果写入 shisi 知识索引。"""

from __future__ import annotations

import logging
from typing import Any

from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService
from shisi.knowledge.retriever import KnowledgeChunk

logger = logging.getLogger("shisi.knowledge.crawler_adapter")


def _split_text(text: str, max_len: int = 300, overlap: int = 30) -> list[str]:
    """把长文本切分成固定长度的知识块。"""
    if not text:
        return []
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_len
        chunk = text[start:end]
        chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def _profile_to_chunks(profile: dict[str, Any]) -> list[KnowledgeChunk]:
    """将爬虫返回的人物 profile 解析为 KnowledgeChunk 列表。"""
    chunks: list[KnowledgeChunk] = []
    source = profile.get("source", "crawler")
    source_url = profile.get("source_url", "")

    # 1) 人物名称
    name = profile.get("name", "").strip() or profile.get("title", "").strip()
    if name:
        chunks.append(KnowledgeChunk(
            content=f"人物名称：{name}",
            source=f"{source}.name",
            source_id="name_0",
        ))

    # 2) 基本信息（infobox）
    basic_info = profile.get("basic_info", {})
    if isinstance(basic_info, dict):
        for key, value in basic_info.items():
            if not value:
                continue
            chunks.append(KnowledgeChunk(
                content=f"{key}：{value}",
                source=f"{source}.basic_info",
                source_id=f"basic_{key}",
            ))

    # 3) 摘要/简介（分段）
    summary = profile.get("summary", "")
    if summary:
        for idx, paragraph in enumerate(_split_text(summary, max_len=400)):
            chunks.append(KnowledgeChunk(
                content=paragraph,
                source=f"{source}.summary",
                source_id=f"summary_{idx}",
            ))

    # 4) 正文 content（若摘要为空，则使用正文）
    content = profile.get("content", "")
    if content and not summary:
        for idx, paragraph in enumerate(_split_text(content, max_len=400)):
            chunks.append(KnowledgeChunk(
                content=paragraph,
                source=f"{source}.content",
                source_id=f"content_{idx}",
            ))

    # 5) 章节标题（作为弱知识提示）
    sections = profile.get("sections", {})
    if isinstance(sections, dict):
        for idx, (section_name, _) in enumerate(sections.items()):
            chunks.append(KnowledgeChunk(
                content=f"相关章节：{section_name}",
                source=f"{source}.sections",
                source_id=f"section_{idx}",
            ))

    # 6) 搜索片段
    query_results = profile.get("query_results", [])
    if isinstance(query_results, list):
        for idx, qr in enumerate(query_results[:5]):
            title = qr.get("title", "")
            snippet = qr.get("snippet", "")
            text = f"{title}\n{snippet}".strip()
            if text:
                chunks.append(KnowledgeChunk(
                    content=text,
                    source=f"{source}.search",
                    source_id=f"search_{idx}",
                ))

    # 7) 元信息：来源 URL
    if source_url:
        chunks.append(KnowledgeChunk(
            content=f"信息来源：{source_url}",
            source=f"{source}.metadata",
            source_id="source_url",
        ))

    return chunks


class CharacterCrawlerAdapter:
    """把网络爬虫抓取的人物资料导入 shisi 知识索引。"""

    def __init__(self, service: CharacterKnowledgeService | None = None):
        self._service = service
        self._tool: Any | None = None

    def _get_tool(self) -> Any:
        """延迟初始化爬虫工具，处理依赖缺失。"""
        if self._tool is not None:
            return self._tool
        try:
            from tools.builtin.character_crawler_tool import CharacterCrawlerTool
            self._tool = CharacterCrawlerTool()
            return self._tool
        except Exception as e:  # noqa: BLE001
            logger.warning("爬虫工具初始化失败: %s", e)
            return None

    @property
    def _knowledge_service(self) -> CharacterKnowledgeService:
        if self._service is None:
            from shisi.knowledge.character_knowledge_service import get_knowledge_service
            self._service = get_knowledge_service()
        return self._service

    def crawl_and_index(
        self,
        character_id: str,
        name: str,
        card: Any | None = None,
    ) -> dict[str, Any]:
        """抓取人物资料并写入角色知识索引。

        Args:
            character_id: 角色唯一标识
            name: 要抓取的人物名称
            card: 可选的 CharaCardV2，用于初始化索引

        Returns:
            操作结果统计字典
        """
        tool = self._get_tool()
        if tool is None:
            return {
                "success": False,
                "character_id": character_id,
                "name": name,
                "chunks_added": 0,
                "error": "爬虫工具不可用，请安装 requests / beautifulsoup4 / cloudscraper",
            }

        logger.info("开始抓取人物资料: %s (character_id=%s)", name, character_id)
        try:
            result = tool.execute(action="fetch_person", name=name)
        except Exception as e:  # noqa: BLE001
            logger.exception("抓取人物资料失败: %s", name)
            return {
                "success": False,
                "character_id": character_id,
                "name": name,
                "chunks_added": 0,
                "error": f"抓取异常: {e}",
            }

        if not result.success or not result.data:
            return {
                "success": False,
                "character_id": character_id,
                "name": name,
                "chunks_added": 0,
                "error": result.error or "未获取到人物资料",
            }

        profile = result.data if isinstance(result.data, dict) else {}
        chunks = _profile_to_chunks(profile)
        if not chunks:
            return {
                "success": False,
                "character_id": character_id,
                "name": name,
                "chunks_added": 0,
                "error": "抓取结果为空或无法解析",
            }

        # 确保索引已初始化
        service = self._knowledge_service
        if not service.has_index(character_id):
            if card is not None:
                service.index_from_card(character_id, card)
            else:
                # 无角色卡时，用空 KeywordRetriever 占位，随后追加爬虫块
                from shisi.knowledge.retriever import KeywordRetriever
                service._retrievers[character_id] = KeywordRetriever()
                service._chunk_counts[character_id] = 0

        retriever = service._retrievers.get(character_id)
        if retriever is None:
            return {
                "success": False,
                "character_id": character_id,
                "name": name,
                "chunks_added": 0,
                "error": "知识索引初始化失败",
            }

        retriever.add_chunks(chunks)
        service._chunk_counts[character_id] = len(retriever._chunks)
        service.save_index(character_id)

        logger.info(
            "人物资料已导入知识索引: %s → %d 块 (来源: %s)",
            character_id, len(chunks), profile.get("source", "unknown"),
        )

        return {
            "success": True,
            "character_id": character_id,
            "name": name,
            "chunks_added": len(chunks),
            "source": profile.get("source", "unknown"),
            "source_url": profile.get("source_url", ""),
            "fallback_chain": profile.get("fallback_chain", []),
        }


def get_crawler_adapter(service: CharacterKnowledgeService | None = None) -> CharacterCrawlerAdapter:
    """工厂函数，返回单例适配器。"""
    return CharacterCrawlerAdapter(service=service)
