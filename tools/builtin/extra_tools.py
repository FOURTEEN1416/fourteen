"""补充工具 — 补齐前端仪表盘展示的工具名称"""
from __future__ import annotations

import logging
import os
from contextlib import suppress
from typing import Any

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("extra_tools")


try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


_MAX_CONTENT_LEN = 5000


class MemoryTool(BaseTool):
    """长期记忆查询"""
    name = "memory"
    description = "查询与用户相关的长期记忆事实"
    permission_level = "public"
    # 会话归属只信编排器服务端注入的 _meta；kwargs 里的 user_key/session_id
    # 是 LLM 可自填参数，旧实现直接采用 → 指定他人键即可跨用户读事实
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要查询的记忆关键词",
            },
            "limit": {
                "type": "integer",
                "description": "最大返回条数，默认5",
            },
        },
        "required": ["query"],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def health_check(self) -> dict[str, Any]:
        return {"available": bool(self._sm), "error": "" if self._sm else "Memory system not available"}

    def execute(self, query: str = "", limit: int = 5, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(False, error="query is required")
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        if not session_key:
            # 无归属即拒绝：回落全库检索等于跨用户读记忆
            return ToolResult(False, error="missing_session_key")
        try:
            if hasattr(self._sm, "user_key_from_session"):
                user_key = self._sm.user_key_from_session(session_key)
            else:
                user_key = session_key
            facts: list = []
            searched = False
            if hasattr(self._sm, "search_facts"):
                try:
                    facts = self._sm.search_facts(query, user_key=user_key)
                    searched = True
                except TypeError:
                    searched = False  # 实现不支持 user_key 形参，不得走无过滤检索
            if not searched and hasattr(self._sm, "get_facts"):
                try:
                    facts = self._sm.get_facts(
                        category=None, limit=limit, user_key=user_key
                    )
                except TypeError:
                    # 无用户维度的旧实现：返回结果也必须按键过滤
                    facts = [
                        f for f in (self._sm.get_facts(category=None, limit=200) or [])
                        if isinstance(f, dict) and f.get("user_key") == user_key
                    ]
            elif not searched:
                return ToolResult(False, error="memory query not supported")
            facts = [f for f in facts[:200] if isinstance(f, dict)]
            ids = [f.get("id") for f in facts[:limit] if f.get("id")]
            if ids and hasattr(self._sm, "increment_fact_access"):
                try:
                    self._sm.increment_fact_access(ids)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("increment_fact_access failed: %s", exc)
            return ToolResult(True, data={"query": query, "facts": facts[:limit], "user_key": user_key})
        except Exception:
            logger.exception("记忆查询失败")
            return ToolResult(False, error="memory_query_failed")


class WebSummaryTool(BaseTool):
    """URL 内容摘要"""
    name = "web_summary"
    description = "抓取指定 URL 的网页内容并返回摘要文本"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的网页 URL",
            },
        },
        "required": ["url"],
    }

    def health_check(self) -> dict[str, Any]:
        if not HAS_REQUESTS:
            return {"available": False, "error": "缺少依赖: pip install requests beautifulsoup4"}
        return {"available": True, "error": ""}

    @staticmethod
    def _check_url_ssrf(url: str) -> str | None:
        """SSRF 闸门：仅 http(s)，且主机不得解析到内网/回环/链路本地等保留地址。"""
        import ipaddress
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "仅允许 http/https URL"
        host = parsed.hostname or ""
        if not host:
            return "URL 缺少主机名"
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
        except socket.gaierror:
            return "主机名无法解析"
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                return "地址解析异常"
            ip = getattr(ip, "ipv4_mapped", None) or ip  # ::ffff:127.0.0.1 这类映射地址不得绕过回环检查
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return "禁止访问内网/保留地址"
        return None

    def execute(self, url: str = "", **kwargs) -> ToolResult:
        if not url:
            return ToolResult(False, error="url is required")
        if not HAS_REQUESTS:
            return ToolResult(False, error="缺少依赖: pip install requests beautifulsoup4")
        ssrf_err = self._check_url_ssrf(url)
        if ssrf_err:
            logger.warning("web_summary 拒绝可疑 URL: %s (%s)", url, ssrf_err)
            return ToolResult(False, error=f"web_summary_blocked: {ssrf_err}")
        try:
            # allow_redirects=False：重定向目标同样可能被解析到内网，
            # 逐跳放行等于绕过上面的闸门
            resp = requests.get(url, timeout=15, allow_redirects=False, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if not location:
                    return ToolResult(False, error="web_summary_failed: 重定向缺少 location")
                from urllib.parse import urljoin
                target = urljoin(url, location)
                ssrf_err = self._check_url_ssrf(target)
                if ssrf_err:
                    logger.warning("web_summary 拒绝重定向目标: %s (%s)", target, ssrf_err)
                    return ToolResult(False, error=f"web_summary_blocked: 重定向被拒（{ssrf_err}）")
                resp = requests.get(target, timeout=15, allow_redirects=False, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.find("title")
            title_text = title.get_text(strip=True) if title else ""
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [line for line in text.splitlines() if line.strip()]
            content = "\n".join(lines)[:_MAX_CONTENT_LEN]
            return ToolResult(True, data={
                "url": url,
                "title": title_text,
                "content": content,
            })
        except Exception as e:
            logger.exception("网页摘要失败")
            return ToolResult(False, error=f"web_summary_failed: {e}")


class ImageGenTool(BaseTool):
    """AI 图片创作：支持 Agnes-AI 等 OpenAI 兼容图像接口。"""
    name = "image_gen"
    description = "根据提示词生成图片"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图片提示词",
            },
            "size": {
                "type": "string",
                "description": "图片尺寸，如 1024x1024、1024x768",
            },
        },
        "required": ["prompt"],
    }

    _DEFAULT_CONFIG: dict[str, dict[str, str]] = {
        "agnes": {
            "base_url": "https://apihub.agnes-ai.com/v1",
            "model": "agnes-image-2.1-flash",
            "size": "1024x1024",
        },
    }

    def _get_config(self) -> dict[str, str] | None:
        provider = os.environ.get("IMAGE_GEN_PROVIDER", "").strip().lower()
        api_key = os.environ.get("IMAGE_GEN_API_KEY", "").strip()
        if not provider or not api_key:
            return None

        defaults = self._DEFAULT_CONFIG.get(provider, {})
        base_url = os.environ.get("IMAGE_GEN_API_BASE", defaults.get("base_url", "")).rstrip("/")
        model = os.environ.get("IMAGE_GEN_MODEL", defaults.get("model", ""))
        size = os.environ.get("IMAGE_GEN_SIZE", defaults.get("size", "1024x1024"))
        if not base_url or not model:
            return None
        return {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "size": size,
        }

    def health_check(self) -> dict[str, Any]:
        cfg = self._get_config()
        if not cfg:
            return {
                "available": False,
                "error": "图片生成服务未配置。请在后台集成图像模型（如 Agnes-AI）后使用。",
                "config_hint": "设置 IMAGE_GEN_PROVIDER=agnes 和 IMAGE_GEN_API_KEY 环境变量",
            }
        return {
            "available": True,
            "error": "",
            "provider": cfg["provider"],
            "model": cfg["model"],
        }

    def execute(self, prompt: str = "", size: str = "", **kwargs) -> ToolResult:
        if not prompt:
            return ToolResult(False, error="prompt is required")
        cfg = self._get_config()
        if not cfg:
            return ToolResult(
                False,
                error="图片生成服务未配置。请设置 IMAGE_GEN_PROVIDER 和 IMAGE_GEN_API_KEY。",
            )
        if not HAS_HTTPX:
            return ToolResult(False, error="缺少依赖: pip install httpx")

        target_size = size or cfg["size"]
        url = f"{cfg['base_url']}/images/generations"
        payload = {
            "model": cfg["model"],
            "prompt": prompt,
            "n": 1,
            "size": target_size,
        }
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            image_url = data.get("data", [{}])[0].get("url")
            if not image_url:
                return ToolResult(False, error="图片生成接口未返回 URL", data=data)
            return ToolResult(True, data={
                "url": image_url,
                "prompt": prompt,
                "size": target_size,
                "model": cfg["model"],
            })
        except httpx.HTTPStatusError as e:
            logger.exception("图片生成接口返回错误")
            detail = ""
            with suppress(Exception):
                detail = e.response.text[:500]
            return ToolResult(False, error=f"image_gen_http_error: {e.response.status_code} {detail}")
        except Exception as e:
            logger.exception("图片生成失败")
            return ToolResult(False, error=f"image_gen_failed: {e}")


