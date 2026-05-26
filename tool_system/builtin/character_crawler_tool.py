"""人物信息爬取工具 - 用于人设设计"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from tool_system.base_tool import BaseTool, ToolResult

logger = logging.getLogger("character_crawler_tool")

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

_MAX_CONTENT_LEN = 5000
_ALLOWED_SCHEMES = ("https://", "http://")
_BLOCKED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
_BLOCKED_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.")


class CharacterCrawlerTool(BaseTool):
    """人物信息爬取工具"""
    name = "character_crawler"
    description = "爬取人物信息构建知识库。支持维基百科（推荐）、通用网页。"
    permission_level = "admin"
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["fetch_wiki", "fetch_url", "batch_crawl"],
                "description": "操作类型",
            },
            "name": {"type": "string", "description": "人物名称（用于维基百科）"},
            "url": {"type": "string", "description": "网页URL"},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "URL列表"},
            "output_file": {"type": "string", "description": "输出文件路径"},
        },
        "required": ["action"],
    }

    def execute(self, action: str, **kwargs) -> ToolResult:  # type: ignore[override]
        if not HAS_REQUESTS:
            return ToolResult(False, error="请安装: pip install requests beautifulsoup4")
        handlers = {
            "fetch_wiki": lambda: self._fetch_wikipedia(kwargs.get("name")),  # type: ignore[arg-type]
            "fetch_url": lambda: self._fetch_generic(kwargs.get("url")),  # type: ignore[arg-type]
            "batch_crawl": lambda: self._batch_crawl(kwargs.get("urls", [])),
        }
        handler = handlers.get(action)
        if not handler:
            return ToolResult(False, error=f"未知操作: {action}")
        try:
            result = handler()
            if kwargs.get("output_file") and result.success:
                self._save(result.data, kwargs["output_file"])
            return result
        except Exception as e:
            logger.exception("人物爬取失败: %s", e)
            return ToolResult(False, error="character_crawl_failed")

    def _fetch_wikipedia(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(False, error="请提供人物名称")
        url = f"https://zh.wikipedia.org/wiki/{quote(name)}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException as e:
            return ToolResult(False, error=f"请求失败: {e}")
        if resp.status_code != 200:
            return ToolResult(False, error=f"请求失败: HTTP {resp.status_code}")
        soup = BeautifulSoup(resp.text, "html.parser")
        profile = self._parse_wikipedia(soup)
        profile["source_url"] = url
        profile["crawled_at"] = datetime.now(tz=timezone.utc).isoformat()
        return ToolResult(True, data=profile)

    def _parse_wikipedia(self, soup) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "source": "维基百科",
            "name": "",
            "basic_info": {},
            "summary": "",
            "sections": {},
        }
        h1 = soup.find("h1")
        profile["name"] = h1.text.strip() if h1 else ""
        for row in soup.select(".infobox tr"):
            th = row.find("th")
            td = row.find("td")
            if th and td:
                key = th.get_text(strip=True)
                val = td.get_text(strip=True)
                if key and val:
                    profile["basic_info"][key] = val[:200]
        paragraphs = soup.select("#mw-content-text .mw-parser-output > p")
        summary_parts = [p.get_text(strip=True) for p in paragraphs[:3] if p.get_text(strip=True)]
        profile["summary"] = "\n".join(summary_parts)[:_MAX_CONTENT_LEN]
        for h2 in soup.select("#mw-content-text h2"):
            span = h2.find("span", class_="mw-headline")
            if span:
                profile["sections"][span.text] = ""
        return profile

    def _validate_url(self, url: str) -> bool:
        if not any(url.startswith(s) for s in _ALLOWED_SCHEMES):
            return False
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname in _BLOCKED_HOSTS:
            return False
        return not any(hostname.startswith(p) for p in _BLOCKED_PREFIXES)

    def _fetch_generic(self, url: str) -> ToolResult:
        if not url:
            return ToolResult(False, error="请提供URL")
        if not self._validate_url(url):
            return ToolResult(False, error="URL不合法或指向内网地址（SSRF防护）")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=False)
        except requests.RequestException as e:
            return ToolResult(False, error=f"请求失败: {e}")
        if resp.status_code in (301, 302, 303, 307, 308):
            return ToolResult(False, error="重定向被阻止（安全策略）")
        if resp.status_code != 200:
            return ToolResult(False, error=f"请求失败: HTTP {resp.status_code}")
        result: dict[str, Any] = {"url": url, "title": ""}
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        result["title"] = title_tag.text.strip() if title_tag else ""
        if HAS_TRAFILATURA:
            extracted = trafilatura.extract(resp.text, include_comments=False, output_format="json")
            if extracted:
                data = json.loads(extracted)
                result["content"] = (data.get("text", "") or "")[:_MAX_CONTENT_LEN]
        else:
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            result["content"] = soup.get_text(strip=True)[:_MAX_CONTENT_LEN]
        return ToolResult(True, data=result)

    def _batch_crawl(self, urls: list[str]) -> ToolResult:
        results = []
        for url in urls:
            try:
                r = self._fetch_generic(url)
                if r.success:
                    results.append(r.data)
            except Exception as e:  # noqa: BLE001
                logger.error("抓取失败 %s: %s", url, e)
        return ToolResult(True, data={"total": len(urls), "success": len(results), "profiles": results})

    @staticmethod
    def _save(data: Any, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class CharacterKnowledgeImporter:
    """人物知识导入器"""

    def __init__(self, structured_memory=None):
        self.memory = structured_memory

    def import_profile(self, profile: dict[str, Any]) -> bool:
        name = profile.get("name", "未知")
        if self.memory:
            self.memory.store_semantic(
                subject=name,
                predicate="基础信息",
                object=json.dumps(profile.get("basic_info", {}), ensure_ascii=False),
                source=profile.get("source_url", ""),
            )
            self.memory.store_semantic(
                subject=name,
                predicate="人物简介",
                object=profile.get("summary", ""),
                source=profile.get("source_url", ""),
            )
        logger.info("人物 '%s' 已导入知识库", name)
        return True

    def generate_character_prompt(self, profile: dict[str, Any]) -> str:
        name = profile.get("name", "角色")
        basic = profile.get("basic_info", {})
        summary = profile.get("summary", "")
        return f"""你现在扮演{name}。

【基本信息】
{json.dumps(basic, ensure_ascii=False, indent=2)}

【人物简介】
{summary}

【回复要求】
1. 用第一人称"我"回复
2. 符合人物身份和性格
3. 可以引用人物的经历和观点
4. 保持自然、真实的对话风格

请以{name}的身份开始对话。"""
