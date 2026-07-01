"""补充工具 — 补齐前端仪表盘展示的工具名称"""
from __future__ import annotations

import json
import logging
from typing import Any

from tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger("extra_tools")


try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


_MAX_CONTENT_LEN = 5000


class MemoryTool(BaseTool):
    """长期记忆查询"""
    name = "memory"
    description = "查询与用户相关的长期记忆事实"
    permission_level = "public"
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
        try:
            if hasattr(self._sm, "search_facts"):
                facts = self._sm.search_facts(query)
            elif hasattr(self._sm, "get_facts"):
                facts = self._sm.get_facts(category=None, limit=limit)
            else:
                return ToolResult(False, error="Memory query not supported")
            return ToolResult(True, data={"query": query, "facts": facts[:limit]})
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

    def execute(self, url: str = "", **kwargs) -> ToolResult:
        if not url:
            return ToolResult(False, error="url is required")
        if not HAS_REQUESTS:
            return ToolResult(False, error="缺少依赖: pip install requests beautifulsoup4")
        try:
            resp = requests.get(url, timeout=15, headers={
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
    """AI 图片创作（占位）"""
    name = "image_gen"
    description = "根据提示词生成图片（需额外配置图片生成服务）"
    permission_level = "public"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "图片提示词",
            },
        },
        "required": ["prompt"],
    }

    def health_check(self) -> dict[str, Any]:
        return {
            "available": False,
            "error": "图片生成服务未配置。请在后台集成图像模型（如 Stability、DALL·E、Agnes-AI）后使用。",
            "config_hint": "设置 IMAGE_GEN_PROVIDER / IMAGE_GEN_API_KEY 环境变量",
        }

    def execute(self, prompt: str = "", **kwargs) -> ToolResult:
        return ToolResult(
            False,
            error="图片生成服务未配置。请在后台集成图像模型（如 Stability、DALL·E、Agnes-AI）后使用。",
        )


class SchedulerTool(BaseTool):
    """定时提醒与调度"""
    name = "scheduler"
    description = "设置一次性提醒或日程安排"
    permission_level = "friend"
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
        try:
            reminder_id = self._sm.add_reminder(content, trigger_time)
            return ToolResult(True, data={
                "reminder_id": reminder_id,
                "content": content,
                "trigger_time": trigger_time,
                "message": f"已安排：{content}",
            })
        except Exception:
            logger.exception("日程安排失败")
            return ToolResult(False, error="scheduler_failed")
