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
        # unregister 的实例保留在这里：旧实现直接 pop 丢引用，导致
        # /api/tools/{name}/toggle 关闭后永远无法再开启（get 返回 None → 404）
        self._disabled: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._disabled.pop(tool.name, None)
        self._tools[tool.name] = tool
        logger.info("Tool registered: %s (permission: %s)", tool.name, tool.permission_level)

    def unregister(self, name: str):
        tool = self._tools.pop(name, None)
        if tool is not None:
            self._disabled[name] = tool

    def reenable(self, name: str) -> bool:
        """恢复此前被 unregister 的工具；成功返回 True。"""
        tool = self._disabled.pop(name, None)
        if tool is None:
            return False
        self._tools[name] = tool
        logger.info("Tool re-enabled: %s", name)
        return True

    @property
    def disabled_names(self) -> list[str]:
        return list(self._disabled.keys())

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
        from contextvars import copy_context

        fut = self._get_executor().submit(copy_context().run, fn)
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

        # 重试策略（2026-09-21 审查统一）：
        #   - 只对 retry_tools（网络型工具）开放，总尝试次数 = retry_count + 1；
        #   - **只有异常/超时才算"没跑完"，值得重试**；`ToolResult(success=False)`
        #     是工具内部 fallback 链（weather→wttr.in、search→多后端+断路器）
        #     已定论的结果，再重试只是重复烧时间；
        #   - 旧实现把重试劈成两处：dispatch 只在**异常**时重试，而
        #     `_execute_with_retry` 又按 `result.success` 重试、其超时分支永远
        #     不可达（dispatch 已先捕获 ToolTimeoutError）——两套语义都记不完指标。
        max_attempts = self.retry_count + 1 if tool_name in self.retry_tools else 1
        start = time.perf_counter()
        last_error = "tool_execution_failed"
        for attempt in range(max_attempts):
            if attempt:
                time.sleep(0.5 * attempt)
            try:
                result = self._call_with_timeout(lambda: tool.execute(**arguments))
            except ToolTimeoutError as e:
                last_error = f"tool_timeout: {e}"
                logger.warning(
                    "工具执行超时: %s — %s（第 %d/%d 次）",
                    tool_name, e, attempt + 1, max_attempts,
                )
                continue
            except Exception:  # noqa: BLE001
                last_error = "tool_execution_failed"
                logger.exception(
                    "工具执行失败: %s（第 %d/%d 次）", tool_name, attempt + 1, max_attempts,
                )
                continue
            # 指标只记最终结果（旧实现为失败的首跳记一次失败，重试成功却不再记录）
            self._record(tool_name, start, result.success)
            return result

        self._record(tool_name, start, False)
        if max_attempts > 1:
            return ToolResult(False, error=f"{last_error}（已重试 {max_attempts - 1} 次）")
        return ToolResult(False, error=last_error)

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

