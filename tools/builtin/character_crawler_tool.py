"""人物信息爬取工具 - 用于人设设计（中国可访问版）"""
from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("character_crawler_tool")

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

# ---- 多 UA 轮换池（随机抽取，降低被 ban 概率） ----
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.72 Mobile Safari/537.36",
    # 百度爬虫（部分站点对百度蜘蛛放行）
    "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
]

# ---- 手机端 UA 池（对付移动端站点） ----
_MOBILE_UAS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.72 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

def _random_ua(mobile: bool = False) -> str:
    return random.choice(_MOBILE_UAS if mobile else _USER_AGENTS)

def _headers(referer: str = "", mobile: bool = False) -> dict[str, str]:
    """生成带随机 UA 和常见请求头的 headers"""
    return {
        "User-Agent": _random_ua(mobile),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        **({"Referer": referer} if referer else {}),
    }

_MAX_CONTENT_LEN = 5000
_ALLOWED_SCHEMES = ("https://", "http://")
_BLOCKED_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
_BLOCKED_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.")


class CharacterCrawlerTool(BaseTool):
    """人物信息爬取工具（中国可访问版）"""
    name = "character_crawler"
    description = "爬取人物信息构建知识库。自动降级：维基→百度搜索→可访问来源。"
    permission_level = "admin"
    # 共享 Session（cloudscraper 优先，自动过 Cloudflare/bot 检测）
    _session: requests.Session | None = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            if HAS_CLOUDSCRAPER:
                # cloudscraper 绕过 Cloudflare/百度云防护
                self._session = cloudscraper.create_scraper()
                self._session.headers.update(_headers())
            else:
                s = requests.Session()
                s.headers.update(_headers())
                self._session = s
        return self._session

    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "fetch_wiki",       # 维基百科（中国大陆大概率超时）
                    "fetch_url",        # 通用网页
                    "batch_crawl",      # 批量抓取
                    "search_fetch",     # ★ 搜索 + 抓取（百度搜索→可访问来源）
                    "fetch_person",     # ★ 自动获取人物信息（多重降级）
                ],
                "description": "操作类型",
            },
            "name": {"type": "string", "description": "人物名称"},
            "url": {"type": "string", "description": "网页URL"},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "URL列表"},
            "output_file": {"type": "string", "description": "输出文件路径"},
        },
        "required": ["action"],
    }

    # ── 可访问来源（中国大陆可直连，含丰富人物信息）──
    _ACCESSIBLE_SOURCES = [
        {
            "name": "Olympics.com",
            "url_template": "https://olympics.com/zh/athletes/{slug}",
            "selector": "article",
            "parser": "generic",
        },
        {
            "name": "央视网体育",
            "url_template": "https://sports.cctv.cn/search/?q={name}",
            "selector": "article",
            "parser": "generic",
        },
    ]

    def execute(self, action: str, **kwargs) -> ToolResult:  # type: ignore[override]
        if not HAS_REQUESTS:
            return ToolResult(False, error="请安装: pip install requests beautifulsoup4")
        handlers = {
            "fetch_wiki": lambda: self._fetch_wikipedia(kwargs.get("name")),  # type: ignore[arg-type]
            "fetch_url": lambda: self._fetch_generic(kwargs.get("url")),  # type: ignore[arg-type]
            "batch_crawl": lambda: self._batch_crawl(kwargs.get("urls", [])),
            "search_fetch": lambda: self._search_and_fetch(kwargs.get("name")),  # type: ignore[arg-type]
            "fetch_person": lambda: self._fetch_person_fallback(kwargs.get("name")),  # type: ignore[arg-type]
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

    # ──────────────────────────────
    #  fetch_wiki — 带 Session + UA 轮换
    # ──────────────────────────────
    def _fetch_wikipedia(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(False, error="请提供人物名称")
        # 尝试多个域名（zh.wikipedia.org 被墙时，en.wikipedia.org 某些地区可用）
        domains = ["https://zh.wikipedia.org", "https://en.wikipedia.org"]
        for domain in domains:
            url = f"{domain}/wiki/{quote(name)}"
            try:
                resp = self.session.get(url, timeout=8)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    profile = self._parse_wikipedia(soup)
                    profile["source_url"] = url
                    profile["crawled_at"] = datetime.now(tz=timezone.utc).isoformat()
                    return ToolResult(True, data=profile)
            except requests.RequestException:
                logger.warning("维基域名 %s 不可达，尝试下一个", domain)
                continue
        return ToolResult(False, error="维基百科不可达（中国大陆网络限制），请用 search_fetch 或 fetch_person")

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

    # ──────────────────────────────
    #  search_fetch — 百度搜索 + 尝试抓取
    # ──────────────────────────────
    def _search_and_fetch(self, name: str | None) -> ToolResult:
        """百度搜索人物 → 获取搜索结果片段"""
        if not name:
            return ToolResult(False, error="请提供人物名称")
        result: dict[str, Any] = {
            "source": "baidu_search",
            "source_name": "百度搜索",
            "name": name,
            "query_results": [],
            "content": "",
            "content_length": 0,
        }
        baidu_url = f"https://www.baidu.com/s?wd={quote(name)}"
        r = self._try_fetch(baidu_url, referer="https://www.baidu.com/")
        result["query_results"].append({
            "source": "baidu_search",
            "url": baidu_url,
            "success": r.success,
        })
        if r.success and r.data:
            result["content"] = r.data.get("content", "")
            result["source_url"] = baidu_url
        result["content_length"] = len(result.get("content", ""))
        return ToolResult(bool(result["content"]), data=result)

    # ──────────────────────────────
    #  fetch_person — 多重降级自动获取
    # ──────────────────────────────
    def _fetch_person_fallback(self, name: str | None) -> ToolResult:
        """完整降级链:
           有 cloudscraper: 百度百科(L1) → 维基(L2) → 百度搜索(L3)
           无 cloudscraper: 维基(L1) → 百度搜索(L2) → 报错
        """
        if not name:
            return ToolResult(False, error="请提供人物名称")
        chain: list[str] = []

        # ── 有 cloudscraper 时优先用百度百科（内容最丰富） ──
        if HAS_CLOUDSCRAPER:
            baike_result = self._fetch_baike(name)
            if baike_result.success:
                baike_result.data["fallback_chain"] = ["baike.baidu.com"]
                return baike_result
            chain.append("baike(unreachable)")

        # ── 其次维基百科 ──
        wiki_result = self._fetch_wikipedia(name)
        if wiki_result.success:
            wiki_result.data["fallback_chain"] = chain + ["wikipedia"]
            return wiki_result
        chain.append("wikipedia(unreachable)")

        # ── 最后百度搜索（片段） ──
        search_result = self._search_and_fetch(name)
        if search_result.success:
            search_result.data["fallback_chain"] = chain + ["search_fetch"]
            return search_result
        chain.append("search_fetch(empty)")

        return ToolResult(False, error=f"无法获取 '{name}' 的信息：所有来源均不可达。降级链: {' → '.join(chain)}")

    # ──────────────────────────────
    #  _fetch_baike — 百度百科（cloudscraper 专属）
    # ──────────────────────────────
    def _fetch_baike(self, name: str) -> ToolResult:
        """从百度百科抓取人物信息（需要 cloudscraper 才能绕过云防护）"""
        if not HAS_CLOUDSCRAPER:
            return ToolResult(False, error="需要 cloudscraper")
        baike_url = f"https://baike.baidu.com/item/{quote(name)}"
        try:
            resp = self.session.get(baike_url, timeout=15)
        except Exception as e:
            return ToolResult(False, error=f"请求百度百科失败: {e}")
        if resp.status_code != 200:
            return ToolResult(False, error=f"百度百科 HTTP {resp.status_code}")
        if len(resp.text) < 2000:
            return ToolResult(False, error="百度百科页面内容过短，可能被拦截")

        soup = BeautifulSoup(resp.text, "html.parser")
        profile: dict[str, Any] = {
            "source": "百度百科",
            "source_url": baike_url,
            "name": name,
            "basic_info": {},
            "summary": "",
            "sections": {},
            "content": "",
        }

        # 标题
        title_tag = soup.find("title")
        if title_tag:
            profile["title"] = title_tag.text.strip()

        # 基本信息：查找 basicInfoItem 类名的 dt 标签
        # 百度百科 CSS modules 生成带 hash 的类名（如 basicInfoItem_WtJzh）
        for dt in soup.select('dt[class*="basicInfoItem"]'):
            key = dt.get_text(strip=True).rstrip("：:")
            dd = dt.find_next_sibling('dd')
            if dd:
                val = dd.get_text(strip=True)[:200]
                if key and val:
                    profile["basic_info"][key] = val

        # 正文摘要（J-summary 是百度百科摘要容器）
        summary_el = soup.select_one('.J-summary')
        if summary_el:
            profile["summary"] = summary_el.get_text(strip=True)[:_MAX_CONTENT_LEN]

        # 全部正文
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        profile["content"] = soup.get_text(strip=True)[:_MAX_CONTENT_LEN]
        profile["crawled_at"] = datetime.now(tz=timezone.utc).isoformat()
        return ToolResult(True, data=profile)

    # ──────────────────────────────
    #  URL 校验 & 通用抓取（支持 UA 轮换 + 重定向跟随）
    # ──────────────────────────────
    def _validate_url(self, url: str) -> bool:
        if not any(url.startswith(s) for s in _ALLOWED_SCHEMES):
            return False
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if hostname in _BLOCKED_HOSTS:
            return False
        return not any(hostname.startswith(p) for p in _BLOCKED_PREFIXES)

    def _try_fetch(self, url: str, referer: str = "", mobile: bool = False) -> ToolResult:
        """轻量级抓取（允许重定向，SSRF 防护）"""
        if not self._validate_url(url):
            return ToolResult(False, error="SSRF防护拦截")
        try:
            resp = self.session.get(
                url,
                headers=_headers(referer=referer, mobile=mobile),
                timeout=12,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            return ToolResult(False, error=f"请求失败: {e}")
        if resp.status_code != 200:
            return ToolResult(False, error=f"HTTP {resp.status_code}")
        result: dict[str, Any] = {"url": url, "title": "", "content": ""}
        soup = BeautifulSoup(resp.text, "html.parser")
        title_tag = soup.find("title")
        result["title"] = title_tag.text.strip() if title_tag else ""
        if HAS_TRAFILATURA:
            extracted = trafilatura.extract(resp.text, include_comments=False, output_format="json")
            if extracted:
                data = json.loads(extracted)
                result["content"] = (data.get("text", "") or "")[:_MAX_CONTENT_LEN]
        else:
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            result["content"] = soup.get_text(strip=True)[:_MAX_CONTENT_LEN]
        return ToolResult(True, data=result)

    def _fetch_generic(self, url: str) -> ToolResult:
        """通用网页抓取（SSRF 防护，不跟随重定向）"""
        if not url:
            return ToolResult(False, error="请提供URL")
        if not self._validate_url(url):
            return ToolResult(False, error="URL不合法或指向内网地址（SSRF防护）")
        try:
            resp = self.session.get(
                url,
                headers=_headers(),
                timeout=15,
                allow_redirects=False,
            )
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
