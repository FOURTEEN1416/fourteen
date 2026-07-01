"""角色爬虫与知识库索引适配器。

职责：
1. 角色创建/导入后自动为角色卡数据建索引；
2. 调用 character_crawler 工具补全网络公开信息；
3. 将爬虫结果统一写入 CharacterKnowledgeService 单例，供对话时 RAG 检索。
"""

from __future__ import annotations

import logging
from typing import Any

from shisi.knowledge.character_knowledge_service import get_knowledge_service
from shisi.knowledge.retriever import KnowledgeChunk

logger = logging.getLogger("shisi.knowledge.crawler_adapter")


def _split_text(text: str, max_len: int = 500, overlap: int = 50) -> list[str]:
    """将长文本切分为固定长度、带重叠的短块。"""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_len
        chunks.append(text[start:end])
        start = end - overlap
        if start >= len(text):
            break
    return chunks


def _profile_to_chunks(profile: dict[str, Any]) -> list[KnowledgeChunk]:
    """将爬虫返回的人物 profile 转换为知识块。"""
    chunks: list[KnowledgeChunk] = []

    name = profile.get("name", "")
    if name:
        chunks.append(KnowledgeChunk(
            content=f"角色名：{name}",
            source="crawler_name",
        ))

    basic = profile.get("basic_info", {})
    for k, v in basic.items():
        if str(v).strip():
            chunks.append(KnowledgeChunk(
                content=f"{k}：{str(v).strip()}",
                source="crawler_basic_info",
            ))

    summary = profile.get("summary", "")
    if summary:
        for para in str(summary).split("\n"):
            if para.strip():
                chunks.append(KnowledgeChunk(
                    content=para.strip(),
                    source="crawler_summary",
                ))

    content = profile.get("content", "")
    if content and str(content) != str(summary):
        for para in str(content).split("\n"):
            para = para.strip()
            if para and len(para) > 10:
                for piece in _split_text(para, max_len=500, overlap=50):
                    chunks.append(KnowledgeChunk(
                        content=piece,
                        source="crawler_content",
                    ))

    source_url = profile.get("source_url", "")
    if source_url:
        chunks.append(KnowledgeChunk(
            content=f"信息来源：{source_url}",
            source="crawler_source_url",
        ))

    return chunks


class CharacterCrawlerAdapter:
    """协调爬虫与知识索引，供角色路由在后台触发。"""

    def __init__(self) -> None:
        self._tool = None
        self._tool_error = None

    def _get_tool(self):
        """惰性加载爬虫工具；首次调用时初始化。"""
        if self._tool is None and self._tool_error is None:
            try:
                from tools.builtin.character_crawler_tool import CharacterCrawlerTool
                self._tool = CharacterCrawlerTool()
            except Exception as e:  # noqa: BLE001
                self._tool_error = f"爬虫工具不可用: {e}"
                logger.warning(self._tool_error)
        return self._tool

    def crawl_and_index(
        self,
        character_id: str,
        name: str,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为角色建立知识索引，并尝试网络爬虫补全。

        返回摘要字典，便于路由端记录日志；所有异常均已捕获，不会阻塞主流程。
        """
        tool = self._get_tool()
        if tool is None:
            return {
                "character_id": character_id,
                "success": False,
                "error": self._tool_error or "爬虫工具不可用",
                "indexed": False,
                "crawled": False,
            }

        service = get_knowledge_service()

        # 1. 先为已有角色卡数据建索引
        if card:
            try:
                self._index_card(service, character_id, card)
            except Exception:  # noqa: BLE001
                logger.warning("角色卡 %s 索引失败（非阻塞）", character_id, exc_info=True)

        # 2. 尝试爬虫补全
        crawl_ok = False
        crawl_error = None
        source = None
        if name and name.strip():
            try:
                result = tool.execute(action="fetch_person", name=name.strip())
                if result.success and result.data:
                    crawl_ok = True
                    source = result.data.get("source", "unknown")
                    chunks = _profile_to_chunks(result.data)
                    if chunks:
                        service.ensure_index(character_id)
                        service.add_knowledge_chunks(character_id, chunks)
                        service.save_index(character_id)
                        logger.info(
                            "角色 '%s' (%s) 爬虫知识已入库: %d 块, 来源=%s",
                            name, character_id, len(chunks), source,
                        )
                else:
                    crawl_error = result.error or "crawler_empty"
                    logger.warning("角色 '%s' 爬虫补全失败: %s", name, crawl_error)
            except Exception:  # noqa: BLE001
                crawl_error = "crawler_exception"
                logger.warning("角色 '%s' 爬虫补全异常（非阻塞）", name, exc_info=True)

        stats = service.get_stats(character_id)
        return {
            "character_id": character_id,
            "success": True,
            "indexed": stats["indexed"],
            "total_chunks": stats["total_chunks"],
            "crawled": crawl_ok,
            "crawl_error": crawl_error,
            "source": source,
        }

    def _index_card(
        self,
        service,
        character_id: str,
        card: dict[str, Any],
    ) -> None:
        """从 app 角色卡格式提取知识块并建索引。"""
        chunks: list[KnowledgeChunk] = []

        name = card.get("name", "")
        if name:
            chunks.append(KnowledgeChunk(
                content=f"她的名字是{name}，你可以称呼她{name}。",
                source="card_name",
            ))

        if card.get("description"):
            for para in str(card["description"]).split("\n"):
                if para.strip():
                    chunks.append(KnowledgeChunk(
                        content=para.strip(),
                        source="card_description",
                    ))

        for anchor in card.get("core_anchors", []):
            if anchor and str(anchor).strip():
                chunks.append(KnowledgeChunk(
                    content=f"核心锚点：{str(anchor).strip()}",
                    source="card_anchors",
                ))

        personality = card.get("personality", {})
        if isinstance(personality, dict):
            for k, v in personality.items():
                chunks.append(KnowledgeChunk(
                    content=f"性格维度 {k}：{v}",
                    source="card_personality",
                ))
        elif isinstance(personality, str) and personality.strip():
            for para in personality.split("\n"):
                if para.strip():
                    chunks.append(KnowledgeChunk(
                        content=para.strip(),
                        source="card_personality",
                    ))

        style = card.get("speaking_style", {})
        if isinstance(style, dict):
            for k, v in style.items():
                if k in ("catchphrases", "口头禅") and isinstance(v, list):
                    for cp in v:
                        if str(cp).strip():
                            chunks.append(KnowledgeChunk(
                                content=f"口头禅：{str(cp).strip()}",
                                source="card_catchphrase",
                            ))
                else:
                    chunks.append(KnowledgeChunk(
                        content=f"说话风格 {k}：{v}",
                        source="card_speaking_style",
                    ))
        elif isinstance(style, str) and style.strip():
            chunks.append(KnowledgeChunk(content=style.strip(), source="card_speaking_style"))

        if card.get("scenario"):
            chunks.append(KnowledgeChunk(
                content=f"场景设定：{card['scenario']}",
                source="card_scenario",
            ))

        if chunks:
            service.ensure_index(character_id)
            service.add_knowledge_chunks(character_id, chunks)
            service.save_index(character_id)
            logger.info("角色卡 %s 已索引: %d 块", character_id, len(chunks))


_crawler_adapter: CharacterCrawlerAdapter | None = None


def get_crawler_adapter() -> CharacterCrawlerAdapter:
    """返回全局唯一的 CharacterCrawlerAdapter 实例。"""
    global _crawler_adapter
    if _crawler_adapter is None:
        _crawler_adapter = CharacterCrawlerAdapter()
    return _crawler_adapter
