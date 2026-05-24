from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("tool_system")


class ToolResult:
    def __init__(self, success: bool, data: Any = None, error: str = ""):
        self.success = success
        self.data = data
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        result = {"success": self.success}
        if self.data is not None:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        return result

    def to_fc_result(self) -> str:
        if self.success:
            return json.dumps(self.data, ensure_ascii=False) if not isinstance(self.data, str) else self.data
        return json.dumps({"error": self.error}, ensure_ascii=False)


class BaseTool:
    name: str = ""
    description: str = ""
    permission_level: str = "public"
    parameters_schema: dict[str, Any] = {}

    def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def to_openai_fc_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool
        logger.info("Tool registered: %s (permission: %s)", tool.name, tool.permission_level)

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_all_schemas(self) -> list[dict]:
        return [tool.to_openai_fc_schema() for tool in self._tools.values()]

    def get_tools_by_permission(self, min_affinity: int = 0) -> list[dict]:
        permission_affinity = {"public": 0, "friend": 2, "intimate": 6, "admin": 99}
        schemas = []
        for tool in self._tools.values():
            required = permission_affinity.get(tool.permission_level, 0)
            if min_affinity >= required:
                schemas.append(tool.to_openai_fc_schema())
        return schemas

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def health_check_all(self) -> dict[str, dict[str, Any]]:
        results = {}
        for name, tool in self._tools.items():
            status = {"available": True, "error": ""}
            if hasattr(tool, "_plugin") and getattr(tool, "_plugin", None) is None:
                status = {"available": False, "error": "plugin not loaded"}
            elif hasattr(tool, "_sm") and getattr(tool, "_sm", None) is None:
                status = {"available": False, "error": "memory system not initialized"}
            results[name] = status
        return results


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, timeout: float = 10.0,
                 rate_limit_per_minute: int = 3,
                 retry_count: int = 1, retry_tools: set | None = None):
        self.registry = registry
        self.timeout = timeout
        self.rate_limit = rate_limit_per_minute
        self._call_times: dict[str, list[float]] = {}
        self.retry_count = retry_count
        self.retry_tools = retry_tools or {"web_search", "get_weather"}

    def dispatch(self, tool_name: str, arguments: dict[str, Any],
                 affinity_level: int = 0, trace_id: str = "") -> ToolResult:
        tool = self.registry.get(tool_name)
        if not tool:
            return ToolResult(False, error=f"Tool not found: {tool_name}")

        if not self._check_permission(tool, affinity_level):
            return ToolResult(False, error=f"Permission denied for tool: {tool_name}")

        if not self._check_rate_limit(tool_name):
            return ToolResult(False, error=f"Rate limit exceeded for tool: {tool_name}")

        start = time.perf_counter()
        try:
            result = tool.execute(**arguments)
            duration_ms = (time.perf_counter() - start) * 1000
            from observability.metrics import record_tool_call
            record_tool_call(tool_name, duration_ms / 1000, result.success)
            return result
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            from observability.metrics import record_tool_call
            record_tool_call(tool_name, duration_ms / 1000, False)
            if tool_name in self.retry_tools and self.retry_count > 0:
                return self._execute_with_retry(tool, arguments, tool_name)
            logger.exception("工具执行失败: %s", tool_name)
            return ToolResult(False, error="tool_execution_failed")

    def _check_permission(self, tool: BaseTool, affinity: int) -> bool:
        permission_affinity = {"public": 0, "friend": 2, "intimate": 6, "admin": 99}
        required = permission_affinity.get(tool.permission_level, 0)
        return affinity >= required

    def _check_rate_limit(self, tool_name: str) -> bool:
        now = time.time()
        times = self._call_times.get(tool_name, [])
        times = [t for t in times if now - t < 60]
        self._call_times[tool_name] = times
        if len(times) >= self.rate_limit:
            return False
        times.append(now)
        return True

    def _execute_with_retry(self, tool: BaseTool, arguments: dict[str, Any],
                            tool_name: str) -> ToolResult:
        for attempt in range(self.retry_count):
            time.sleep(0.5 * (attempt + 1))
            try:
                result = tool.execute(**arguments)
                if result.success:
                    return result
            except Exception as e:
                logger.warning("工具重试 %s (%d/%d) 失败: %s", tool_name, attempt + 1, self.retry_count, e)
        return ToolResult(False, error=f"Tool {tool_name} failed after {self.retry_count} retries")
