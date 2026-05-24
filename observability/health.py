from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("health")


class HealthChecker:
    def __init__(self):
        self._checks: dict[str, Callable] = {}

    def register(self, name: str, check_fn: Callable[[], dict]):
        self._checks[name] = check_fn

    def check(self) -> dict[str, Any]:
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
