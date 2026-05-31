from __future__ import annotations

import logging

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("search_tool")

try:
    from duckduckgo_search import DDGS
    HAS_DDG = True
except ImportError:
    HAS_DDG = False


class SearchTool(BaseTool):
    name = "web_search"
    description = "使用DuckDuckGo搜索引擎搜索网页信息"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "description": "最大结果数，默认5",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> ToolResult:
        if not HAS_DDG:
            return ToolResult(False, error="duckduckgo-search not installed")
        if not query:
            return ToolResult(False, error="query is required")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                simplified = [
                    {"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                    for r in results
                ]
                return ToolResult(True, data=simplified)
        except Exception as e:
            logger.exception("Search failed: %s", e)
            return ToolResult(False, error="search_failed")
