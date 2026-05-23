"""
定时调度器 — 基于 APScheduler

管理所有定时任务：
1. 早安任务（08:00）
2. 晚安任务（23:30）
3. ASE 检查（每5分钟）
4. 每日维护（00:05）
5. 纪念日检查（每天）
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("scheduler")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("APScheduler not installed, scheduler disabled")


class ProactiveScheduler:
    """
    主动消息调度器

    定时检查 ASE 引擎，判断是否该主动发消息。
    同时管理早安/晚安等定时问候。
    """

    def __init__(
        self,
        ase_engine: Optional[Any] = None,
        send_message_func: Optional[Callable[[str], None]] = None,
        daily_maintenance_func: Optional[Callable[[], None]] = None,
    ):
        self.ase = ase_engine
        self._send = send_message_func
        self._daily_maintenance = daily_maintenance_func

        self._scheduler: Any = None
        self._active_tasks: Dict[str, bool] = {}

        # 上次 ASE 检查的时间（用于计算小时差）
        self._last_check_time: Optional[datetime] = None

        logger.info("ProactiveScheduler initialized (APScheduler=%s)", HAS_APSCHEDULER)

    def _safe_job_wrapper(self, job_fn: Callable, job_name: str) -> Callable:
        def wrapper(*args, **kwargs):
            try:
                return job_fn(*args, **kwargs)
            except Exception as e:
                logger.error("Scheduled job '%s' failed: %s", job_name, e, exc_info=True)
        return wrapper

    def start(self) -> bool:
        """
        启动所有定时任务

        Returns:
            是否成功启动
        """
        if not HAS_APSCHEDULER:
            logger.warning("APScheduler not available, cannot start scheduler")
            return False

        if self._scheduler and self._scheduler.running:
            logger.info("Scheduler already running")
            return True

        try:
            self._scheduler = BackgroundScheduler(daemon=True)  # type: ignore

            # 1. ASE 检查（每5分钟）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._check_ase, "ase_check"),
                IntervalTrigger(minutes=5),  # type: ignore
                id="ase_check",
                name="ASE主动消息检查",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 2. 早安任务（08:00）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._morning_greeting, "morning_greeting"),
                CronTrigger(hour=8, minute=0),  # type: ignore
                id="morning_greeting",
                name="早安问候",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 3. 晚安任务（23:30）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._night_greeting, "night_greeting"),
                CronTrigger(hour=23, minute=30),  # type: ignore
                id="night_greeting",
                name="晚安问候",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 4. 每日维护（00:05）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._run_daily_maintenance, "daily_maintenance"),
                CronTrigger(hour=0, minute=5),  # type: ignore
                id="daily_maintenance",
                name="每日维护",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 5. 每日 ASE 重置（00:00）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._reset_daily, "daily_reset"),
                CronTrigger(hour=0, minute=0),  # type: ignore
                id="daily_reset",
                name="每日重置",
                replace_existing=True,
            )

            self._scheduler.start()
            self._last_check_time = datetime.now()
            logger.info("Scheduler started with %d jobs", len(self._scheduler.get_jobs()))
            return True

        except Exception as e:
            logger.error("Failed to start scheduler: %s", e)
            return False

    def stop(self) -> None:
        """停止调度器"""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ── 定时任务 ─────────────────────────────────────────

    def _check_ase(self) -> None:
        """ASE 主动消息检查"""
        if not self.ase:
            return

        try:
            # 计算距离上次聊天的小时数
            hours = self._hours_since_last_check()

            result = self.ase.tick(hours)
            if result:
                message = result.get("message", "")
                msg_type = result.get("type", "unknown")
                logger.info("ASE triggered: [%s] %s", msg_type, message)
                if self._send:
                    self._send(f"[{msg_type}] {message}")
        except Exception as e:
            logger.error("ASE check failed: %s", e)
        finally:
            self._last_check_time = datetime.now()

    def _morning_greeting(self) -> None:
        """早安问候"""
        if not self._send:
            return
        greetings = [
            "早安呀～今天又比我先醒",
            "早！新的一天开始了",
            "早上好，昨晚睡得好吗",
        ]
        msg = random.choice(greetings)
        logger.info("Morning greeting sent")
        self._send(msg)

    def _night_greeting(self) -> None:
        """晚安问候"""
        if not self._send:
            return
        greetings = [
            "还不睡？要我陪你会儿吗",
            "晚安啦，别熬夜太晚",
            "到点睡觉了，别让我担心",
        ]
        msg = random.choice(greetings)
        logger.info("Night greeting sent")
        self._send(msg)

    def _run_daily_maintenance(self) -> None:
        """每日维护"""
        if self._daily_maintenance:
            try:
                self._daily_maintenance()
                logger.info("Daily maintenance completed")
            except Exception as e:
                logger.error("Daily maintenance failed: %s", e)

    def _reset_daily(self) -> None:
        """每日重置"""
        if self.ase and hasattr(self.ase, "reset_daily_count"):
            self.ase.reset_daily_count()
            logger.info("Daily ASE count reset")

    # ── 工具方法 ─────────────────────────────────────────

    def _hours_since_last_check(self) -> float:
        if self._last_check_time:
            delta = datetime.now() - self._last_check_time
            return delta.total_seconds() / 3600
        return 0.0

    def get_jobs(self) -> List[Dict[str, Any]]:
        """获取所有任务状态"""
        if not self._scheduler:
            return []

        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })
        return jobs

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "apscheduler_available": HAS_APSCHEDULER,
            "jobs": len(self.get_jobs()) if self._scheduler else 0,
        }
