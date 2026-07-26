from __future__ import annotations

import logging
from typing import Any

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("search_tool")

try:
    from duckduckgo_search import DDGS
    HAS_DDG = True
except ImportError:
    HAS_DDG = False


class SearchTool(BaseTool):
    name = "search"
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

    def health_check(self) -> dict[str, Any]:
        if not HAS_DDG:
            return {"available": True, "error": "", "note": "使用公开搜索接口降级"}
        return {"available": True, "error": ""}

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(False, error="query is required")
        if HAS_DDG:
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results))
                    if results:
                        simplified = [
                            {"title": r.get("title", ""), "body": r.get("body", ""), "href": r.get("href", "")}
                            for r in results
                        ]
                        return ToolResult(True, data=simplified)
            except Exception as e:
                logger.exception("DuckDuckGo 搜索失败，尝试降级: %s", e)
        return self._fallback_search(query, max_results)

    def _fallback_search(self, query: str, max_results: int = 5) -> ToolResult:
        """无 DDGS 时的公开搜索降级：Bing 网页快照（无需 API Key）。"""
        try:
            from urllib.parse import quote

            import requests
            url = f"https://www.bing.com/search?q={quote(query)}"
            resp = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            resp.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            items = []
            for li in soup.select("li.b_algo")[:max_results]:
                a = li.find("a")
                title = a.get_text(strip=True) if a else ""
                href = a.get("href", "") if a else ""
                body = ""
                p = li.find("p")
                if p:
                    body = p.get_text(strip=True)
                if title or body:
                    items.append({"title": title, "body": body, "href": href})
            if items:
                return ToolResult(True, data=items)
            return ToolResult(False, error="未找到搜索结果")
        except Exception as e:
            logger.exception("搜索降级失败")
            return ToolResult(False, error=f"搜索暂不可用: {e}")
