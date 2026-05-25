"""
知识采集调度器 — 定时触发知识采集任务
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("knowledge_scheduler")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    AsyncIOScheduler: Any = None  # type: ignore[no-redef]
    logger.warning("APScheduler not installed, scheduled collection disabled")


class KnowledgeScheduler:
    """
    知识采集调度器

    使用APScheduler定时触发采集任务
    """

    def __init__(
        self,
        collect_func: Callable[[], None] | None = None,
        default_times: list[str] | None = None,
    ):
        self._collect_func = collect_func
        self._default_times = default_times or ["08:00", "20:00"]
        self._scheduler: Any | None = None
        self._jobs: dict[str, Any] = {}

    def start(self) -> bool:
        """启动调度器"""
        if not HAS_APSCHEDULER:
            logger.warning("APScheduler not available")
            return False

        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler()

        # 添加默认采集任务
        for t in self._default_times:
            hour, minute = map(int, t.split(":"))
            job_id = f"collect_{t.replace(':', '')}"

            self._scheduler.add_job(
                self._collect_func or self._default_collect,
                CronTrigger(hour=hour, minute=minute),
                id=job_id,
                name=f"Knowledge collection at {t}",
            )
            self._jobs[job_id] = {"time": t, "enabled": True}

        self._scheduler.start()
        logger.info("Knowledge scheduler started with %d jobs", len(self._jobs))
        return True

    def stop(self) -> None:
        """停止调度器"""
        if self._scheduler:
            self._scheduler.shutdown()
            self._scheduler = None
            logger.info("Knowledge scheduler stopped")

    def add_job(
        self,
        job_func: Callable,
        hour: int,
        minute: int = 0,
        job_id: str | None = None,
    ) -> str:
        """
        添加采集任务

        Args:
            job_func: 任务函数
            hour: 小时
            minute: 分钟
            job_id: 任务ID

        Returns:
            任务ID
        """
        if not HAS_APSCHEDULER:
            return ""

        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler()

        job_id = job_id or f"custom_{hour}_{minute}"

        self._scheduler.add_job(
            job_func,
            CronTrigger(hour=hour, minute=minute),
            id=job_id,
        )
        self._jobs[job_id] = {"time": f"{hour:02d}:{minute:02d}", "enabled": True}

        logger.info("Added knowledge collection job: %s at %02d:%02d", job_id, hour, minute)
        return job_id

    def remove_job(self, job_id: str) -> bool:
        """移除任务"""
        if self._scheduler and job_id in self._jobs:
            self._scheduler.remove_job(job_id)
            del self._jobs[job_id]
            logger.info("Removed job: %s", job_id)
            return True
        return False

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有任务"""
        return [
            {"id": job_id, **job_info}
            for job_id, job_info in self._jobs.items()
        ]

    async def _default_collect(self) -> None:
        """默认采集函数"""
        logger.info("Knowledge collection triggered at %s", datetime.now(tz=timezone.utc))
        # 实际采集逻辑由外部注入
        if self._collect_func:
            self._collect_func()

    def trigger_now(self) -> None:
        """立即触发一次采集"""
        logger.info("Manual knowledge collection triggered")
        if self._collect_func:
            self._collect_func()
