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
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from utils.project_paths import project_path

logger = logging.getLogger("scheduler")

# 成就兜底重算的角色库目录。锚定项目根而非 CWD（从非仓库根启动时
# CWD 相对路径会扫到空目录，导致成就兜底静默失效）；
# 同时保留为模块级常量，便于测试注入临时目录。
_CHARACTERS_DIR = project_path("config", "characters")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("APScheduler not installed, scheduler disabled")


def run_achievement_maintenance() -> int:
    """成就每日兜底重算（ADR-0014 每日维护路径，2026-09-01 第二阶段）。

    对角色库（config/characters/*.json 内部 id 字段）全部角色幂等重算：
    修复漏事件/历史数据漂移；读取时重算（GET achievements）仍是主路径。
    返回处理的角色数；角色库缺失或为空时返回 0。
    """
    import json as _json

    from api.achievement_engine import recalculate_achievements
    from api.database import _async_session as _ach_session_factory
    from api.path_security import sanitize_id as _sanitize_id

    # 锚定项目根，避免从非仓库根 CWD 启动时扫到空目录（扫不到 = 成就兜底静默失效）
    characters_dir = _CHARACTERS_DIR
    if not characters_dir.exists():
        return 0
    ids: list[str] = []
    for card_file in characters_dir.glob("*.json"):
        try:
            data = _json.loads(card_file.read_text(encoding="utf-8"))
            cid = _sanitize_id(str(data.get("id", "")))
            if cid:
                ids.append(cid)
        except Exception:  # noqa: BLE001
            logger.warning("成就兜底跳过无法解析的角色文件: %s", card_file.name)

    async def _recalc_all() -> None:
        async with _ach_session_factory() as session:
            for cid in ids:
                try:
                    await recalculate_achievements(session, cid)
                except Exception:  # noqa: BLE001
                    logger.warning("成就兜底重算失败: %s", cid, exc_info=True)

    asyncio.run(_recalc_all())
    return len(ids)


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
        self._quiet_hours = (23, 7)       # 23:00-07:00 免打扰（web 端可调）

        # 知识库定期采集（Vault collect）— web 控制端开关
        self._vault_enabled = False
        self._vault_interval_min = 60
        # 配置以 data/scheduler_config.json 为跨 worker 真源（4 uvicorn worker
        # 中仅 master 持有调度器，GET/POST 可能落到任一 worker——文件保证读一
        # 致，master 每次 _check_ase 重载保证写最终生效 ≤5 分钟）。
        # 路径锚定项目根（见 _CONFIG_PATH）：相对路径按 CWD 解析，从非仓库根
        # 启动时会静默读写另一个文件，表现为"开关保存成功但不生效"。
        self._load_config_file()

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
            # 知识库定期采集任务按持久化配置恢复
            self._sync_vault_job()
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

    def set_quiet_hours(self, start: int, end: int) -> None:
        """设置免打扰时段（web 控制端可调；0-23 整点）。"""
        if not (0 <= int(start) <= 23 and 0 <= int(end) <= 23):
            raise ValueError(f"免打扰小时必须在 0-23：got {start}-{end}")
        self._quiet_hours = (int(start), int(end))
        self._save_config_file()
        logger.info("免打扰时段已更新: %02d:00-%02d:00", start, end)

    def get_quiet_hours(self) -> tuple[int, int]:
        return self._quiet_hours

    # ── 知识库定期采集（web 开关 + 持久化）──────────────

    # 跨 worker 配置真源路径 —— 必须锚定项目根（绝对路径）。
    # 旧实现为 Path("data")/"scheduler_config.json"（相对路径）：按进程 CWD 解析，
    # 从非仓库根启动/测试时读写到另一个文件，导致
    # ① 开关"保存成功但不生效"；② 测试读到宿主机脏值而失败。
    _CONFIG_PATH = project_path("data", "scheduler_config.json")

    def get_vault_config(self) -> dict[str, Any]:
        return {
            "enabled": self._vault_enabled,
            "interval_minutes": self._vault_interval_min,
        }

    def set_vault_collect(self, enabled: bool, interval_minutes: int | None = None) -> dict[str, Any]:
        """web 控制端开关：启用/停用知识库定期采集，立即生效并持久化。"""
        self._vault_enabled = bool(enabled)
        if interval_minutes is not None:
            self._vault_interval_min = max(10, int(interval_minutes))
        self._save_config_file()
        self._sync_vault_job()
        logger.info("知识库定期采集: enabled=%s interval=%dmin", self._vault_enabled, self._vault_interval_min)
        return self.get_vault_config()

    # ── 跨 worker 配置文件（data/scheduler_config.json 为真源）──

    @classmethod
    def _read_config_file(cls) -> dict[str, Any]:
        try:
            if cls._CONFIG_PATH.exists():
                return json.loads(cls._CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("调度器配置读取失败: %s", e)
        return {}

    @classmethod
    def write_config_file(
        cls,
        quiet_hours: tuple[int, int] | None = None,
        vault_enabled: bool | None = None,
        vault_interval: int | None = None,
    ) -> dict[str, Any]:
        """非 master worker 的 POST 端点直接写文件；master 下个 tick 重载生效。"""
        data = cls._read_config_file()
        if quiet_hours is not None:
            data["quiet_hours"] = {"start": int(quiet_hours[0]), "end": int(quiet_hours[1])}
        vault = data.get("vault") or {}
        if vault_enabled is not None:
            vault["enabled"] = bool(vault_enabled)
        if vault_interval is not None:
            vault["interval_minutes"] = max(10, int(vault_interval))
        data["vault"] = vault
        try:
            cls._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            cls._CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("调度器配置保存失败: %s", e)
        return data

    def _load_config_file(self) -> None:
        data = self._read_config_file()
        qh = data.get("quiet_hours") or {}
        if "start" in qh and "end" in qh:
            with contextlib.suppress(TypeError, ValueError):
                self._quiet_hours = (int(qh["start"]), int(qh["end"]))
        vault = data.get("vault") or {}
        if "enabled" in vault:
            self._vault_enabled = bool(vault["enabled"])
        if "interval_minutes" in vault:
            self._vault_interval_min = max(10, int(vault["interval_minutes"]))

    def _save_config_file(self) -> None:
        self.write_config_file(
            quiet_hours=self._quiet_hours,
            vault_enabled=self._vault_enabled,
            vault_interval=self._vault_interval_min,
        )

    def reload_config(self) -> None:
        """master worker 周期性从文件重载（其他 worker 的写 ≤5 分钟内生效）。"""
        before = (self._quiet_hours, self._vault_enabled, self._vault_interval_min)
        self._load_config_file()
        after = (self._quiet_hours, self._vault_enabled, self._vault_interval_min)
        if before != after:
            self._sync_vault_job()
            logger.info("调度器配置已重载: quiet=%s vault=%s", self._quiet_hours, self.get_vault_config())

    def _sync_vault_job(self) -> None:
        """按当前开关状态增删 APScheduler 任务（幂等）。"""
        if not (self._scheduler and self._scheduler.running):
            return
        job_id = "vault_collect"
        if not self._vault_enabled:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
            return
        self._scheduler.add_job(
            self._safe_job_wrapper(self._run_vault_collect, "vault_collect"),
            IntervalTrigger(minutes=self._vault_interval_min),
            id=job_id,
            name="知识库定期采集",
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )

    def _run_vault_collect(self) -> None:
        """对 shisi 角色库全部角色做知识提取+索引（VaultCollector.collect_card）。"""
        if not self._vault_enabled:
            return
        try:
            from api.deps import deps as _deps
            from shisi.vault import VaultCollector

            cm = getattr(getattr(_deps, "shisi_reg", None), "character_manager", None)
            if cm is None:
                return
            collector = VaultCollector()
            count = 0
            for state in cm.list_characters():
                try:
                    card = cm.load_character(state.character_id)
                except Exception:  # noqa: BLE001
                    card = None
                if card is None:
                    continue
                collector.collect_card(state.character_id, card)
                count += 1
            if count:
                logger.info("知识库定期采集完成: %d 个角色已重建索引", count)
        except Exception as e:  # noqa: BLE001
            logger.warning("知识库定期采集失败: %s", e)

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
            # 原实现此处**静默 return** —— 引擎未注入时生产环境完全不可观测
            # （2026-09-18 排查代价：数小时，最终靠逐层加日志才定位）。
            logger.warning("ASE 引擎未注入（components['ase'] 为空），主动消息检查跳过")
            return

        # master 每 tick 重载跨 worker 配置文件（其他 worker 的写 ≤5 分钟生效）
        self.reload_config()

        try:
            if self._get_last_chat_time:
                last_chat = self._get_last_chat_time()
                hours = (datetime.now(tz=timezone.utc) - last_chat).total_seconds() / 3600 if last_chat else 99.0
            elif hasattr(self.ase, "_hours_since_last_chat"):
                # ASE 引擎自身持久化了 last_chat_time（on_chat 更新、状态文件落盘）。
                # 旧实现此处回退"距上次调度检查的时间"（≈5 分钟），
                # 导致 missing_bonus 恒为 0、紧迫度永远到不了阈值（2026-09-17 修复）。
                hours = self.ase._hours_since_last_chat()
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
            # 每 tick 一条可观测记录：这是排查"主动消息不发"时最关键的一行
            # （此前只有"触发成功"才打日志，未触发的原因完全不可见）
            logger.info(
                "ASE tick: hours=%.2f urgency=%.2f daily_count=%d paused=%s result=%s",
                hours,
                getattr(getattr(self.ase, "urgency", None), "total", -1.0),
                getattr(self.ase, "_daily_message_count", -1),
                getattr(self.ase, "_paused", None),
                bool(result),
            )
            if result:
                message = result.get("message", "")
                msg_type = result.get("type", "unknown")
                logger.info("ASE triggered: [%s] %s", msg_type, message)
                self._deliver(message)
        except Exception as e:  # noqa: BLE001
            logger.error("ASE check failed: %s", e)
        finally:
            self._last_check_time = datetime.now(tz=timezone.utc)

    def _deliver(self, message: str) -> None:
        """在 APScheduler 工作线程内同步投递主动消息。

        旧实现用 asyncio.get_event_loop()+ensure_future —— 非主线程无事件循环
        必抛 RuntimeError，导致全部消息落入 console 日志兜底、微信通道从未送达
        （2026-09-17 生产日志实证：64 次触发 0 次送达）。_send_to_all 内部均为
        同步 HTTP 调用（requests），asyncio.run 新建临时循环执行是安全的。
        """
        try:
            asyncio.run(self._send_to_all(message))
        except Exception as e:  # noqa: BLE001
            logger.error("主动消息投递失败: %s", e)
            if self._send:
                try:
                    self._send(message)
                except Exception:  # noqa: BLE001
                    logger.exception("主动消息兜底发送失败")

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

        # ── 成就每日兜底重算（ADR-0014 第二阶段）──
        try:
            maintained = run_achievement_maintenance()
            if maintained:
                logger.info("成就每日兜底重算完成: %d 个角色", maintained)
        except Exception as e:  # noqa: BLE001
            logger.warning("成就兜底重算任务失败: %s", e)

        # ── 候选 D：重要日期检查（生日/纪念日命中即发祝福） ──
        self._check_important_dates()

    def _check_important_dates(self) -> None:
        """候选 D：命中重要日期时以角色口吻发送祝福（LLM 生成，模板兜底）。"""
        try:
            from datetime import datetime as _dt

            from utils.important_dates import check_today

            active_id = ""
            try:
                from api.deps import deps as _deps

                cm = getattr(getattr(_deps, "shisi_reg", None), "character_manager", None)
                active_id = (cm.get_active_id() if cm else "") or ""
                if cm and active_id:
                    _card = cm.get_card(active_id) if hasattr(cm, "get_card") else None
                    (getattr(_card, "name", "") or "") if _card else ""
            except Exception:
                pass

            hits = check_today(active_id, _dt.now())
            if not hits:
                return

            names = "、".join(h.get("name", "") for h in hits)
            kinds = "/".join(sorted({h.get("kind", "custom") for h in hits}))
            wish = "生日快乐" if "birthday" in kinds else "纪念日快乐"
            message = f"今天是个特别的日子（{names}）。{wish}呀！"
            # LLM 润色（失败用模板）
            try:
                ase = self.ase
                llm = getattr(ase, "_llm", None)
                if llm is not None and hasattr(llm, "chat_sync"):
                    polished = llm.chat_sync(
                        query=(
                            f"以角色口吻给对方发一条{'生日' if 'birthday' in kinds else '纪念日'}祝福，"
                            f"提到「{names}」，2-3 句话，真挚不说教："
                        ),
                        max_tokens=150, temperature=0.8,
                    )
                    if polished and str(polished).strip():
                        message = str(polished).strip()
            except Exception:
                pass

            logger.info("[重要日期] 命中 %s，发送祝福", names)
            self._deliver(message)
        except Exception as e:  # noqa: BLE001
            logger.warning("重要日期检查失败: %s", e)

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