class SchedulerTool(BaseTool):
    """定时提醒与调度"""
    name = "scheduler"
    description = "设置一次性提醒或日程安排"
    # 2026-09-21：与 set_reminder 对齐，托付类工具不设亲密度门槛
    permission_level = "public"
    # 归属由编排器服务端注入（_meta）。旧实现不带 session_key 落库 →
    # 无投递目标，轮询永远不会发它（静默丢提醒）
    wants_call_context = True
    parameters_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "提醒内容",
            },
            "trigger_time": {
                "type": "string",
                "description": "触发时间，格式 YYYY-MM-DD HH:MM",
            },
        },
        "required": ["content"],
    }

    def __init__(self, structured_memory=None):
        self._sm = structured_memory

    def health_check(self) -> dict[str, Any]:
        return {"available": bool(self._sm), "error": "" if self._sm else "Memory system not available"}

    def execute(self, content: str = "", trigger_time: str | None = None, **kwargs) -> ToolResult:
        if not content:
            return ToolResult(False, error="content is required")
        if not self._sm:
            return ToolResult(False, error="Memory system not available")
        meta = kwargs.pop("_meta", None) if isinstance(kwargs.get("_meta"), dict) else None
        session_key = str((meta or {}).get("session_key") or "")
        user_id = (meta or {}).get("user_id")
        if not session_key:
            return ToolResult(False, error="missing_session_key")
        try:
            reminder_id = self._sm.add_reminder(
                content, trigger_time, session_key=session_key, user_id=user_id
            )
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": trigger_time,
                "deliver_to": session_key,
                "message": f"已安排：{content}",
            })
        except Exception:
            logger.exception("日程安排失败")
            return ToolResult(False, error="scheduler_failed")
