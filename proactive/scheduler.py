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
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

# 消息清洗（拦截 LLM 推理过程泄漏为消息内容）+ 统一本地时钟。
# proactive.ase_engine 不反向依赖 scheduler，不存在循环导入。
# ⚠️ 静默时段判定必须与 ASE 引擎共用 _local_now()：引擎在非 UTC+8 主机
# （GitHub Actions / 容器默认 UTC）会强制换算到北京时间，若调度器仍用
# datetime.now().hour，两边小时数差 8，静默短路会静默失效（2026-09-19 CI 实证）。
from proactive.ase_engine import _local_now, sanitize_message
from utils.project_paths import project_path

logger = logging.getLogger("scheduler")


def _accepts_key(fn: Callable | None) -> bool:
    """回调是否接受 user_key 参数（兼容旧零参/无 key 签名）。"""
    if fn is None:
        return False
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return any(
        p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY, p.VAR_KEYWORD)
        and name in ("user_key", "session_key", "session_id")
        for name, p in params.items()
    ) or any(p.kind == p.VAR_KEYWORD for p in params.values())

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
    ):
        self.ase = ase_engine
        self._send = send_message_func
        self._daily_maintenance = daily_maintenance_func
        self._get_last_chat_time = get_last_chat_time
        self._is_online_check = is_online_check
        # LLM 主动决策（用户裁决：时机与内容由模型判断，无策略闸）
        self._llm_provider: Any | None = None
        # 自问自答根治：主动消息送达后回写历史需要记忆服务
        self._memory: Any | None = None
        # AX 审查 B3：执行 LLM 自己给出的 wait_minutes（非硬编码日程表）
        self._llm_proactive_next_ok: dict[str, float] = {}
        # P1-22：投递连续失败计数（指数退避；旧实现失败零退避，
        # 不可达用户每 5 分钟「生成→失败→再生成」，一天 288 次 LLM 零投递）
        self._deliver_fail_counts: dict[str, int] = {}
        # P1-23：通道资产（WebSocketServer 连接/锁）所属事件循环的 getter，
        # 由装配层注入——调度线程直接 asyncio.run 跨循环调用属未定义行为
        self._delivery_loop_getter: Callable[[], Any] | None = None
        # P1-51：web_disabled 账本事件每用户每日至多一条（旧实现每 tick 写一行，
        # 关闭态反而涨得最快）
        self._disabled_event_day: dict[str, str] = {}

        self._scheduler: Any = None
        self._active_tasks: dict[str, bool] = {}

        self._last_check_time: datetime | None = None

        # 通道注册表（支持多通道投递）
        self._channels: dict[str, Callable[[], Any]] = {}        # name → sender_factory
        self._channel_instances: dict[str, Callable | None] = {}  # name → instantiated sender
        self._health_check_interval = 60  # 秒
        # 提醒到期投递任务（api 装配层注入；每分钟轮询，豁免静默时段）
        self._reminder_task: Callable[[], None] | None = None
        self._quiet_hours = (23, 7)       # 23:00-07:00 免打扰（web 端可调）
        # 重要日期当日幂等记录（每小时任务 + 00:05 维护可能同日命中）
        self._important_dates_sent: set[str] = set()
        # AX P2：夜间记忆 curator
        self._curator_enabled = True

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

    def set_llm_provider(self, llm: Any | None) -> None:
        """注入 LLM，供主动消息决策（P1：LLM 判时机与文案）。"""
        self._llm_provider = llm

    def set_memory(self, memory: Any | None) -> None:
        """注入记忆服务，供主动消息**送达后回写对话历史**（自问自答根治）。"""
        self._memory = memory

    def _resolve_memory(self) -> Any | None:
        if self._memory is not None:
            return self._memory
        try:
            from api.deps import deps

            orch = getattr(deps, "orch", None)
            comps = getattr(orch, "components", None)
            if isinstance(comps, dict):
                return comps.get("memory")
        except Exception:  # noqa: BLE001
            pass
        return None

    def _record_outbound(self, message: str, session_key: str | None) -> None:
        """定向投递成功后把这句写进该会话历史（无会话键的广播无法归属，不写）。"""
        if not session_key:
            return
        mem = self._resolve_memory()
        record = getattr(mem, "record_outbound_message", None)
        if record is None:
            return
        try:
            record(message=message, session_id=str(session_key))
        except Exception as e:  # noqa: BLE001
            logger.warning("主动消息回写历史失败 session=%s: %s", session_key, e)

    def _resolve_proactive_llm(self, eng: Any | None = None) -> Any | None:
        if self._llm_provider is not None:
            return self._llm_provider
        if eng is not None:
            llm = getattr(eng, "_llm", None)
            if llm is not None:
                return llm
        try:
            from api.deps import deps

            orch = getattr(deps, "orch", None)
            comps = getattr(orch, "components", None)
            if isinstance(comps, dict):
                return comps.get("llm")
        except Exception:  # noqa: BLE001
            pass
        return None

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

    def register_reminder_task(self, task: Callable[[], None]) -> None:
        """注册提醒到期轮询任务（每分钟；静默豁免与定向投递由 task 内部负责）。

        可在 start() 之后调用（api 装配时序晚于 scheduler.start()），
        此时立即挂 job；start() 之前注册则由 start() 统一挂。
        """
        self._reminder_task = task
        if self._scheduler is not None and getattr(self._scheduler, "running", False):
            self._scheduler.add_job(
                self._safe_job_wrapper(task, "reminder_check"),
                IntervalTrigger(minutes=1),
                id="reminder_check",
                name="提醒到期投递检查",
                replace_existing=True,
                misfire_grace_time=30,
                coalesce=True,
            )
            logger.info("提醒到期投递任务已注册（scheduler 运行中，立即挂载）")

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
            # AX P2：夜间记忆 curator（02:17）
            self._scheduler.add_job(
                self._safe_job_wrapper(self._run_memory_curator, "memory_curator"),
                CronTrigger(hour=2, minute=17),
                id="memory_curator",
                name="夜间记忆整理",
                replace_existing=True,
                misfire_grace_time=600,
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

            # 6. 重要日期补发检查（每小时）
            #    00:05 的每日维护恒落在免打扰时段(23-7)内 → 生日/纪念日祝福
            #    会被 _send_to_all() 静默丢弃。改为每小时重试一次，由
            #    _check_important_dates() 内的静默判定 + 当日幂等键保证
            #    「静默结束后第一时间送达，且当日只发一次」。
            self._scheduler.add_job(
                self._safe_job_wrapper(self._check_important_dates, "important_dates"),
                IntervalTrigger(hours=1),
                id="important_dates",
                name="重要日期补发检查",
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=True,
            )

            # 7. 提醒到期投递检查（每分钟；豁免静默时段）
            #    叫醒类提醒（如 06:00）恰落在静默窗内，绝不能套用主动消息的
            #    静默闸门 —— 用户明确要求「六点叫我起床」却收不到，即此缺陷。
            #    task 由 api 装配层注入（register_reminder_task）；未注入则不挂。
            if self._reminder_task is not None:
                self._scheduler.add_job(
                    self._safe_job_wrapper(self._reminder_task, "reminder_check"),
                    IntervalTrigger(minutes=1),
                    id="reminder_check",
                    name="提醒到期投递检查",
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
        """设置免打扰时段（web 控制端可调；0-23 整点）。

        同步注入 ASE 引擎：引擎需在**生成前**就知道静默时段，否则会生成
        消息→扣配额→投递被丢弃（2026-09-19 生产事故根因）。
        """
        if not (0 <= int(start) <= 23 and 0 <= int(end) <= 23):
            raise ValueError(f"免打扰小时必须在 0-23：got {start}-{end}")
        self._quiet_hours = (int(start), int(end))
        if self.ase is not None and hasattr(self.ase, "set_quiet_hours"):
            self.ase.set_quiet_hours(self._quiet_hours[0], self._quiet_hours[1])
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
    # P1-53：write_config_file 是全文件读-改-写，4 worker 下并发写互相覆盖/半文件。
    _CONFIG_LOCK = threading.RLock()

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
        follow_up: dict[str, Any] | None = None,
        llm_proactive: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # P1-53：全文件读-改-写必须加锁 + 原子替换。旧实现无锁、直接 write_text，
        # 4 uvicorn worker 下训练页每次保存都是并发 RMW，互相覆盖半文件风险。
        with cls._CONFIG_LOCK:
            data = cls._read_config_file()
            if quiet_hours is not None:
                data["quiet_hours"] = {"start": int(quiet_hours[0]), "end": int(quiet_hours[1])}
            if follow_up is not None:
                data["follow_up"] = dict(follow_up)
            if llm_proactive is not None:
                cur = data.get("llm_proactive") or {}
                if not isinstance(cur, dict):
                    cur = {}
                cur.update({k: v for k, v in llm_proactive.items() if v is not None})
                data["llm_proactive"] = cur
            vault = data.get("vault") or {}
            if vault_enabled is not None:
                vault["enabled"] = bool(vault_enabled)
            if vault_interval is not None:
                vault["interval_minutes"] = max(10, int(vault_interval))
            data["vault"] = vault
            try:
                cls._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = cls._CONFIG_PATH.with_name(
                    f"{cls._CONFIG_PATH.name}.tmp.{os.getpid()}"
                )
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, cls._CONFIG_PATH)
            except Exception as e:  # noqa: BLE001
                logger.warning("调度器配置保存失败: %s", e)
        return data

    def get_llm_proactive_config(self) -> dict[str, Any]:
        from proactive.llm_proactive import DEFAULT_WEB_CONFIG, read_web_proactive_config

        cfg = read_web_proactive_config()
        qs, qe = self.get_quiet_hours() if hasattr(self, "get_quiet_hours") else (23, 7)
        return {
            **DEFAULT_WEB_CONFIG,
            **cfg,
            "quiet_hours_start": qs,
            "quiet_hours_end": qe,
        }

    def _load_config_file(self) -> None:
        data = self._read_config_file()
        qh = data.get("quiet_hours") or {}
        if "start" in qh and "end" in qh:
            with contextlib.suppress(TypeError, ValueError):
                self._quiet_hours = (int(qh["start"]), int(qh["end"]))
        # 免打扰时段必须同步注入 ASE 引擎：引擎在生成前就要知道静默时段，
        # 否则会「生成→扣配额→投递被丢弃」（2026-09-19 生产事故根因）。
        if self.ase is not None and hasattr(self.ase, "set_quiet_hours"):
            self.ase.set_quiet_hours(self._quiet_hours[0], self._quiet_hours[1])
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
        """检查是否在免打扰时段。

        必须与 ASEEngine._in_quiet_hours 使用同一时钟源 _local_now()。
        系统时区非北京时间时（CI/容器 UTC），datetime.now().hour 会与引擎
        相差 8 小时，导致调度器静默短路失效、引擎却在算静默——配额记账
        与投递判定分裂（v1.13 生产事故的 CI 侧再现）。
        """
        local_hour = _local_now().hour
        start, end = self._quiet_hours
        if start < end:
            return start <= local_hour < end
        return local_hour >= start or local_hour < end

    # 仅写日志、不构成真实送达的通道（开发期观察用）。
    # 它们【不得】计入送达：否则真实通道（微信）失败时会被兜底掩盖成「成功」，
    # 上层据此提交配额。生产实证 2026-09-19：微信接口连续多日返回
    # {"ret": -2, "errmsg": "prepare failed"}（会话窗口失效），日志却始终显示
    # 「主动消息已投递: wechat」—— 因为 console 与旧 send_message_func 都是
    # `logger.info` 包装，必然「成功」。
    _LOG_ONLY_CHANNELS = frozenset({"console"})
    # 真实投递通道（按优先级）
    _REAL_CHANNELS = ("wechat", "websocket")

    async def _send_to_all(self, message: str) -> bool:
        """
        向所有已注册通道发送消息
        优先级: wechat > websocket（console 仅留痕）

        Returns:
            **是否真实送达** —— 任一真实通道成功即为 True。
            仅写日志的兜底通道（console / 旧 `send_message_func`）照常留痕，
            但**不影响返回值**，因此不会导致「没送达却扣配额」。

        2026-09-19：免打扰分支改为**明确说明未投递**。旧日志写「跳过非紧急消息」
        但代码里并不存在紧急消息旁路，措辞掩盖了「消息被丢弃」的事实 ——
        配合「生成时即扣配额」，静默时段静默烧光全天配额而无人察觉。
        """
        if self._is_quiet_hours():
            logger.warning(
                "免打扰时段(%s-%s)：消息未投递（无紧急旁路），内容不计数不扣配额",
                self._quiet_hours[0], self._quiet_hours[1],
            )
            return False

        sent = False
        for name in self._REAL_CHANNELS:
            sender = self._channel_instances.get(name)
            if sender is None:
                # 「通道未就绪」必须可见：旧实现静默 continue，导致
                # 「为什么微信一条都没发」在生产日志里完全无线索
                # （2026-09-19 实测：master 的 wechat 通道为 None，静默跳过后
                #   websocket 零客户端也"成功"，消息从未到达用户）。
                logger.warning("主动消息通道 %s 未就绪（instance=None），跳过", name)
                continue
            try:
                if asyncio.iscoroutinefunction(sender):
                    await sender(message)
                else:
                    sender(message)
                logger.info("主动消息已送达: %s", name)
                sent = True
                break  # 高优先级成功就不再尝试低优先级
            except Exception as e:
                logger.warning("通道投递失败: %s - %s", name, e)
                # 标记失效；通道健康检查（每 60s）会从 factory 重建
                self._channel_instances[name] = None

        # 仅日志通道：留痕，但**不计入送达**
        for name in sorted(self._LOG_ONLY_CHANNELS):
            sender = self._channel_instances.get(name)
            if sender is None:
                continue
            with contextlib.suppress(Exception):
                if asyncio.iscoroutinefunction(sender):
                    await sender(message)
                else:
                    sender(message)

        # 旧 send_message_func 兜底：生产装配里它是 logger.info 包装，同属「仅留痕」
        if not sent and self._send:
            with contextlib.suppress(Exception):
                self._send(message)

        if not sent:
            logger.warning(
                "主动消息未送达任何真实通道（仅留痕），不消耗配额: %.40s", message
            )
        return sent

    def _collect_ase_user_keys(self) -> list[str]:
        """收集需要 tick 的 user_key（完整会话键 / 用户实例键）。"""
        keys: list[str] = []
        seen: set[str] = set()

        def _add(k: str) -> None:
            k = str(k or "").strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)

        try:
            from proactive.ase_hub import ASEHub, load_user_key_index

            if isinstance(self.ase, ASEHub):
                for k in self.ase.known_user_keys():
                    _add(k)
                for k in load_user_key_index():
                    _add(k)
        except Exception:  # noqa: BLE001
            pass

        try:
            from api.deps import deps as _deps

            gf = getattr(_deps, "gf", None)
            if gf is not None and hasattr(gf, "get_all_users"):
                # P0-4-3：禁止裸 user_id 当会话键——裸键引擎无对话数据、紧迫度恒顶格，
                # 投递解析不出目标会演变成跨用户广播。只为有绑定 peer 的用户生成完整键。
                bound: list[str] = []
                if hasattr(gf, "get_bound_wxids"):
                    try:
                        bound = list(gf.get_bound_wxids() or [])
                    except Exception:  # noqa: BLE001
                        bound = []
                for u in gf.get_all_users() or []:
                    uid = str(u.get("user_id") or "")
                    if not uid:
                        continue
                    for key in bound:
                        if ":" in key:
                            left, right = key.split(":", 1)
                            if left == uid and right:
                                _add(f"{uid}:{right}")
        except Exception:  # noqa: BLE001
            pass

        try:
            from wechat_direct.connector_registry import get_registry

            registry = get_registry()
            from api.deps import deps as _deps

            user_mgr = getattr(_deps, "gf", None)
            bound = list(user_mgr.get_bound_wxids()) if user_mgr and hasattr(user_mgr, "get_bound_wxids") else []
            for owner_id, _slot, conn in registry.all():
                if not getattr(conn, "token", ""):
                    continue
                peers: list[str] = []
                for key in bound:
                    if ":" in key:
                        left, right = key.split(":", 1)
                        if left == str(owner_id):
                            peers.append(right)
                    # 裸 wxid 不归属具体 owner，不注入任何通道（隔离）
                for peer in peers:
                    _add(f"{owner_id}:{peer}")
        except Exception:  # noqa: BLE001
            pass

        return keys

    def _check_ase(self) -> None:
        """ASE 主动消息检查（APScheduler同步任务）

        2026-09-21 P1：ASEHub 按 user_key 分引擎 tick，消息**定向投递**到
        该会话，不再一条内容广播给所有人。
        """
        if not self.ase:
            logger.warning("ASE 引擎未注入（components['ase'] 为空），主动消息检查跳过")
            return

        self.reload_config()

        try:
            from proactive.ase_hub import ASEHub

            if isinstance(self.ase, ASEHub):
                self._check_ase_per_user()
                return
            # 兼容：全局单例引擎（旧路径）
            self._check_ase_global()
        except Exception as e:  # noqa: BLE001
            logger.error("ASE check failed: %s", e)
        finally:
            self._last_check_time = datetime.now(tz=timezone.utc)

    def _check_ase_per_user(self) -> None:
        """P1：主动消息由 **LLM 判断**是否开口、说什么（用户裁决：无策略闸）。

        系统仅保证投递正确性：LLM=false 不发；**投递成功才记账**。
        urgency 仅作上下文信号，不作为发送硬闸。
        """
        from proactive.ase_hub import ASEHub

        hub: ASEHub = self.ase  # type: ignore[assignment]
        user_keys = self._collect_ase_user_keys()
        if not user_keys:
            logger.info("ASE tick: 无用户目标，跳过（hub_known=%s）", hub.known_user_keys())
            return
        logger.info("proactive LLM tick per-user: targets=%d %s", len(user_keys), user_keys[:8])
        for user_key in user_keys:
            try:
                self._llm_proactive_one_user(hub, user_key)
            except Exception as e:  # noqa: BLE001
                logger.warning("proactive LLM tick failed user=%s: %s", user_key, e)

    def _llm_proactive_one_user(self, hub: Any, user_key: str) -> None:
        from proactive.ase_engine import _local_now, sanitize_message
        from proactive.llm_proactive import (
            build_proactive_context,
            decide_proactive,
            load_persona_hint,
            read_web_proactive_config,
        )
        from shisi.agent_plane.runtime import (
            append_proactive_event,
            get_profile_prompt_block,
            project_profile_for,
        )

        web_cfg = read_web_proactive_config()
        if not web_cfg.get("enabled", True):
            # P1-51：关闭态账本事件每用户每日至多一条（旧实现每 tick 写一行，
            # enabled=false 反而让 agent_plane.db 涨得最快）
            today = _local_now().date().isoformat()
            if self._disabled_event_day.get(str(user_key)) != today:
                self._disabled_event_day[str(user_key)] = today
                append_proactive_event(session_key=user_key, sent=False, reason="web_disabled")
            return
        # P1-49：静默时段在 LLM 决策**之前**前置闸。旧实现静默只挡投递层，
        # 23:00-07:00 每 5 分钟照调一次远端 LLM（消息不发、token 恒流失）。
        if self._is_quiet_hours():
            logger.debug("proactive LLM 前置静默闸命中 user=%s，跳过决策", user_key)
            return
        # 执行 LLM 上次决策的 wait_minutes（模型自判时机，非策略闸）
        import time as _time

        _now = _time.time()
        _next_ok = self._llm_proactive_next_ok.get(str(user_key))
        if _next_ok and _now < _next_ok:
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="llm_wait_window",
                wait_minutes=int(max(0, (_next_ok - _now) // 60)),
            )
            return

        eng = hub.get(user_key) if hasattr(hub, "get") else None
        hours = eng._hours_since_last_chat() if eng is not None and hasattr(eng, "_hours_since_last_chat") else 0.0
        urgency = None
        if eng is not None:
            try:
                eng.tick(hours, dry_run=True)
                urgency = float(getattr(getattr(eng, "urgency", None), "total", 0.0) or 0.0)
            except Exception:  # noqa: BLE001
                urgency = None
        try:
            profile = project_profile_for(user_key)
        except Exception:  # noqa: BLE001
            profile = {}
        rel = ""
        try:
            block = get_profile_prompt_block(user_key)
            if block:
                rel = block.split("\n", 1)[0][:80]
        except Exception:  # noqa: BLE001
            rel = ""
        # 人设：会话绑定角色优先。P1-48：旧实现读 eng._character_id——该属性
        # 根本不存在（真实为 _knowledge_character_id），异常被吞后 persona 恒 ""
        persona = ""
        try:
            char_id = str(getattr(eng, "_knowledge_character_id", "") or "")
            if not char_id:
                # hub 键兼容 N:peer|char 形态（纯 N:peer 无角色槽则留空，禁空转 glob）
                sk = str(user_key)
                if "|" in sk:
                    tail = sk.split("|")[-1].strip()
                    if tail and not tail.startswith("im.wechat"):
                        char_id = tail
            if not char_id:
                char_id = str(profile.get("character_id") or "")
            persona = load_persona_hint(char_id) if char_id else ""
        except Exception:  # noqa: BLE001
            persona = ""
        now = _local_now()
        quiet = None
        try:
            quiet = self.get_quiet_hours() if hasattr(self, "get_quiet_hours") else None
        except Exception:  # noqa: BLE001
            quiet = None
        ctx = build_proactive_context(
            session_key=str(user_key),
            hours_since_last_chat=float(hours or 0.0),
            local_time=now.strftime("%Y-%m-%d %H:%M %A"),
            profile=profile,
            relationship_hint=rel,
            urgency_signal=urgency,
            persona_hint=persona,
            web_config=web_cfg,
            quiet_hours=quiet,
        )
        llm = self._resolve_proactive_llm(eng)
        if llm is None:
            logger.info("proactive LLM unavailable user=%s skip", user_key)
            append_proactive_event(session_key=user_key, sent=False, reason="llm_unavailable")
            return
        decision = decide_proactive(llm, ctx)
        logger.info(
            "proactive LLM decision user=%s should=%s wait=%s reason=%s",
            user_key,
            decision.get("should_contact"),
            decision.get("wait_minutes"),
            (decision.get("reason") or "")[:80],
        )
        wait_m = decision.get("wait_minutes")
        if isinstance(wait_m, int) and wait_m > 0:
            self._llm_proactive_next_ok[str(user_key)] = _now + float(wait_m) * 60.0
        if not decision.get("should_contact"):
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason=str(decision.get("reason") or "llm_false"),
                wait_minutes=decision.get("wait_minutes"),
            )
            return
        message = sanitize_message(str(decision.get("message") or ""))
        if not message:
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="empty_message_after_sanitize",
                wait_minutes=decision.get("wait_minutes"),
            )
            return
        if self._deliver(message, session_key=user_key):
            if eng is not None and hasattr(eng, "commit_sent"):
                import contextlib

                with contextlib.suppress(Exception):
                    eng.commit_sent(
                        {
                            "message": message,
                            "type": "llm_proactive",
                            "reason": decision.get("reason") or "",
                        }
                    )
            append_proactive_event(
                session_key=user_key,
                sent=True,
                message=message,
                reason=str(decision.get("reason") or ""),
                wait_minutes=decision.get("wait_minutes"),
            )
            logger.info("proactive LLM delivered user=%s %s", user_key, message[:40])
            self._deliver_fail_counts.pop(str(user_key), None)
        else:
            # P1-22：投递失败指数退避（5m→15m→45m→2h 封顶），成功送达即清零。
            # 旧实现失败侧无任何 backoff：不可达用户每 5 分钟「生成→失败→再生成」，
            # 一天 288 次 LLM 零投递（v1.13 把记账移到投递后是对的，但没补退避）。
            fails = self._deliver_fail_counts.get(str(user_key), 0) + 1
            self._deliver_fail_counts[str(user_key)] = fails
            backoff_min = min(5 * (3 ** (fails - 1)), 120)
            self._llm_proactive_next_ok[str(user_key)] = _time.time() + backoff_min * 60.0
            logger.info(
                "proactive 投递失败 user=%s 连续第%d次 → 退避 %d 分钟",
                user_key, fails, backoff_min,
            )
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason=f"deliver_failed_backoff_{backoff_min}m",
                message=message[:80],
                wait_minutes=decision.get("wait_minutes"),
            )

    def _check_ase_global(self) -> None:
        """旧全局 ASE 单例路径（兼容；新装配走 ASEHub）。"""
        try:
            if self._get_last_chat_time:
                last_chat = self._get_last_chat_time()
                hours = (datetime.now(tz=timezone.utc) - last_chat).total_seconds() / 3600 if last_chat else 99.0
            elif hasattr(self.ase, "_hours_since_last_chat"):
                hours = self.ase._hours_since_last_chat()
            else:
                hours = self._hours_since_last_check()

            is_online = True
            if self._is_online_check:
                is_online = self._is_online_check()

            if not is_online:
                if hasattr(self.ase, 'tick'):
                    self.ase.tick(hours, dry_run=True)
                return

            if self._is_quiet_hours():
                if hasattr(self.ase, 'tick'):
                    self.ase.tick(hours, dry_run=True)
                logger.info(
                    "ASE tick: 免打扰时段(%02d-%02d)静默，仅累积紧迫度 "
                    "hours=%.2f urgency=%.2f daily_count=%d reason=quiet_hours",
                    self._quiet_hours[0], self._quiet_hours[1], hours,
                    getattr(getattr(self.ase, "urgency", None), "total", -1.0),
                    getattr(self.ase, "_daily_message_count", -1),
                )
                return

            result = self.ase.tick(hours)
            logger.info(
                "ASE tick: hours=%.2f urgency=%.2f daily_count=%d paused=%s "
                "result=%s reason=%s",
                hours,
                getattr(getattr(self.ase, "urgency", None), "total", -1.0),
                getattr(self.ase, "_daily_message_count", -1),
                getattr(self.ase, "_paused", None),
                bool(result),
                getattr(self.ase, "_last_skip_reason", "") or ("ok" if result else "unknown"),
            )
            if result:
                message = result.get("message", "")
                logger.info("ASE triggered: [%s] %s", result.get("type"), message)
                if self._deliver(message) and hasattr(self.ase, "commit_sent"):
                    self.ase.commit_sent(result)
        except Exception as e:  # noqa: BLE001
            logger.error("ASE global check failed: %s", e)

    def set_delivery_loop(self, getter: Callable[[], Any]) -> None:
        """P1-23：装配层注入「通道资产所属事件循环」的 getter（如 ws 线程 loop）。

        调度任务跑在 APScheduler worker 线程；WebSocketServer 的连接与
        asyncio.Lock 属另一条循环。旧实现直接 asyncio.run 在新循环里 await
        这些资产 = 跨循环未定义行为：轻则误判未送达，重则 future 永不
        resolve、占死 executor worker（ase_check 无 max_instances）。
        """
        self._delivery_loop_getter = getter

    def _run_blocking(self, coro_factory: Callable[[], Any]) -> Any:
        """把协程桥到投递循环执行（无注入循环时退回独立循环）。"""
        loop = None
        if self._delivery_loop_getter is not None:
            with contextlib.suppress(Exception):
                loop = self._delivery_loop_getter()
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
                return fut.result(timeout=90)
            raise RuntimeError("投递循环与当前运行循环相同，阻塞等待必死锁")
        return asyncio.run(coro_factory())

    def _deliver(self, message: str, session_key: str | None = None) -> bool:
        """投递主动消息。session_key 非空时**定向**到该会话，否则广播（旧路径）。"""
        try:
            delivered = bool(self._run_blocking(lambda: self._send_targeted(message, session_key)))
            if delivered:
                self._record_outbound(message, session_key)
            return delivered
        except Exception as e:  # noqa: BLE001
            logger.error("主动消息投递失败: %s", e)
            if self._send:
                try:
                    self._send(message)
                    self._record_outbound(message, session_key)
                    return True
                except Exception:  # noqa: BLE001
                    logger.exception("主动消息兜底发送失败")
            return False

    @staticmethod
    def _is_wechat_session_key(session_key: str) -> bool:
        sk = str(session_key or "")
        if "@im.wechat" in sk:
            return True
        if ":" in sk:
            left, right = sk.split(":", 1)
            return left.isdigit() and bool(right.strip())
        return False

    async def _send_targeted(self, message: str, session_key: str | None = None) -> bool:
        if self._is_quiet_hours():
            logger.warning(
                "免打扰时段(%s-%s)：消息未投递（无紧急旁路），内容不计数不扣配额",
                self._quiet_hours[0], self._quiet_hours[1],
            )
            return False

        if session_key:
            sender = self._channel_instances.get("wechat")
            if sender is not None:
                try:
                    if asyncio.iscoroutinefunction(sender):
                        await sender(message, session_key=session_key)
                    else:
                        sender(message, session_key=session_key)
                    logger.info("主动消息已定向投递: wechat session=%s", session_key)
                    return True
                except TypeError as e:
                    # P1-21：通道不收 session_key（旧签名）——定向消息**绝不**
                    # 静默转广播（A 的私信发给全员 = 跨用户泄漏），直接判失败。
                    # 旧实现此处回退 sender(message) 广播并记「定向投递成功」。
                    logger.warning(
                        "wechat 通道不接受 session_key，定向消息拒绝降级为广播: session=%s %s",
                        session_key, e,
                    )
                    return False
                except Exception as e:  # noqa: BLE001
                    logger.warning("定向投递失败 session=%s: %s", session_key, e)
                    self._channel_instances["wechat"] = None
            if self._is_wechat_session_key(session_key):
                # P1-21：微信会话键在 wechat 通道缺失/失败时直接返回 False。
                # 旧实现落到 _send_to_all —— instance=None 时整段跳过（微信形态
                # 零送达却继续白烧 LLM），有实例时变跨用户广播 + 记「定向成功」。
                logger.warning("微信定向投递未成功（通道不可用），不回退广播: session=%s", session_key)
                return False
            # 非微信（web 会话）定向：退回 ws 广播（在线面板，无跨微信用户问题）
            return await self._send_to_all(message)

        return await self._send_to_all(message)

    def _run_memory_curator(self) -> None:
        """AX P2：夜间记忆整理（垃圾归档 / near-dup 合并 / 账本事件）。"""
        try:
            from shisi.agent_plane.curator import run_curator_all_known

            orch = None
            try:
                from api.deps import deps

                orch = getattr(deps, "orch", None)
            except Exception:  # noqa: BLE001
                orch = None
            sm = None
            llm = self._llm_provider
            if orch is not None:
                comps = getattr(orch, "components", None) or {}
                mem = comps.get("memory")
                sm = getattr(mem, "structured_memory", None) or getattr(mem, "_sm", None)
                if llm is None:
                    llm = comps.get("llm")
            if sm is None:
                logger.info("memory curator: structured_memory 不可用，跳过")
                return
            result = run_curator_all_known(sm, llm=llm)
            logger.info("memory curator done: %s", result.get("sessions"))
        except Exception as e:  # noqa: BLE001
            logger.warning("memory curator failed: %s", e)
        # P1-51：账本保留（30 天）——旧实现 event_ledger 只 append 不 prune，
        # chat/tool/profile/web_disabled 全类型常驻 → agent_plane.db 无界增长。
        try:
            from shisi.agent_plane.event_ledger import default_ledger

            removed = default_ledger().prune(retention_days=30)
            if removed:
                logger.info("event_ledger 保留清理：删除 %d 条 30 天前事件", removed)
        except Exception as e:  # noqa: BLE001
            logger.warning("event_ledger prune failed: %s", e)

    def _run_daily_maintenance(self) -> None:
        """每日维护"""
        if self._daily_maintenance:
            try:
                self._daily_maintenance()
                logger.info("Daily maintenance completed")
            except Exception as e:  # noqa: BLE001
                logger.error("Daily maintenance failed: %s", e)

        # 情感时间衰减 — 审计 item45：打向**每用户存活引擎**（UserManager 真态）。
        # 旧实现打在 orchestrator 模板引擎上，其 state 无任何读者=功能不存在。
        try:
            hours = self._hours_since_last_check()
            if hours > 0:
                from api.deps import deps as _deps
                user_mgr = getattr(_deps, "gf", None)
                decayed = 0
                if user_mgr is not None and hasattr(user_mgr, "apply_time_decay_all"):
                    decayed = user_mgr.apply_time_decay_all(hours)
                logger.info(
                    "情感时间衰减已应用: %.2f 小时，覆盖 %d 个用户引擎", hours, decayed
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("情感时间衰减任务失败: %s", e)

        # 好感度衰减 — 对所有已记录键（含 user×character）应用每日衰减
        try:
            from api.deps import deps as _deps
            shisi_reg = getattr(_deps, "shisi_reg", None)
            if shisi_reg is not None:
                enhancer = getattr(shisi_reg, "affinity_enhancer", None)
                if enhancer is not None:
                    if hasattr(enhancer, "decay_all"):
                        total_decay = enhancer.decay_all()
                        if total_decay > 0:
                            logger.info("好感度衰减完成 total=%.2f", total_decay)
                    else:
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
        """候选 D：命中重要日期时以角色口吻发送祝福（LLM 生成，模板兜底）。

        ⚠️ 2026-09-19 修复：本方法原先**只**由 `_run_daily_maintenance`（00:05）
        调用，而免打扰时段默认 23-7 —— 00:05 恒在静默内，`_send_to_all()` 直接
        返回 False，祝福被无声吞掉。即：生日/纪念日祝福从未送达过。
        现改为幂等 + 静默跳过，并由独立的每小时任务（见 start()）在静默结束后
        第一时间补发，当日只发一次。
        """
        if self._is_quiet_hours():
            logger.info(
                "[重要日期] 处于免打扰时段(%02d-%02d)，等待静默结束后补发",
                self._quiet_hours[0], self._quiet_hours[1],
            )
            return
        try:
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

            # P1-24：与 _is_quiet_hours 共用 _local_now()（北京墙钟）。旧实现把裸
            # _dt.now()（依赖主机时区）显式喂给 v1.21 刚收口为 now_local 的
            # check_today，dedup_key 也不同钟——UTC 主机上北京 00:00-07:59 命中的
            # 祝福被算成前一天（换机即静默失效类）。
            _now_local = _local_now()
            hits = check_today(active_id, _now_local)
            if not hits:
                return

            names = "、".join(h.get("name", "") for h in hits)
            kinds = "/".join(sorted({h.get("kind", "custom") for h in hits}))
            # 当日幂等：每小时任务与 00:05 维护都可能命同一天，避免重复轰炸
            dedup_key = f"{_now_local:%Y-%m-%d}|{active_id}|{names}"
            if dedup_key in self._important_dates_sent:
                return

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
                    cleaned = sanitize_message(str(polished or ""))
                    if cleaned:
                        message = cleaned
            except Exception:
                pass

            logger.info("[重要日期] 命中 %s，发送祝福", names)
            if self._deliver(message):
                self._important_dates_sent.add(dedup_key)
                logger.info("[重要日期] 祝福已送达: %s", names)
            else:
                logger.warning("[重要日期] 祝福未送达，将在下一个非静默小时重试: %s", names)
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
