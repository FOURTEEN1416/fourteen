"""
定时调度器 — 基于 APScheduler

管理所有定时任务：
1. ASE 检查（每5分钟）
2. 每日维护（00:05）
3. 每日重置（00:00）

早安/晚安由 ASE 场景触发处理，不再独立注册定时任务。
"""

from __future__ import annotations

import logging
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
    早安/晚安由 ASEEngine._check_scene_triggers() 统一处理。
    """

    def __init__(
        self,
        ase_engine: Optional[Any] = None,
        send_message_func: Optional[Callable[[str], None]] = None,
        daily_maintenance_func: Optional[Callable[[], None]] = None,
        get_last_chat_time: Optional[Callable[[], Optional[datetime]]] = None,
        is_online_check: Optional[Callable[[], bool]] = None,
    ):
        self.ase = ase_engine
        self._send = send_message_func
        self._daily_maintenance = daily_maintenance_func
        self._get_last_chat_time = get_last_chat_time
        self._is_online_check = is_online_check

        self._scheduler: Any = None
        self._active_tasks: Dict[str, bool] = {}

        self._last_check_time: Optional[datetime] = None

        self._ws_server = None
        self._wechat_connector = None

        logger.info("ProactiveScheduler initialized (APScheduler=%s)", HAS_APSCHEDULER)

    def set_ws_server(self, ws_server) -> None:
        """注入WebSocket服务器实例"""
        self._ws_server = ws_server

    def set_wechat_connector(self, wechat_connector) -> None:
        """注入微信连接器实例"""
        self._wechat_connector = wechat_connector

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
            self._scheduler = BackgroundScheduler(daemon=True)

            # 1. ASE 检查（每5分钟）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._check_ase, "ase_check"),
                IntervalTrigger(minutes=5),
                id="ase_check",
                name="ASE主动消息检查",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 2. 每日维护（00:05）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._run_daily_maintenance, "daily_maintenance"),
                CronTrigger(hour=0, minute=5),
                id="daily_maintenance",
                name="每日维护",
                replace_existing=True,
                misfire_grace_time=60,
                coalesce=True,
            )

            # 3. 每日 ASE 重置（00:00）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._reset_daily, "daily_reset"),
                CronTrigger(hour=0, minute=0),
                id="daily_reset",
                name="每日重置",
                replace_existing=True,
            )

            # 4. 状态持久化（每10分钟）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._save_state, "save_state"),
                IntervalTrigger(minutes=10),
                id="save_state",
                name="状态持久化",
                replace_existing=True,
                misfire_grace_time=120,
                coalesce=True,
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
            self._save_state()
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ── 定时任务 ─────────────────────────────────────────

    def _check_ase(self) -> None:
        """ASE 主动消息检查"""
        if not self.ase:
            return

        try:
            if self._get_last_chat_time:
                last_chat = self._get_last_chat_time()
                if last_chat:
                    hours = (datetime.now() - last_chat).total_seconds() / 3600
                else:
                    hours = 99.0
            else:
                hours = self._hours_since_last_check()

            is_online = True
            if self._is_online_check:
                is_online = self._is_online_check()

            if not is_online:
                logger.debug("用户离线，仅更新紧迫度不发送")
                if hasattr(self.ase, 'tick'):
                    self.ase.tick(hours, dry_run=True)
                return

            result = self.ase.tick(hours)
            if result:
                message = result.get("message", "")
                msg_type = result.get("type", "unknown")
                logger.info("ASE triggered: [%s] %s", msg_type, message)
                if self._send:
                    self._send(message)
        except Exception as e:
            logger.error("ASE check failed: %s", e)
        finally:
            self._last_check_time = datetime.now()

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
        self._save_state()

    def _save_state(self) -> None:
        """持久化ASE引擎状态"""
        if self.ase and hasattr(self.ase, "save_state"):
            try:
                self.ase.save_state()
            except Exception as e:
                logger.warning("State save failed: %s", e)

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
