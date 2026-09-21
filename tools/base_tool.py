from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeout
from typing import Any, ClassVar

logger = logging.getLogger("tool_system")


class ToolTimeoutError(Exception):
    """工具执行超过 self.timeout —— 与"工具抛异常"区分，单独记错误并降级。"""



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
            result["error"] = self.error  # type: ignore[assignment]
        return result

    def to_fc_result(self) -> str:
        if self.success:
            if isinstance(self.data, str):
                return self.data
            return json.dumps(self.data, ensure_ascii=False)
        return json.dumps({"error": self.error}, ensure_ascii=False)


class BaseTool:
    name: str = ""
    description: str = ""
    permission_level: str = "public"
    parameters_schema: ClassVar[dict[str, Any]] = {}

    def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        """返回工具健康状态；子类可覆盖以检查依赖/配置。"""
        status = {"available": True, "error": ""}
        if hasattr(self, "_plugin") and getattr(self, "_plugin", None) is None:
            status = {"available": False, "error": "plugin not loaded"}
        elif hasattr(self, "_sm") and getattr(self, "_sm", None) is None:
            status = {"available": False, "error": "memory system not initialized"}
        return status

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
            try:
                results[name] = tool.health_check()
            except Exception:
                logger.exception("工具 %s 健康检查失败", name)
                results[name] = {"available": False, "error": "health_check_failed"}
        return results


class ToolDispatcher:
    # 工具执行专用**有界**线程池（P1-11）：挂死工具只占这里的槽，
    # 不再占满全局默认 to_thread 池（后者被记忆/画像/统计等所有 to_thread 共用）。
    _EXECUTOR_MAX_WORKERS = 16

    def __init__(self, registry: ToolRegistry, timeout: float = 10.0,
                 rate_limit_per_minute: int = 3,
                 retry_count: int = 1, retry_tools: set | None = None):
        self.registry = registry
        self.timeout = timeout
        self.rate_limit = rate_limit_per_minute
        # 限速键 = (tool_name, caller_id)：按调用者隔离，第 N 个用户不再被
        # 前 N 个用户耗尽同一工具的配额（旧实现按工具名全局共享 → 用户互耗）
        self._call_times: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()
        self.retry_count = retry_count
        self.retry_tools = retry_tools or {"search", "weather"}

    def _get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._executor_lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._EXECUTOR_MAX_WORKERS,
                        thread_name_prefix="tool_exec",
                    )
        return self._executor

    def _call_with_timeout(self, fn: Callable[[], ToolResult]) -> ToolResult:
        """在专用有界池执行 fn 并消费 self.timeout。

        - timeout<=0：直接内联执行（保留旧的无超时行为）。
        - 超时抛 ToolTimeoutError（底层线程无法强杀，但已返回控制权给调用方，
          且挂死线程被限制在本池 16 槽内，不再饿死全局 to_thread）。
        """
        if self.timeout <= 0:
            return fn()
        fut = self._get_executor().submit(fn)
        try:
            return fut.result(timeout=self.timeout)
        except _FutTimeout as e:
            fut.cancel()
            raise ToolTimeoutError(f"工具执行超过 {self.timeout}s") from e

    def close(self) -> None:
        """释放工具执行线程池（编排器 shutdown 时调用）。"""
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def dispatch(self, tool_name: str, arguments: dict[str, Any],
                 affinity_level: int = 0, trace_id: str = "",
                 caller_id: str = "") -> ToolResult:
        tool = self.registry.get(tool_name)
        if not tool:
            return ToolResult(False, error=f"Tool not found: {tool_name}")

        if not self._check_permission(tool, affinity_level):
            return ToolResult(False, error=f"Permission denied for tool: {tool_name}")

        if not self._check_rate_limit(tool_name, caller_id):
            return ToolResult(False, error=f"Rate limit exceeded for tool: {tool_name}")

        start = time.perf_counter()
        try:
            result = self._call_with_timeout(lambda: tool.execute(**arguments))
            self._record(tool_name, start, result.success)
            return result
        except ToolTimeoutError as e:
            self._record(tool_name, start, False)
            logger.warning("工具执行超时: %s — %s", tool_name, e)
            return ToolResult(False, error=f"tool_timeout: {e}")
        except Exception:
            self._record(tool_name, start, False)
            if tool_name in self.retry_tools and self.retry_count > 0:
                return self._execute_with_retry(tool, arguments, tool_name)
            logger.exception("工具执行失败: %s", tool_name)
            return ToolResult(False, error="tool_execution_failed")

    @staticmethod
    def _record(tool_name: str, start: float, success: bool) -> None:
        duration_ms = (time.perf_counter() - start) * 1000
        try:
            from observability.metrics import record_tool_call
            record_tool_call(tool_name, duration_ms / 1000, success)
        except Exception:  # noqa: BLE001
            pass

    def _check_permission(self, tool: BaseTool, affinity: int) -> bool:
        permission_affinity = {"public": 0, "friend": 2, "intimate": 6, "admin": 99}
        required = permission_affinity.get(tool.permission_level, 0)
        return affinity >= required

    def _check_rate_limit(self, tool_name: str, caller_id: str = "") -> bool:
        now = time.time()
        key = (tool_name, caller_id or "__global__")
        with self._lock:
            times = [t for t in self._call_times.get(key, []) if now - t < 60]
            if len(times) >= self.rate_limit:
                self._call_times[key] = times
                return False
            times.append(now)
            self._call_times[key] = times
            # 顺带回收已空闲的键，避免长跑进程里 per-caller 键无界增长
            if len(self._call_times) > 512:
                for k in [k for k, v in self._call_times.items()
                          if not v or now - v[-1] >= 60]:
                    self._call_times.pop(k, None)
            return True

    def _execute_with_retry(self, tool: BaseTool, arguments: dict[str, Any],
                            tool_name: str) -> ToolResult:
        for attempt in range(self.retry_count):
            time.sleep(0.5 * (attempt + 1))
            try:
                result = self._call_with_timeout(lambda: tool.execute(**arguments))
                if result.success:
                    return result
            except ToolTimeoutError as e:
                logger.warning("工具重试 %s (%d/%d) 超时: %s", tool_name, attempt + 1, self.retry_count, e)
            except Exception as e:  # noqa: BLE001
                logger.warning("工具重试 %s (%d/%d) 失败: %s", tool_name, attempt + 1, self.retry_count, e)
        return ToolResult(False, error=f"Tool {tool_name} failed after {self.retry_count} retries")
