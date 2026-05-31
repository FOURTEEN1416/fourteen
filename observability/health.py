from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("health")


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
        overall = "healthy"
        for name, fn in self._checks.items():
            try:
                result = fn()
                results[name] = result
                if not result.get("connected", result.get("available", True)):
                    overall = "degraded" if overall == "healthy" else overall
            except Exception:
                logger.exception("健康检查异常: %s", name)
                results[name] = {"connected": False, "error": "health_check_failed"}
                overall = "unhealthy"
        return {
            "status": overall,
            "checks": results,
        }

    async def async_check(self) -> dict[str, Any]:
        """异步健康检查（真实探测后端，超时保护）"""
        results = {}
        overall = "healthy"

        # 同步检查
        for name, fn in self._checks.items():
            try:
                result = fn()
                results[name] = result
                if not result.get("connected", result.get("available", True)):
                    overall = "degraded" if overall == "healthy" else overall
            except Exception:
                logger.exception("同步健康检查异常: %s", name)
                results[name] = {"connected": False, "error": "health_check_failed"}
                overall = "unhealthy"

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
                if not result.get("connected", result.get("available", True)):
                    overall = "degraded" if overall == "healthy" else overall

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
