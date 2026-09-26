"""人物信息爬取工具 - 用于人设设计（中国可访问版）"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tools.base_tool import BaseTool, ToolResult
from tools.url_guard import UrlBlockedError, assert_public_http_url, fetch_guarded

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

# ── 正文有效性阈值（2026-09-19 新增）──
# 为什么必须有：百度百科对爬虫返回反爬壳页，HTML 体积很大（实测 95 KB）能通过
# 原有的 `len(resp.text) < 2000` 校验，但正文抽取后只剩「百度百科」4 个字符。
# 旧代码据此判定 success=True，使 _fetch_person_fallback 的降级链在第 1 步就短路，
# 真正可用的 search_fetch（实测 1212 字符）永远不会被调用 —— 表现为
# 「语义上成功、数据为空」。体积校验 ≠ 内容校验。
_MIN_USEFUL_CONTENT = 200
_MIN_USEFUL_SUMMARY = 60
_ANTI_BOT_MARKERS = (
    "百度安全验证", "请输入验证码", "网络不给力", "请稍后再试",
    "访问过于频繁", "点击进行验证", "系统检测到",
)

# ── 维基不可达记忆（2026-09-19 新增）──
# 中国大陆网络下 zh/en 维基均被持续阻断，每次调用都要白等 2×timeout。生产实测：
# 整条降级链 33.2s，其中维基占 32.0s（97%），而真正可用的 search_fetch 只要 1.1s。
# 这么大的耗时会让对话内工具调用直接撞上「处理超时」。故把失败记下来短期跳过。
_WIKI_TIMEOUT = 4.0          # 单域名超时（原 8s）
# ⚠️ 实测：cloudscraper 会把实际等待放大到约 2×（配 4s 时两域名共耗 16.0s）——
#    排障时不要以为 timeout 参数没生效。
_WIKI_MEMO_TTL = 1800.0      # 不可达记忆有效期（秒）。大陆对维基的封锁是持续性的，
                             # 600s 会让 16s 白等每 10 分钟重演一次，故放宽到 30 分钟。
_WIKI_MEMO: dict[str, float] = {}
_WIKI_MEMO_LOCK = threading.Lock()


class CharacterCrawlerTool(BaseTool):
    """人物信息爬取工具（中国可访问版）"""
    name = "character_card"
    description = "爬取人物信息构建知识库。自动降级：百度百科→维基→百度搜索。"
    permission_level = "friend"

    # 受控知识存储默认根（类级常量：conftest 可全局重定向进沙箱）
    _DEFAULT_STORAGE_ROOT = (
        Path(__file__).resolve().parents[2] / "data" / "character_crawler"
    )

    def __init__(self, storage_root: str | Path | None = None):
        self._session: requests.Session | None = None
        self._session_lock = threading.Lock()
        # W6 缺陷 C 根治：抓取结果统一落受控知识存储（固定根目录），
        # 不再向模型暴露 output_file 任意路径写。
        if storage_root is not None:
            self._storage_root = Path(storage_root).resolve()
        else:
            self._storage_root = Path(self._DEFAULT_STORAGE_ROOT)

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            with self._session_lock:
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

    def health_check(self) -> dict[str, Any]:
        if not HAS_REQUESTS:
            return {"available": False, "error": "请安装: pip install requests beautifulsoup4"}
        return {
            "available": True,
            "error": "",
            "features": {
                "cloudscraper": HAS_CLOUDSCRAPER,
                "trafilatura": HAS_TRAFILATURA,
            },
        }

    # ── 内容有效性 & 结构归一 ──
    @staticmethod
    def _is_usable_profile(profile: dict[str, Any]) -> tuple[bool, str]:
        """判定抓取结果是否真带到了人物信息。

        Returns:
            (是否可用, 不可用原因)
        """
        content = (profile.get("content") or "").strip()
        summary = (profile.get("summary") or "").strip()
        basic = profile.get("basic_info") or {}

        for marker in _ANTI_BOT_MARKERS:
            if marker in content:
                return False, f"命中反爬特征「{marker}」"
        if basic or len(summary) >= _MIN_USEFUL_SUMMARY or len(content) >= _MIN_USEFUL_CONTENT:
            return True, ""
        return False, f"正文 {len(content)} 字符/摘要 {len(summary)} 字符/基础信息 0 项，均低于阈值"

    @staticmethod
    def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """统一各来源的返回结构。

        各来源产出不一致：`_fetch_baike` 有 content+summary，`_fetch_wikipedia` 只有
        summary，`_search_and_fetch` 只有 content。调用方若固定读 `content`，
        维基来源必然读到空 —— 与「百度壳页」同属一类「成功但无数据」。
        """
        if not isinstance(profile, dict):
            return profile
        content = profile.get("content") or ""
        summary = profile.get("summary") or ""
        if not content and summary:
            profile["content"] = summary[:_MAX_CONTENT_LEN]
        if not summary and content:
            profile["summary"] = content[:_MAX_CONTENT_LEN]
        profile.setdefault("basic_info", {})
        profile["content_length"] = len(profile.get("content") or "")
        return profile

    def execute(self, action: str, **kwargs) -> ToolResult:  # type: ignore[override]
        if not HAS_REQUESTS:
            return ToolResult(False, error="请安装: pip install requests beautifulsoup4")
        # W6 缺陷 C：模型不再拥有任何文件路径控制权。保留参数名是为了给
        # 仍传该参数的调用方一个明确的拒绝语义，而非静默忽略让其误以为已保存。
        if kwargs.get("output_file"):
            return ToolResult(
                False,
                error="output_file 参数已移除：结果统一写入受控知识存储"
                "（data/character_crawler/），路径由服务端固定，不接受模型指定",
            )
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
            if result.success and isinstance(result.data, dict):
                result.data = self._normalize_profile(result.data)
                saved_name = str(
                    kwargs.get("name") or result.data.get("name") or action
                )
                result.data["saved_to"] = self._save_to_knowledge_store(
                    result.data, saved_name
                )
            return result
        except Exception as e:
            logger.exception("人物爬取失败: %s", e)
            return ToolResult(False, error="character_crawl_failed")

    # ── 受控知识存储（W6 缺陷 C 根治）──────────────────────────

    @staticmethod
    def _slugify(name: str) -> str:
        """文件名消毒：路径分隔符/盘符/UNC/.. 全部落入下划线，杜绝逃逸。"""
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(name or ""), flags=re.UNICODE)
        return (slug.strip("._-") or "profile")[:80]

    def _save_to_knowledge_store(self, data: Any, name: str) -> str:
        """写入固定根目录；返回落盘绝对路径。任何逃逸直接拒绝。"""
        root = self._storage_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{self._slugify(name)}.json"
        if dest.parent != root:
            # 双保险：slug 已消毒，此处仍强制根内（根目录本身被 symlink
            # 替换时 resolve 已收敛到真实路径，判定依然成立）。
            raise ValueError(f"受控存储路径逃逸: {dest}")
        fd, tmp_name = tempfile.mkstemp(
            prefix=".crawl_", suffix=".tmp", dir=str(root)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, dest)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise
        return str(dest)

    # ──────────────────────────────
    #  fetch_wiki — 带 Session + UA 轮换
    # ──────────────────────────────
    def _fetch_wikipedia(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(False, error="请提供人物名称")
        # 尝试多个域名（zh.wikipedia.org 被墙时，en.wikipedia.org 某些地区可用）
        domains = ["https://zh.wikipedia.org", "https://en.wikipedia.org"]
        pending = [d for d in domains if not self._wiki_domain_blocked(d)]
        if not pending:
            return ToolResult(False, error="维基百科近期不可达（已记忆，跳过）")
        for domain in pending:
            url = f"{domain}/wiki/{quote(name)}"
            try:
                resp = fetch_guarded(self.session, url, timeout=_WIKI_TIMEOUT)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    profile = self._parse_wikipedia(soup)
                    usable, reason = self._is_usable_profile(profile)
                    if not usable:
                        logger.warning("维基百科 %s 页面无有效内容（%s），尝试下一个域名", domain, reason)
                        continue
                    profile["source_url"] = url
                    profile["crawled_at"] = datetime.now(tz=timezone.utc).isoformat()
                    return ToolResult(True, data=profile)
            except UrlBlockedError:
                # 固定公网域名被守卫拒绝（异常网络环境）→ 与不可达同样记忆跳过
                self._mark_wiki_domain_blocked(domain)
                continue
            except requests.RequestException:
                self._mark_wiki_domain_blocked(domain)
                logger.warning("维基域名 %s 不可达（已记忆 %ds），尝试下一个", domain, int(_WIKI_MEMO_TTL))
                continue
        return ToolResult(False, error="维基百科不可达（中国大陆网络限制），请用 search_fetch 或 fetch_person")

    @staticmethod
    def _wiki_domain_blocked(domain: str) -> bool:
        with _WIKI_MEMO_LOCK:
            return time.monotonic() < _WIKI_MEMO.get(domain, 0.0)

    @staticmethod
    def _mark_wiki_domain_blocked(domain: str) -> None:
        with _WIKI_MEMO_LOCK:
            _WIKI_MEMO[domain] = time.monotonic() + _WIKI_MEMO_TTL

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
        if result["content"]:
            return ToolResult(True, data=result)
        return ToolResult(False, error="百度搜索未返回可解析正文")

    # ──────────────────────────────
    #  fetch_person — 多重降级自动获取
    # ──────────────────────────────
    def _fetch_person_fallback(self, name: str | None) -> ToolResult:
        """完整降级链:
           有 cloudscraper: 百度百科(L1) → 维基(L2) → 百度搜索(L3)
           无 cloudscraper: 维基(L1) → 百度搜索(L2) → 报错

        ⚠️ 每一层都必须先过 `_is_usable_profile` 才算成功。否则百度反爬壳页会让
        链条在第 1 步短路（2026-09-19 实测：终态 content 仅 4 字符，而本可用的
        search_fetch 能拿到 1212 字符）。
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
            chain.append(f"baike({(baike_result.error or 'failed')[:60]})")

        # ── 其次维基百科 ──
        wiki_result = self._fetch_wikipedia(name)
        if wiki_result.success:
            wiki_result.data["fallback_chain"] = [*chain, "wikipedia"]
            return wiki_result
        chain.append(f"wikipedia({(wiki_result.error or 'failed')[:60]})")

        # ── 最后百度搜索（片段） ──
        search_result = self._search_and_fetch(name)
        if search_result.success:
            search_result.data["fallback_chain"] = [*chain, "search_fetch"]
            return search_result
        chain.append(f"search_fetch({(search_result.error or 'empty')[:60]})")

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
            resp = fetch_guarded(self.session, baike_url, timeout=15)
        except UrlBlockedError as e:
            return ToolResult(False, error=f"SSRF防护拦截: {e}")
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

        # ⚠️ 体积校验（上面的 len(resp.text) < 2000）不足以防反爬：实测百度壳页
        #    HTML 95 KB 却只有 4 个字符正文。必须校验**抽取后**的内容。
        usable, reason = self._is_usable_profile(profile)
        if not usable:
            logger.warning("百度百科未返回有效内容（%s）", reason)
            return ToolResult(False, error=f"百度百科返回反爬/空壳页面（{reason}）")
        return ToolResult(True, data=profile)

    # ──────────────────────────────
    #  URL 校验 & 通用抓取（tools/url_guard 统一 SSRF 守卫：DNS 解析级
    #  判定 + 手动逐跳重定向校验。W6 缺陷 D 根治前这里只做字面前缀匹配）
    # ──────────────────────────────
    def _validate_url(self, url: str) -> bool:
        return assert_public_http_url(url) is None

    def _try_fetch(self, url: str, referer: str = "", mobile: bool = False) -> ToolResult:
        """轻量级抓取（逐跳校验的受控重定向）"""
        try:
            resp = fetch_guarded(
                self.session,
                url,
                headers=_headers(referer=referer, mobile=mobile),
                timeout=12,
            )
        except UrlBlockedError as e:
            return ToolResult(False, error=f"SSRF防护拦截: {e}")
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
        """通用网页抓取（DNS 级 SSRF 防护，重定向逐跳校验）"""
        if not url:
            return ToolResult(False, error="请提供URL")
        try:
            resp = fetch_guarded(self.session, url, headers=_headers(), timeout=15)
        except UrlBlockedError as e:
            return ToolResult(False, error=f"URL不合法或指向内网地址（SSRF防护）: {e}")
        except requests.RequestException as e:
            return ToolResult(False, error=f"请求失败: {e}")
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
