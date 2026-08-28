"""网络人设增强引擎 — 从互联网搜索并完善角色人设知识库。

融合多种内容源：
1. DirectScraper — requests + BeautifulSoup 通用网页抓取（需已知 URL）
2. AgentReachSource — 通过 Agent-Reach 安装的 CLI 工具（bili-cli、mcporter）搜索
3. FirecrawlSource — firecrawl-py SDK（需 FIRECRAWL_API_KEY）
4. StdinPipe — 管道输入模式（与 LLM/ai-first-scraper 配合）


用法：
    # 从 URL 抓取
    python scripts/enrich_persona_web.py --name "上杉绘梨衣" --id 上杉绘梨衣 --urls "https://zh.moegirl.org.cn/上杉绘梨衣"

    # 交互式搜索 + 抓取
    python scripts/enrich_persona_web.py --name "上杉绘梨衣" --id 上杉绘梨衣 --interactive

    # 管道模式（与 LLM 配合）
    echo "人设内容..." | python scripts/enrich_persona_web.py --name "洛十六" --id 洛十六 --pipe

    # 全源搜索（B站 + Firecrawl，自动收集）
    python scripts/enrich_persona_web.py --name "上杉绘梨衣" --id 上杉绘梨衣 --all-sources
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("persona_extractor.web_enricher")

# Agent-Reach 模块路径：通过环境变量配置，避免硬编码本地路径。
# 服务器（Linux）上若未安装 agent-reach，AgentReachChannels 将优雅降级为空通道列表。
_AGENT_REACH_PATH = os.environ.get("AGENT_REACH_PATH")

# ── 数据模型 ──

@dataclass
class RawDocument:
    url: str = ""
    title: str = ""
    content: str = ""
    source: str = ""
    score: float = 0.0

@dataclass
class EnrichResult:
    character_id: str = ""
    character_name: str = ""
    documents_found: int = 0
    documents_processed: int = 0
    chunks_generated: int = 0
    chunks_added: int = 0
    sources_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

# ── 搜索查询模板 ──

_SEARCH_QUERIES = [
    "{name} 角色介绍",
    "{name} 性格分析",
    "{name} 人物设定",
    "{name} 经典台词",
    "{name} 背景故事",
    "{name} 性格特点",
]

_PERSONA_EXTRACTION_PROMPT = """从以下文本中提取关于"{name}"的角色人设信息。
只提取明确与这个人设相关的信息，忽略无关内容。

文本内容：
```
{content}
```

请提取以下维度的信息（每个维度一行，用 | 分隔）：
维度1 - 核心性格：用3-5个关键词描述核心性格（如：温柔、傲娇、病娇）
维度2 - 背景设定：角色的身份、经历、世界观设定
维度3 - 行为模式：角色习惯性的行为方式
维度4 - 说话风格：语气词、口癖、常用表达
维度5 - 经典台词：角色最具代表性的台词

格式要求：
核心性格 | 关键词1、关键词2、关键词3
背景设定 | 一句话描述
行为模式 | 一句话描述
说话风格 | 一句话描述
经典台词 | 如果文本中有经典台词，摘录1-2句

