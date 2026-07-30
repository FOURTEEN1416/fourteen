"""
定时调度器 — 基于 APScheduler

管理所有定时任务：
1. ASE 检查（每5分钟）
2. 每日维护（00:05）
3. 每日重置（00:00）

早安/晚安由 ASE 场景触发处理，不再独立注册定时任务。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

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
        ase_engine: Any | None = None,
        send_message_func: Callable[[str], None] | None = None,
        daily_maintenance_func: Callable[[], None] | None = None,
        get_last_chat_time: Callable[[], datetime | None] | None = None,
        is_online_check: Callable[[], bool] | None = None,
        emotion_engine: Any | None = None,
    ):
        self.ase = ase_engine
        self._send = send_message_func
        self._daily_maintenance = daily_maintenance_func
        self._get_last_chat_time = get_last_chat_time
        self._is_online_check = is_online_check
        self._emotion_engine = emotion_engine

        self._scheduler: Any = None
        self._active_tasks: dict[str, bool] = {}

        self._last_check_time: datetime | None = None

        # 通道注册表（支持多通道投递）
        self._channels: dict[str, Callable[[], Any]] = {}        # name → sender_factory
        self._channel_instances: dict[str, Callable | None] = {}  # name → instantiated sender
        self._health_check_interval = 60  # 秒
        self._quiet_hours = (23, 7)       # 23:00-07:00 免打扰

        logger.info("ProactiveScheduler initialized (APScheduler=%s)", HAS_APSCHEDULER)

    def register_channel(self, name: str, sender_factory: Callable[[], Any]) -> None:
        """
        注册并初始化消息通道

        参数:
            name: 通道名称 (如 "wechat", "websocket", "console")
            sender_factory: 返回 async send(message) 可调用对象的工厂函数
        """
        if name in self._channels:
            logger.warning("通道 '%s' 重复注册，将覆盖旧通道", name)
        self._channels[name] = sender_factory
        try:
            instance = sender_factory()
            self._channel_instances[name] = instance
            logger.info("消息通道已注册并初始化: %s", name)
        except Exception as e:
            logger.warning("消息通道初始化失败: %s - %s（稍后重试）", name, e)
            self._channel_instances[name] = None

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

            # 5. 通道健康检查（每60秒）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._health_check_channels, "health_check_channels"),
                IntervalTrigger(seconds=self._health_check_interval),
                id="health_check_channels",
                name="通道健康检查",
                replace_existing=True,
                misfire_grace_time=30,
                coalesce=True,
            )

            self._scheduler.start()
            self._last_check_time = datetime.now(tz=timezone.utc)
            logger.info("Scheduler started with %d jobs", len(self._scheduler.get_jobs()))
            return True

        except Exception as e:  # noqa: BLE001
            logger.error("Failed to start scheduler: %s", e)
            return False

    def stop(self) -> None:
        """停止调度器"""
        if self._scheduler and self._scheduler.running:
            self._save_state()
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # ── 定时任务 ─────────────────────────────────────────

    def _is_quiet_hours(self) -> bool:
        """检查是否在免打扰时段"""
        # 使用本地时区（Asia/Shanghai 默认 UTC+8）。
        # 旧实现硬编码 +8 偏移且未取模，在 UTC 16:00-23:00 时段会得到 24-31 的非法小时。
        # 现在使用 datetime.now() 获取本地时间（已考虑系统时区），并允许通过 timezone_offset 配置。
        try:
            # 优先使用系统本地时间（已含时区转换）
            local_hour = datetime.now().hour
        except Exception:  # noqa: BLE001
            # 兜底：UTC+8
            local_hour = (datetime.now(tz=timezone.utc).hour + 8) % 24
        start, end = self._quiet_hours
        if start < end:
            return start <= local_hour < end
        return local_hour >= start or local_hour < end

    async def _send_to_all(self, message: str) -> bool:
        """
        向所有已注册通道发送消息
        优先级: wechat > websocket > console

        Returns: 是否至少一个通道发送成功
        """
        if self._is_quiet_hours():
            logger.info("免打扰时段(%s-%s)，跳过非紧急消息", self._quiet_hours[0], self._quiet_hours[1])
            return False

        priority = ["wechat", "websocket", "console"]
        sent = False
        for name in priority:
            sender = self._channel_instances.get(name)
            if sender is None:
                continue
            try:
                if asyncio.iscoroutinefunction(sender):
                    await sender(message)
                else:
                    sender(message)
                logger.info("主动消息已投递: %s", name)
                sent = True
                break  # 高优先级成功就不再尝试低优先级
            except Exception as e:
                logger.warning("通道投递失败: %s - %s", name, e)
                self._channel_instances[name] = None  # 标记失效

        # 兜底：使用旧的 send_message_func
        if not sent and self._send:
            try:
                self._send(message)
                sent = True
            except Exception as e:
                logger.error("兜底发送失败: %s", e)

        return sent

    def _check_ase(self) -> None:
        """ASE 主动消息检查（APScheduler同步任务）"""
        if not self.ase:
            return

        try:
            if self._get_last_chat_time:
                last_chat = self._get_last_chat_time()
                hours = (datetime.now(tz=timezone.utc) - last_chat).total_seconds() / 3600 if last_chat else 99.0
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
                # 通过事件循环发送（APScheduler在非async上下文运行）
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(self._send_to_all(message))
                    else:
                        loop.run_until_complete(self._send_to_all(message))
                except RuntimeError:
                    # 无事件循环，兜底
                    if self._send:
                        self._send(message)
        except Exception as e:  # noqa: BLE001
            logger.error("ASE check failed: %s", e)
        finally:
            self._last_check_time = datetime.now(tz=timezone.utc)

    def _run_daily_maintenance(self) -> None:
        """每日维护"""
        if self._daily_maintenance:
            try:
                self._daily_maintenance()
                logger.info("Daily maintenance completed")
            except Exception as e:  # noqa: BLE001
                logger.error("Daily maintenance failed: %s", e)

        # 情感时间衰减 — 应用自上次检查以来的能量恢复与强度衰减
        try:
            hours = self._hours_since_last_check()
            if hours > 0 and self._emotion_engine is not None and hasattr(self._emotion_engine, 'apply_time_decay'):
                self._emotion_engine.apply_time_decay(hours)
                logger.info("情感时间衰减已应用: %.2f 小时", hours)
        except Exception as e:  # noqa: BLE001
            logger.warning("情感时间衰减任务失败: %s", e)

        # 好感度衰减 — 对所有已记录角色应用每日衰减
        # DecayEngine 逻辑正确但此前未被调度调用，此处补全
        try:
            from api.deps import deps as _deps
            shisi_reg = getattr(_deps, "shisi_reg", None)
            if shisi_reg is not None:
                enhancer = getattr(shisi_reg, "affinity_enhancer", None)
                if enhancer is not None:
                    # 遍历所有已记录好感度的角色，逐一应用衰减
                    character_ids = list(getattr(enhancer, "_values", {}).keys())
                    total_decay = 0.0
                    decayed_count = 0
                    for cid in character_ids:
                        decay = enhancer.apply_decay(cid)
                        if decay > 0:
                            total_decay += decay
                            decayed_count += 1
                    if decayed_count > 0:
                        logger.info(
                            "好感度衰减完成: %d/%d 个角色衰减, 总衰减 %.2f",
                            decayed_count, len(character_ids), total_decay,
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning("好感度衰减任务失败: %s", e)

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
            except Exception as e:  # noqa: BLE001
                logger.warning("State save failed: %s", e)

    # ── 工具方法 ─────────────────────────────────────────

    def _hours_since_last_check(self) -> float:
        if self._last_check_time:
            delta = datetime.now(tz=timezone.utc) - self._last_check_time
            return delta.total_seconds() / 3600
        return 0.0

    def get_jobs(self) -> list[dict[str, Any]]:
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

    def _health_check_channels(self) -> None:
        """检查并重连失效通道

        修复：factory() 返回 None 时不算重连成功（如 wechat connector 尚未注入），
        避免每分钟刷"通道已重连"的虚假日志。
        """
        for name in list(self._channel_instances.keys()):
            if self._channel_instances.get(name) is None:
                factory = self._channels.get(name)
                if factory:
                    try:
                        instance = factory()
                        if instance is not None:
                            self._channel_instances[name] = instance
                            logger.info("通道已重连: %s", name)
                        else:
                            logger.debug("通道 %s 暂未就绪，等待依赖注入", name)
                    except Exception as e:
                        logger.debug("通道重连失败: %s - %s", name, e)

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "running": self._scheduler is not None and self._scheduler.running,
            "apscheduler_available": HAS_APSCHEDULER,
            "jobs": len(self.get_jobs()) if self._scheduler else 0,
        }
