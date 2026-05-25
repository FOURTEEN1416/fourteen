"""
搜索采集器 — 使用DuckDuckGo进行定向搜索
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("search_collector")

try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False
    logger.warning("duckduckgo-search not installed, search disabled")


@dataclass
class SearchResult:
    """搜索结果"""
    title: str
    link: str
    snippet: str
    source: str = "duckduckgo"


class SearchCollector:
    """
    搜索采集器

    使用DuckDuckGo进行免费搜索，无需API Key
    """

    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        """
        执行搜索

        Args:
            query: 搜索关键词
            max_results: 最大结果数

        Returns:
            搜索结果列表
        """
        if not HAS_DDGS:
            logger.warning("DuckDuckGo search not available")
            return []

        max_results = max_results or self.max_results

        try:
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(SearchResult(
                        title=r.get("title", ""),
                        link=r.get("href", ""),
                        snippet=r.get("body", ""),
                    ))

            logger.info("Search '%s' returned %d results", query, len(results))
            return results

        except Exception as e:  # noqa: BLE001

            logger.error("Search failed: %s", e)
            return []

    def search_person(self, name: str, keywords: list[str] | None = None) -> list[SearchResult]:
        """
        搜索人物相关信息

        Args:
            name: 人物名称
            keywords: 额外关键词

        Returns:
            搜索结果
        """
        queries = [f"{name} 最新动态", f"{name} 新闻"]
        if keywords:
            for kw in keywords[:2]:
                queries.append(f"{name} {kw}")

        all_results = []
        seen_links = set()

        for query in queries:
            results = self.search(query, max_results=3)
            for r in results:
                if r.link not in seen_links:
                    all_results.append(r)
                    seen_links.add(r.link)

        return all_results[:10]

    def to_dict(self, result: SearchResult) -> dict[str, Any]:
        """转换为字典"""
        return {
            "title": result.title,
            "link": result.link,
            "snippet": result.snippet,
            "source": result.source,
        }