如果某个维度没有相关信息，写"无相关信息"。
"""

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_JINA_READER_BASE = "https://r.jina.ai"


# ══════════════════════════════════════════════
#  内容源 1: 直接 URL 抓取
# ══════════════════════════════════════════════

class DirectScraper:
    """通用网页抓取器 — requests + BeautifulSoup 提取正文。"""

    NAME = "direct_scrape"

    @staticmethod
    def scrape(url: str, timeout: int = 15) -> RawDocument:
        """抓取并提取网页正文内容。"""
        doc = RawDocument(url=url, source=DirectScraper.NAME)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            r.encoding = "utf-8"

            if r.status_code != 200:
                logger.debug("HTTP %d: %s", r.status_code, url)
                return doc

            soup = BeautifulSoup(r.text, "html.parser")

            for tag in soup(["script", "style", "nav", "footer", "header",
                             "aside", "noscript", "iframe", "form", "button"]):
                tag.decompose()

            title_tag = soup.find("title")
            doc.title = title_tag.get_text(strip=True) if title_tag else ""

            main_content = soup.find("article") or soup.find("main") or soup.find(
                class_=re.compile(r"(article|content|main|post|entry|text)")
            )
            target = main_content if main_content else soup

            text = target.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n")
                     if line.strip() and len(line.strip()) > 15]
            doc.content = "\n\n".join(lines[:200])

        except requests.RequestException as e:
            logger.debug("抓取失败 %s: %s", url, e)
        except Exception as e:
            logger.debug("解析失败 %s: %s", url, e)

        return doc


# ══════════════════════════════════════════════
#  内容源 2: Agent-Reach 安装的 CLI 工具
# ══════════════════════════════════════════════
#
# Agent-Reach 本身不是一个搜索 API，它是一个安装器 + 健康检查框架。
# 安装完成后，实际搜索通过 CLI 工具执行：
#   - bili-cli:    `bili search <query> --json`
#   - mcporter:    `mcporter` (Exa 搜索的 MCP 代理)
#   - Jina Reader: `curl https://r.jina.ai/<url>` (通用网页抓取)
#
# 这个类封装对这些 CLI 的 subprocess 调用，而不是依赖 Python 模块。
# ══════════════════════════════════════════════

class AgentReachSource:
    """通过 Agent-Reach 安装的 CLI 工具搜索多平台内容。

    底层工具：
      - bili-cli → `bili search <query> --json`
      - Jina Reader → `https://r.jina.ai/<url>`
      - mcporter → Exa 语义搜索（需配置）
    """

    NAME = "agent_reach"

    def __init__(self):
        self._bili_available = self._check_bili()
        self._mcporter_available = self._check_mcporter()

    @staticmethod
    def _check_bili() -> bool:
        try:
            r = subprocess.run(
                ["bili", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def _check_mcporter() -> bool:
        try:
            r = subprocess.run(
                ["mcporter", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def bili_available(self) -> bool:
        return self._bili_available

    @property
    def mcporter_available(self) -> bool:
        return self._mcporter_available

    # ── Bilibili 搜索 ──

    def search_bilibili(self, query: str, num_results: int = 5) -> list[RawDocument]:
        """通过 bili-cli 搜索 B 站内容。"""
        docs: list[RawDocument] = []
        if not self._bili_available:
            return docs

        try:
            r = subprocess.run(
                ["bili", "search", query, "--json"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                logger.debug("bili search 失败: %s", r.stderr[:200])
                return docs

            data = json.loads(r.stdout)
            results = data.get("data", []) if isinstance(data, dict) else data
            if isinstance(results, dict):
                # bili-cli 的嵌套结构
                for key in ("videos", "bangumi", "live_room", "article", "media"):
                    items = results.get(key, [])
                    for item in items[:num_results]:
                        doc = self._parse_bili_result(item, query)
                        if doc.content:
                            docs.append(doc)
            elif isinstance(results, list):
                for item in results[:num_results]:
                    doc = self._parse_bili_result(item, query)
                    if doc.content:
                        docs.append(doc)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug("Bilibili 搜索异常: %s", e)
        except Exception as e:
            logger.debug("Bilibili 搜索未知错误: %s", e)

        if not docs:
            # 后备: 通过 web 搜索 API 模拟
            docs = self._search_bili_api_fallback(query, num_results)

        return docs

    def _parse_bili_result(self, item: dict, query: str) -> RawDocument:
        """解析 bili-cli 返回的单个结果。"""
        title = item.get("title", "") or ""
        # bili-cli 返回的 title 可能带 XML 标签
        title = re.sub(r"<[^>]+>", "", title).strip()

        desc = item.get("description", "") or item.get("sign", "") or ""
        url = item.get("url", "") or ""
        if not url:
            aid = item.get("aid") or item.get("id", "")
            if aid:
                url = f"https://www.bilibili.com/video/av{aid}"

        bvid = item.get("bvid", "")
        if bvid:
            url = f"https://www.bilibili.com/video/BV{bvid}"

        content_parts = [p for p in [title, desc] if p]
        content = "\n".join(content_parts) if content_parts else title or query

        return RawDocument(
            url=url,
            title=title or query,
            content=content,
            source="bilibili",
        )

    def _search_bili_api_fallback(self, query: str, num_results: int) -> list[RawDocument]:
        """后备: 直接调 Bilibili 搜索 API（无依赖）。"""
        docs: list[RawDocument] = []
        try:
            url = f"https://api.bilibili.com/x/web-interface/search/type?keyword={quote(query)}&search_type=video&page=1"
            r = requests.get(url, headers=_HEADERS, timeout=5)
            if r.status_code != 200:
                return docs
            data = r.json()
            if data.get("code") != 0:
                return docs
            for item in (data.get("data", {}).get("result", []) or [])[:num_results]:
                title = item.get("title", "")
                title = re.sub(r"<[^>]+>", "", title).strip()
                desc = item.get("description", "") or ""
                author = item.get("author", "") or ""
                bvid = item.get("bvid", "") or ""
                url = f"https://www.bilibili.com/video/BV{bvid}" if bvid else ""
                content = "\n".join(p for p in [title, desc, author] if p)
                docs.append(RawDocument(
                    url=url, title=title, content=content, source="bilibili_api",
                ))
        except Exception as e:
            logger.debug("Bilibili API 后备失败: %s", e)
        return docs

    # ── Jina Reader 网页抓取 ──

    def jina_read(self, url: str, timeout: int = 30) -> RawDocument:
        """通过 Jina Reader 读取任意网页（返回干净 Markdown）。"""
        doc = RawDocument(url=url, source="jina_reader")
        try:
            jina_url = f"{_JINA_READER_BASE}/{url}"
            r = requests.get(jina_url, headers={
                "User-Agent": _HEADERS["User-Agent"],
                "Accept": "text/plain",
                "X-Return-Format": "markdown",
            }, timeout=timeout)
            if r.status_code == 200:
                doc.content = r.text
                # 从首行提取 title
                lines = r.text.strip().split("\n")
                if lines:
                    doc.title = lines[0].strip("# ").strip()
            else:
                logger.debug("Jina Reader HTTP %d: %s", r.status_code, url)
        except requests.RequestException as e:
            logger.debug("Jina Reader 失败 %s: %s", url, e)
        return doc

    def jina_search(self, query: str, num_results: int = 3) -> list[RawDocument]:
        """通过 Jina Reader 搜索（搜索端点 r.jina.ai/search）。"""
        docs: list[RawDocument] = []
        if not self._jina_reachable():
            return docs
        try:
            r = requests.get(
                f"{_JINA_READER_BASE}/search",
                headers={
                    "User-Agent": _HEADERS["User-Agent"],
                    "Accept": "text/plain",
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": num_results},
                timeout=10,
            )
            if r.status_code == 200:
                doc = RawDocument(content=r.text, source="jina_search")
                docs.append(doc)
        except Exception as e:
            logger.debug("Jina Reader 搜索失败: %s", e)
        return docs

    @staticmethod
    def _jina_reachable() -> bool:
        """快速探测 Jina Reader 是否可达。"""
        try:
            r = requests.get(_JINA_READER_BASE, headers=_HEADERS, timeout=3)
            return r.status_code < 500
        except requests.RequestException:
            return False

    # ── Exa 搜索（通过 mcporter） ──

    def search_exa(self, query: str, num_results: int = 3) -> list[RawDocument]:
        """通过 mcporter 调用 Exa MCP 搜索。

        需要先配置: mcporter config add exa https://mcp.exa.ai/mcp
        """
        docs: list[RawDocument] = []
        if not self._mcporter_available:
            return docs
        try:
            r = subprocess.run(
                ["mcporter", "call", "exa", "search", query],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode != 0:
                logger.debug("Exa 搜索失败 (exit %d): %s", r.returncode, r.stderr[:200])
                return docs
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = {"results": [{"title": r.stdout[:200], "url": "", "text": r.stdout[:2000]}]}
            results = data if isinstance(data, list) else data.get("results", data.get("data", []))
            for item in results[:num_results]:
                url = (item.get("url") or item.get("link") or "").strip()
                title = (item.get("title") or "").strip()
                content = (item.get("text") or item.get("content") or item.get("snippet") or "").strip()
                if title or content:
                    docs.append(RawDocument(
                        url=url, title=title, content=content or title, source="exa_search",
                    ))
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug("Exa mcporter 异常: %s", e)
        return docs

    # ── 综合搜索 ──

    def search_all(self, query: str, max_per_source: int = 3) -> list[RawDocument]:
        """在所有可用源上搜索。"""
        docs: list[RawDocument] = []
        docs.extend(self.search_bilibili(query, max_per_source))
        # Jina 搜索
        jina_docs = self.jina_search(query, max_per_source)
        if jina_docs and jina_docs[0].content:
            # Jina 返回全文，尝试从中提取 URL
            for line in jina_docs[0].content.split("\n"):
                m = re.match(r"^- \[(.+?)\]\((.+?)\)", line)
                if m and len(docs) < max_per_source:
                    docs.append(RawDocument(
                        title=m.group(1), url=m.group(2),
                        content=m.group(1), source="jina_search",
                    ))
                if len(docs) >= max_per_source * 2:
                    break
        try:
            exa_docs = self.search_exa(query, max_per_source)
            docs.extend(exa_docs)
        except Exception as e:
            logger.debug("Exa 搜索不可用: %s", e)
        return docs


# ══════════════════════════════════════════════
#  内容源 3: Crawl4AI（免授权替代 Firecrawl）
# ══════════════════════════════════════════════

class Crawl4AISource:
    """Crawl4AI 免授权网页抓取与搜索模块。

    不需要 FIRECRAWL API Key，使用 Crawl4AI 的 AsyncWebCrawler。

    兼容 FirecrawlSource 同名接口：`search()` 与 `scrape()`。
    """

    NAME = "crawl4ai"

    def __init__(self):
        self._crawler: Any = None

    @property
    def available(self) -> bool:
        # Crawl4AI 已预装，永远可用（无需 API Key）
        return True

    async def _search_async(self, query: str, max_results: int) -> list[RawDocument]:
        """真正的 async 搜索实现。"""
        import re

        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        CrawlerRunConfig(cache_mode="bypass", verbose=False)
        crawler = AsyncWebCrawler(config=browser_cfg)
        await crawler.start()
        try:
            docs: list[RawDocument] = []
            result = await crawler.arun(f"https://r.jina.ai/search?q={query}&num={max_results}")
            if result.success and result.markdown:
                content = result.markdown
                for line in content.split("\n"):
                    m = re.match(r"^- \[(.+?)\]\((.+?)\)", line.strip())
                    if m:
                        title, url = m.group(1), m.group(2)
                        docs.append(RawDocument(url=url, title=title, content=title, source=self.NAME))
                        if len(docs) >= max_results:
                            break
        except Exception as e:
            logger.debug("Crawl4AI 搜索异常: %s", e)
        finally:
            with contextlib.suppress(Exception):
                await crawler.close()
        return docs

    async def _scrape_async(self, url: str) -> RawDocument:
        """真正的 async 抓取实现。"""
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        browser_cfg = BrowserConfig(headless=True, verbose=False)
        CrawlerRunConfig(cache_mode="bypass", verbose=False)
        crawler = AsyncWebCrawler(config=browser_cfg)
        await crawler.start()
        doc = RawDocument(url=url, source=self.NAME)
        try:
            result = await crawler.arun(url)
            if result.success and result.markdown:
                doc.content = result.markdown
                # 尝试从 markdown 提取 title（首行作为 title）
                lines = result.markdown.split("\n")
                doc.title = lines[0].strip() if lines else url
            else:
                doc.content = ""
                doc.title = url
        except Exception as e:
            logger.warning("Crawl4AI 抓取失败 %s: %s", url, e)
        finally:
            with contextlib.suppress(Exception):
                await crawler.close()
        return doc

    def search(self, query: str, max_results: int = 5) -> list[RawDocument]:
        """同步入口：用 Crawl4AI 搜索并返回 RawDocument 列表。"""
        import asyncio
        try:
            return asyncio.run(self._search_async(query, max_results))
        except RuntimeError:
            # 若已在 loop 中（极少数情况），则创建新 loop
            return asyncio.new_event_loop().run_until_complete(self._search_async(query, max_results))

    def scrape(self, url: str) -> RawDocument:
        """同步入口：抓取单个 URL 并返回 RawDocument。"""
        import asyncio
        try:
            return asyncio.run(self._scrape_async(url))
        except RuntimeError:
            return asyncio.new_event_loop().run_until_complete(self._scrape_async(url))



# ══════════════════════════════════════════════
#  内容源 4: Agent-Reach Python 渠道
# ══════════════════════════════════════════════

class AgentReachChannels:
    """直接使用 Agent-Reach 的 Python channels（无需 CLI subprocess）。

    支持 13 种平台：
      bilibili, web(Jina), exa_search, xiaohongshu, youtube,
      twitter, github, reddit, v2ex, xueqiu, xiaoyuzhou, linkedin, rss

    用法：
        ch = AgentReachChannels()
        docs = ch.search_bilibili("角色名")      # B站搜索
        doc = ch.read_url("https://...")      # Jina Reader
        docs = ch.search_xiaohongshu("角色名") # 小红书
        docs = ch.search_all("角色名")         # 全平台
    """

    NAME = "agent_reach_channels"

    def __init__(self):
        self._channels = {}
        self._init_channels()

    def _init_channels(self):
        import os as _os
        import sys as _sys
        # 优先使用 AGENT_REACH_PATH 环境变量；未设置时优雅降级（不导入）
        _ar_path = _os.environ.get("AGENT_REACH_PATH", "").strip()
        if _ar_path and _ar_path not in _sys.path:
            _sys.path.insert(0, _ar_path)
        try:
            from agent_reach.channels import ALL_CHANNELS
            for c in ALL_CHANNELS:
                self._channels[c.name] = c
        except ImportError:
            # AGENT_REACH_PATH 未配置或导入失败时静默降级
            pass

    @property
    def available_channels(self) -> list[str]:
        return list(self._channels.keys())

    def read_url(self, url: str) -> RawDocument | None:
        """通过 WebChannel (Jina Reader) 读取任意网页。"""
        ch = self._channels.get("web")
        if not ch:
            return None
        try:
            content = ch.read(url)
            if content:
                lines = content.strip().split(chr(10))
                title = lines[0].strip("# ").strip() if lines else ""
                return RawDocument(
                    url=url, title=title, content=content,
                    source="agent_reach_web",
                )
        except Exception as e:
            logger.debug("AgentReach WebChannel 失败 %s: %s", url, e)
        return None

    def search_bilibili(self, query: str, num: int = 5) -> list[RawDocument]:
        """通过 BilibiliChannel 搜索 B站。"""
        docs: list[RawDocument] = []
        ch = self._channels.get("bilibili")
        if not ch or not hasattr(ch, "search"):
            return docs
        try:
            results = ch.search(query, limit=num)
            for item in (results or []):
                title = getattr(item, "title", "") or (item.get("title", "") if isinstance(item, dict) else "")
                url = getattr(item, "url", "") or (item.get("url", "") if isinstance(item, dict) else "")
                content = getattr(item, "content", "") or (item.get("description", "") if isinstance(item, dict) else "")
                if isinstance(title, str):
                    title = re.sub(r"<[^>]+>", "", title).strip()
                docs.append(RawDocument(
                    url=str(url) if url else "",
                    title=str(title) if title else query,
                    content=str(content) if content else title or query,
                    source="agent_reach_bilibili",
                ))
        except Exception as e:
            logger.debug("AgentReach BilibiliChannel 失败: %s", e)
        return docs

    def _search_channel(self, channel_name: str, query: str, num: int = 3) -> list[RawDocument]:
        """通用渠道搜索。"""
        docs: list[RawDocument] = []
        ch = self._channels.get(channel_name)
        if not ch or not hasattr(ch, "search"):
            return docs
        try:
            results = ch.search(query, limit=num)
            for item in (results or []):
                if isinstance(item, dict):
                    title = str(item.get("title", "") or "")
                    url = str(item.get("url", "") or "")
                    content = str(item.get("content", "") or item.get("description", "") or "")
                    docs.append(RawDocument(
                        url=url, title=title or query,
                        content=content or title or query,
                        source=f"agent_reach_{channel_name}",
                    ))
                elif hasattr(item, "title"):
                    docs.append(RawDocument(
                        url=getattr(item, "url", ""),
                        title=getattr(item, "title", query),
                        content=getattr(item, "content", "") or getattr(item, "title", query),
                        source=f"agent_reach_{channel_name}",
                    ))
        except Exception as e:
            logger.debug("AgentReach %s 失败: %s", channel_name, e)
        return docs

    def search_xiaohongshu(self, query: str, num: int = 3) -> list[RawDocument]:
        return self._search_channel("xiaohongshu", query, num)

    def search_exa(self, query: str, num: int = 3) -> list[RawDocument]:
        return self._search_channel("exa_search", query, num)

    def search_youtube(self, query: str, num: int = 3) -> list[RawDocument]:
        return self._search_channel("youtube", query, num)

    def search_all(self, query: str, max_per_source: int = 2) -> list[RawDocument]:
        """在所有可用渠道上搜索。"""
        docs: list[RawDocument] = []
        seen: set[str] = set()
        for name in ["bilibili", "xiaohongshu", "exa_search", "youtube", "v2ex"]:
            if name not in self._channels:
                continue
            for d in (self._search_channel(name, query, max_per_source) if name != "bilibili"
                      else self.search_bilibili(query, max_per_source)):
                key = d.url or d.content[:80]
                if key and key not in seen and d.content:
                    seen.add(key)
                    docs.append(d)
        return docs



# ══════════════════════════════════════════════
#  内容源 4: Agent-Reach Python 渠道


# ══════════════════════════════════════════════
#  主引擎
# ══════════════════════════════════════════════

class WebPersonaEnricher:
    """网络人设增强引擎。

    从互联网搜索角色相关信息，处理后注入角色知识库（BM25 索引），
    让人设在对话中有更多真实素材可调用。

    支持多种内容输入方式：
    - add_url(url): 直接抓取指定 URL
    - add_content(text, source): 直接添加文本内容
    - search_agent_reach(query): 通过 Agent-Reach 安装的 CLI 工具搜索
    - search_firecrawl(query): 通过 Firecrawl 搜索
    - search_all_sources(query): 在所有可用源上搜索
    """

    def __init__(self, knowledge_service=None, llm_gateway=None,
                 firecrawl_api_key: str | None = None):
        self.knowledge_service = knowledge_service
        self.llm = llm_gateway
        self.direct = DirectScraper()
        self.crawl4ai = Crawl4AISource()
        self.agent_reach = AgentReachSource()
        self.ar_channels = AgentReachChannels()
        self._available_sources: list[str] = []
        self._detect_sources()

    def _detect_sources(self) -> None:
        self._available_sources = ["direct_scrape", "crawl4ai"]
        if self.agent_reach.bili_available:
            self._available_sources.append("bilibili (bili-cli)")
            logger.info("bili-cli: 可用")
        if self.agent_reach.mcporter_available:
            self._available_sources.append("exa (mcporter)")
            logger.info("mcporter: 可用")
        self._available_sources.append("jina_reader")
        if self.ar_channels.available_channels:
            for ch_name in self.ar_channels.available_channels[:6]:
                self._available_sources.append(f"ar_{ch_name}")
            logger.info("AgentReach channels: %s", ", ".join(self.ar_channels.available_channels))
        logger.info("可用内容源: %s", ", ".join(self._available_sources))

    def add_url(self, url: str) -> RawDocument | None:
        """抓取并返回单个 URL 内容（含多引擎 fallback）。

        抓取链路: DirectScraper → Jina Reader → Crawl4AI
        """
        doc = self.direct.scrape(url)
        if doc.content and len(doc.content) > 100:
            return doc
        # fallback: Jina Reader
        jina_doc = self.agent_reach.jina_read(url)
        if jina_doc.content and len(jina_doc.content) > 100:
            return jina_doc
        # fallback: Crawl4AI
        fc_doc = self.crawl4ai.scrape(url)
        if fc_doc.content and len(fc_doc.content) > 100:
            return fc_doc
        return doc if doc.content else None

    def add_content(self, text: str, source: str = "pipe_input") -> RawDocument:
        """直接添加文本内容。"""
        return RawDocument(content=text, source=source)

    def search_agent_reach(self, query: str, platform: str = "all") -> list[RawDocument]:
        """通过 Agent-Reach 安装的 CLI 工具搜索。"""
        docs: list[RawDocument] = []
        if platform in ("all", "bilibili"):
            docs.extend(self.agent_reach.search_bilibili(query))
        if platform in ("all", "exa"):
            docs.extend(self.agent_reach.search_exa(query))
        if platform in ("all", "jina"):
            docs.extend(self.agent_reach.jina_search(query))
        return docs

    def search_firecrawl(self, query: str, max_results: int = 5) -> list[RawDocument]:
        """通过 Crawl4AI 搜索。"""
        return self.crawl4ai.search(query, max_results)

    def search_all_sources(self, query: str, max_per_source: int = 3) -> list[RawDocument]:
        """在所有可用源上搜索并合并结果。"""
        docs: list[RawDocument] = []
        seen_urls: set[str] = set()
        seen_content: set[str] = set()

        for doc in self.agent_reach.search_bilibili(query, max_per_source):
            key = doc.url or doc.content[:80]
            if key and key not in seen_urls and key not in seen_content and doc.content:
                seen_urls.add(key)
                seen_content.add(doc.content[:80])
                docs.append(doc)

        if self.crawl4ai.available:
            for doc in self.crawl4ai.search(query, max_results=max_per_source):
                key = doc.url or doc.content[:80]
                if key and key not in seen_urls and key not in seen_content and doc.content:
                    seen_urls.add(key)
                    seen_content.add(doc.content[:80])
                    docs.append(doc)

        # Jina Reader 搜索
        jina_docs = self.agent_reach.jina_search(query, max_per_source)
        for doc in jina_docs:
            key = doc.url or doc.content[:80]
            if key and key not in seen_urls and key not in seen_content and doc.content:
                seen_urls.add(key)
                seen_content.add(doc.content[:80])
                docs.append(doc)

        return docs

    def enrich(
        self,
        character_id: str,
        character_name: str,
        docs: list[RawDocument] | None = None,
        search_queries: list[str] | None = None,
        max_docs: int = 3,
        interactive: bool = False,
    ) -> EnrichResult:
        """执行人设增强。

        Args:
            character_id: 角色 ID
            character_name: 角色名称
            docs: 预先准备好的文档列表（跳过搜索阶段）
            search_queries: 自定义搜索查询
            max_docs: 最大文档数
            interactive: 交互模式
        """
        start = time.time()
        result = EnrichResult(character_id=character_id, character_name=character_name)

        if interactive:
            print(f"\n🔍 处理人设素材 [{character_name}] ...")

        # 1. 收集文档
        if docs is None:
            queries = search_queries or [q.format(name=character_name) for q in _SEARCH_QUERIES]
            docs = self._collect_docs(character_name, queries, max_docs, interactive)
        result.documents_found = len(docs)

        if not docs:
            result.errors.append("未找到文档")
            if interactive:
                print("  ⚠ 未找到文档")
            result.duration_seconds = time.time() - start
            return result

        # 2. 处理为知识块
        chunks = self._process_docs(docs, character_name)
        result.chunks_generated = len(chunks)

        # 3. 写入知识库
        if self.knowledge_service and chunks:
            self.knowledge_service.add_knowledge_chunks(character_id, chunks)
            self.knowledge_service.save_index(character_id)
            result.chunks_added = len(chunks)
            result.sources_used = list(set(d.source for d in docs if d.source))
            if interactive:
                stats = self.knowledge_service.get_stats(character_id)
                print(f"  ✅ 已写入知识库 (+{len(chunks)} 块, 共 {stats.get('total_chunks', 0)} 块)")

        result.duration_seconds = time.time() - start
        return result

    def _collect_docs(
        self,
        character_name: str,
        queries: list[str],
        max_docs: int,
        interactive: bool,
    ) -> list[RawDocument]:
        all_docs: list[RawDocument] = []
        seen: set[str] = set()

        q = queries[0] if queries else character_name

        # Phase 1: Bilibili (bili-cli)
        if len(all_docs) < max_docs:
            if interactive:
                print("  📡 bili-cli (B站搜索) ...")
            for doc in self.agent_reach.search_bilibili(q, max_per_source=3):
                if doc.url and doc.url not in seen and len(all_docs) < max_docs:
                    seen.add(doc.url)
                    all_docs.append(doc)
                    if interactive:
                        print(f"    ✓ [B站] {doc.title[:50]}")

        # Phase 2: Crawl4AI（如果可用）
        if len(all_docs) < max_docs:
            if interactive:
                print("  🕷️ Crawl4AI ...")
            for doc in self.crawl4ai.search(q, max_results=3):
                if doc.url and doc.url not in seen and len(all_docs) < max_docs:
                    seen.add(doc.url)
                    all_docs.append(doc)
                    if interactive:
                        print(f"    ✓ [Crawl4AI] {doc.title[:50] or doc.url[:50]}")

        # Phase 3: Jina Reader
        if len(all_docs) < max_docs:
            if interactive:
                print("  📖 Jina Reader ...")
            jina_docs = self.agent_reach.jina_search(q, max_per_source=3)
            for doc in jina_docs:
                if doc.url and doc.url not in seen and len(all_docs) < max_docs:
                    seen.add(doc.url)
                    all_docs.append(doc)
                    if interactive:
                        print(f"    ✓ [Jina] {doc.title[:50] or doc.url[:50]}")

        # Phase 4: Exa (mcporter)
        if len(all_docs) < max_docs:
            if interactive:
                print("  🔎 Exa (mcporter) ...")
            for doc in self.agent_reach.search_exa(q, max_per_source=2):
                if doc.url and doc.url not in seen and len(all_docs) < max_docs:
                    seen.add(doc.url)
                    all_docs.append(doc)
                    if interactive:
                        print(f"    ✓ [Exa] {doc.title[:50] or doc.url[:50]}")

        return all_docs

    def _process_docs(
        self,
        docs: list[RawDocument],
        character_name: str,
    ) -> list[Any]:
        from shisi.knowledge.retriever import KnowledgeChunk
        chunks: list[KnowledgeChunk] = []
        src_counter: dict[str, int] = {}

        for doc in docs:
            if not doc.content or len(doc.content) < 20:
                continue
            src_counter[doc.source] = src_counter.get(doc.source, 0) + 1
            sid = f"web_{doc.source}_{int(time.time())}_{src_counter[doc.source]}"

            # 尝试 LLM 提取
            extracted = self._llm_extract(doc.content, character_name, doc.url)
            if extracted and len(extracted) > 10:
                chunks.append(KnowledgeChunk(
                    content=extracted, source=f"web_enricher.{doc.source}", source_id=sid))
            else:
                # 后备：按段落分块
                cleaned = self._clean_content(doc.content)
                for para in cleaned.split("\n\n"):
                    para = para.strip()
                    if para and 30 < len(para) < 2000:
                        chunks.append(KnowledgeChunk(
                            content=para, source=f"web_enricher.{doc.source}", source_id=sid))

            if doc.url:
                chunks.append(KnowledgeChunk(
                    content=f"信息来源：{doc.url}",
                    source=f"web_enricher.{doc.source}",
                    source_id=f"{sid}_url",
                ))

        return chunks

    def _llm_extract(self, content: str, character_name: str, url: str) -> str:
        if not self.llm:
            return ""
        prompt = _PERSONA_EXTRACTION_PROMPT.format(name=character_name, content=content[:4000])
        try:
            if hasattr(self.llm, "chat_completion"):
                resp = self.llm.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800, temperature=0.3)
            elif hasattr(self.llm, "generate"):
                resp = self.llm.generate(prompt, max_tokens=800)
            else:
                return ""
            text = resp.get("content", "") or resp.get("text", "") if isinstance(resp, dict) else str(resp)
            return text if text and text != "无相关信息" else ""
        except Exception:
            return ""

    @staticmethod
    def _clean_content(raw: str) -> str:
        patterns = [
            r"导航\s*菜单", r"点击.*关注", r"Copyright.*", r"All Rights Reserved.*",
            r"关注我们", r"免责声明.*", r"分享到", r"举报", r"反馈",
            r"广告投放", r"关于我们", r"联系我们", r"版权声明",
            r"热门评论", r"全部评论", r"发表评论",
        ]
        text = raw
        for pat in patterns:
            text = re.sub(pat, "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
