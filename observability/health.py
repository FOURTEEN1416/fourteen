from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

logger = logging.getLogger("health")


class HealthStatus(str, Enum):
    """共享健康枚举 — /api/ready 与所有健康检查器的唯一状态口径。

    W10 修复：/api/ready 旧判据 `status != 'error'` 会把 degraded/unhealthy
    全部放行；现改为各方共用本枚举（healthy / degraded / unhealthy）。
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


_STATUS_RANK = {HealthStatus.HEALTHY.value: 0, HealthStatus.DEGRADED.value: 1, HealthStatus.UNHEALTHY.value: 2}


def normalize_status(value: Any) -> str:
    """任意健康状态值 → 共享枚举值；未知值按 unhealthy（fail-closed）。"""
    text = str(value or "").strip().lower()
    return text if text in _STATUS_RANK else HealthStatus.UNHEALTHY.value


def worst_status(a: str, b: str) -> str:
    a_n, b_n = normalize_status(a), normalize_status(b)
    return a_n if _STATUS_RANK[a_n] >= _STATUS_RANK[b_n] else b_n


def evaluate_result(result: dict[str, Any]) -> str:
    """单条检查结果 → 共享枚举。

    - connected/available 为 False → degraded（组件在但失联）
    - 显式 degraded 标记 → degraded
    - 其余 → healthy（异常由调用方判 unhealthy）
    """
    if not result.get("connected", result.get("available", True)):
        return HealthStatus.DEGRADED.value
    if result.get("degraded"):
        return HealthStatus.DEGRADED.value
    return HealthStatus.HEALTHY.value


class HealthChecker:
    def __init__(self):
        self._checks: dict[str, Callable] = {}
        self._async_checks: dict[str, Callable[[], Awaitable[dict]]] = {}

    def register(self, name: str, check_fn: Callable[[], dict]):
        """注册同步健康检查函数"""
        self._checks[name] = check_fn

    def register_async(self, name: str, check_fn: Callable[[], Awaitable[dict]]):
        """注册异步健康检查函数（会真正探测后端）"""
        self._async_checks[name] = check_fn

    def check(self) -> dict[str, Any]:
        """同步健康检查（基于缓存/属性）"""
        results = {}
        overall = HealthStatus.HEALTHY.value
        for name, fn in self._checks.items():
            try:
                result = fn()
                results[name] = result
                overall = worst_status(overall, evaluate_result(result))
            except Exception:
                logger.exception("健康检查异常: %s", name)
                results[name] = {"connected": False, "error": "health_check_failed"}
                overall = worst_status(overall, HealthStatus.UNHEALTHY.value)
        return {
            "status": overall,
            "checks": results,
        }

    async def async_check(self) -> dict[str, Any]:
        """异步健康检查（真实探测后端，超时保护）"""
        results = {}
        overall = HealthStatus.HEALTHY.value

        # 同步检查
        for name, fn in self._checks.items():
            try:
                result = fn()
                results[name] = result
                overall = worst_status(overall, evaluate_result(result))
            except Exception:
                logger.exception("同步健康检查异常: %s", name)
                results[name] = {"connected": False, "error": "health_check_failed"}
                overall = worst_status(overall, HealthStatus.UNHEALTHY.value)

        # 异步检查（带超时，全部并行）
        if self._async_checks:
            async def _safe_check(name: str, fn: Callable) -> tuple[str, dict]:
                try:
                    result = await asyncio.wait_for(fn(), timeout=5.0)
                    return name, result
                except asyncio.TimeoutError:
                    return name, {"connected": False, "error": "timeout"}
                except Exception:
                    logger.exception("异步健康检查异常: %s", name)
                    return name, {"connected": False, "error": "check_failed"}

            async_results = await asyncio.gather(
                *(_safe_check(name, fn) for name, fn in self._async_checks.items()),
                return_exceptions=False,
            )
            for name, result in async_results:
                results[name] = result
                overall = worst_status(overall, evaluate_result(result))

        return {
            "status": overall,
            "checks": results,
            "probed": bool(self._async_checks),
        }

    def register_defaults(self, emotion_engine=None, tone_mimic=None,
                          vector_memory=None, structured_memory=None,
                          llm_gateway=None, ase_engine=None, scheduler=None):
        if emotion_engine:
            self.register("emotion_engine", emotion_engine.health_check)
        if tone_mimic:
            self.register("tone_mimic", tone_mimic.health_check)
        if vector_memory:
            self.register("vector_memory", vector_memory.health_check)
        if structured_memory:
            self.register("structured_memory", structured_memory.health_check)
        if llm_gateway:
            self.register("llm_gateway", llm_gateway.health_check)
        if ase_engine:
            self.register("ase_engine", ase_engine.health_check)
        if scheduler:
            self.register("scheduler", scheduler.health_check)


health_checker = HealthChecker()
