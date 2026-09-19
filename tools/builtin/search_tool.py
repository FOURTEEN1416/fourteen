"""网页搜索工具 —— Bing 直抓为主后端，DuckDuckGo 为可选副后端。

为什么是 Bing 直抓而不是 DuckDuckGo（2026-09-19 生产实测，勿凭直觉改回）：

| 后端                        | 可靠性 | 单次延迟 | 结果数 |
|-----------------------------|--------|----------|--------|
| `ddgs` 9.16.0（新包）       | 0/5    | 20s×N    | 0      |
| `duckduckgo_search` 8.1.1   | ~1/5   | ~1s      | 3      |
| **Bing 直抓（本模块主路径）** | **5/5** | **0.6s** | **10** |

`ddgs` 9.x 聚合 Google / Brave / Startpage / Yahoo / DDG-html，这些域名在中国大陆
全部不可达 —— 每次搜索会串行撞满 5 个后端 × 20s 超时，等于把一次对话卡死 100 秒。
**不要把 `ddgs` 装回来当主后端。** 旧包 `duckduckgo_search` 8.1.1 内部走 Bing，
在中国大陆能用但会被间歇限流，故保留为副后端。
"""
from __future__ import annotations

import logging
import re
import warnings
from typing import Any
from urllib.parse import quote, urlparse

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("search_tool")

# `duckduckgo_search` 已改名 `ddgs`，导入时会抛 RuntimeWarning —— 抑制它以免刷日志。
with warnings.catch_warnings():
    warnings.simplefilter("ignore", RuntimeWarning)
    try:
        from duckduckgo_search import DDGS

        HAS_DDG = True
    except ImportError:
        HAS_DDG = False

_BING_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# DDG/Bing 解析出的标题偶尔是「域名+URL+面包屑」拼接（实测：
# 'ynu.edu.cnhttps://www.ynu.edu.cn› xxgk › sbyd.htm'）。判定：以 http(s):// 开头
# 或同时含 '›' 与 'http'。命中时退化为「域名 — 路径末段」，至少可读。
_BREADCRUMB_TITLE_RE = re.compile(r"^\S*https?://")


def _clean_title(title: str, href: str) -> str:
    """修掉面包屑式标题；正常标题原样返回。"""
    t = (title or "").strip()
    if not t:
        return ""
    if _BREADCRUMB_TITLE_RE.match(t) or ("›" in t and "http" in t):
        try:
            parsed = urlparse(href or "")
            host = (parsed.hostname or "").removeprefix("www.")
            tail = (parsed.path or "").rstrip("/").rsplit("/", 1)[-1]
            if host:
                return f"{host} — {tail}" if tail else host
        except ValueError:
            pass
        return href or t
    return t


class SearchTool(BaseTool):
    name = "search"
    description = "搜索网页信息（Bing 为主，DuckDuckGo 为辅）"
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
        """如实报告后端可用性。

        2026-09-19 之前此方法无条件返回 `available: True` —— 与「投递链路六层谎报」
        同类：调用方分不清「真能搜到」和「只剩降级路径」。现在返回 primary + 各后端状态。
        """
        try:
            import requests  # noqa: F401
            from bs4 import BeautifulSoup  # noqa: F401

            bing_ok = True
        except ImportError:
            bing_ok = False

        available = bing_ok or HAS_DDG
        error = "" if available else "缺少 requests/beautifulsoup4，且无 DuckDuckGo 兜底"
        return {
            "available": available,
            "error": error,
            "primary": "bing" if bing_ok else ("duckduckgo" if HAS_DDG else ""),
            "backends": {"bing": bing_ok, "duckduckgo": HAS_DDG},
        }

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(False, error="query is required")

        errors: list[str] = []

        bing = self._bing_search(query, max_results)
        if bing.success:
            return bing
        errors.append(f"bing({bing.error})")

        if HAS_DDG:
            ddg = self._ddg_search(query, max_results)
            if ddg.success:
                return ddg
            errors.append(f"duckduckgo({ddg.error})")

        return ToolResult(False, error="搜索暂不可用：" + "；".join(errors))

    def _bing_search(self, query: str, max_results: int = 5) -> ToolResult:
        """Bing 网页直抓（无需 API Key）。中国大陆可靠性最好：实测 5/5、0.6s、10 条。"""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError as e:
            return ToolResult(False, error=f"缺少依赖: {e}")

        url = f"https://www.bing.com/search?q={quote(query)}"
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": _BING_UA})
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001 - requests 异常族 + 网络异常
            return ToolResult(False, error=f"请求失败: {type(e).__name__}")

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict[str, str]] = []
        for li in soup.select("li.b_algo"):
            if len(items) >= max_results:
                break
            # ⚠️ 必须走 `h2 a`。li 内**第一个** <a> 是 Bing 的站点面包屑
            #    （文本形如 'ynu.edu.cnhttps://www.ynu.edu.cn› xxgk › sbyd.htm'），
            #    历史代码用 li.find("a") 取到它，导致 title 全是面包屑。
            anchor = li.select_one("h2 a") or li.select_one("h2")
            href = ""
            if anchor is not None and anchor.name == "a":
                href = anchor.get("href", "") or ""
            title = _clean_title(anchor.get_text(strip=True) if anchor else "", href)
            p = li.select_one("p")
            body = p.get_text(strip=True) if p else ""
            if not (title or body):
                continue
            items.append({"title": title, "body": body, "href": href})

        if items:
            return ToolResult(True, data=items)
        return ToolResult(False, error="未解析到结果")

    def _ddg_search(self, query: str, max_results: int = 5) -> ToolResult:
        """DuckDuckGo 副后端（内部走 Bing）。限流是常态，失败只记一行警告。"""
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
        except Exception as e:  # noqa: BLE001 - DDG 抛自有异常类型
            # 不打全量 traceback：限流属常态，刷栈会淹没真正的故障。
            logger.warning(
                "DuckDuckGo 不可用（降级）：%s: %s", type(e).__name__, str(e)[:120]
            )
            return ToolResult(False, error=type(e).__name__)

        items = [
            {
                "title": _clean_title(r.get("title", ""), r.get("href", "")),
                "body": r.get("body", ""),
                "href": r.get("href", ""),
            }
            for r in raw
        ]
        items = [i for i in items if i["title"] or i["body"]]
        if items:
            return ToolResult(True, data=items)
        return ToolResult(False, error="未返回结果")
