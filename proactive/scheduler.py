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
import math
import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

# 消息清洗（拦截 LLM 推理过程泄漏为消息内容）+ 统一本地时钟。
# proactive.ase_engine 不反向依赖 scheduler，不存在循环导入。
# ⚠️ 静默时段判定必须与 ASE 引擎共用 _local_now()：引擎在非 UTC+8 主机
# （GitHub Actions / 容器默认 UTC）会强制换算到北京时间，若调度器仍用
# datetime.now().hour，两边小时数差 8，静默短路会静默失效（2026-09-19 CI 实证）。
from proactive.ase_engine import _local_now, sanitize_message
from utils import character_resolver, json_state
from utils import session_key as session_key_mod
from utils.project_paths import project_path

# 回复模式单一真源（沉浸式/小说式）。出站守卫需要它来决定是否剥括号动作段
# ——不传时 terminal_reply_guard 的 reply_mode 缺省空串，沉浸式括号会原样
# 发给用户（2026-10-21 FIX-1：主动链「把带括号的示例喂进 prompt 又不拦」）。
from utils.reply_mode import read_reply_mode

logger = logging.getLogger("scheduler")


def apply_wait_jitter(
    now: float,
    wait_minutes: int,
    jitter_seconds: float,
    *,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    """模型等待窗 + 随机抖动（T-4，2026-10-27「仿真人机制」）。

    ``jitter_seconds <= 0`` → 行为与旧实现逐位一致（``now + wait*60``）；
    否则在 ``[0, jitter_seconds]`` 均匀取一个正向偏移——主动消息不再精确
    卡在「模型说的第 N 分钟」这种机器节奏上。随机源可注入，测试可确定。
    """
    base = float(now) + float(wait_minutes) * 60.0
    if jitter_seconds <= 0:
        return base
    return base + float(rng(0.0, float(jitter_seconds)))


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


def _classify_deliver_error(err: str) -> str:
    """投递失败原因分类（2026-10-17 批，纯函数）。

    窗口拒收/通道掉线/静默时段/其他异常是几种不同的病——生产实证连续 11
    次失败全是「平台窗口拒收」，旧账本只有 backoff 阶梯看不出病型。返回
    账本 reason 后缀（空串=无分类）。
    """
    e = str(err or "")
    if not e:
        return ""
    if "send_api_rejected" in e or "prepare failed" in e:
        return "|window_rejected"
    if "channel_unavailable" in e or "通道不可用" in e:
        return "|channel_down"
    if "quiet_hours" in e:
        return "|quiet_hours"
    return "|send_error"

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
    ):
        self.ase = ase_engine
        self._send = send_message_func
        self._daily_maintenance = daily_maintenance_func
        # LLM 主动决策（用户裁决：时机与内容由模型判断，无策略闸）
        self._llm_provider: Any | None = None
        self._session_llm_resolver: Callable | None = None
        # 自问自答根治：主动消息送达后回写历史需要记忆服务
        self._memory: Any | None = None
        # AX 审查 B3：执行 LLM 自己给出的 wait_minutes（非硬编码日程表）
        # ── 节流账本（三步：init → _load_throttle_ledger → 变更时 _persist）──
        # 🔴 块E（2026-09-22 二次根治）：这四个账本此前**只存内存**，进程重启
        # 即归零。后果按账本分述：
        #   · `_llm_proactive_next_ok`：LLM 自判的 wait_minutes 与**投递失败的
        #     指数退避**一起丢 ⇒ 重启后立刻重试 → 形成「重启即打满」的循环。
        #   · `_deliver_fail_counts`：退避阶梯从 0 重新开始（5m 永远重新开始，
        #     永远升不到 120m 封顶）——不可达用户依旧每 5 分钟烧一次 LLM。
        #   · `_disabled_event_day`：每用户每日去重失效 ⇒ 重启当日再写一行。
        #   · `_important_dates_sent`：当日已发祝福重启后再发一遍（重复祝福）。
        # 现统一落盘到 `data/scheduler_config.json` 的 `throttle` 段（走
        # `utils.json_state` 原子写 + flock，与 `last_emotion_decay` 同源）。
        self._llm_proactive_next_ok: dict[str, float] = {}
        #: 决策线最近对话摘要（供 post-draft judge 使用，投递时弹出，不累积）。
        self._judge_recent: dict[str, list[str]] = {}
        # P1-22：投递连续失败计数（指数退避；旧实现失败零退避，
        # 不可达用户每 5 分钟「生成→失败→再生成」，一天 288 次 LLM 零投递）
        self._deliver_fail_counts: dict[str, int] = {}
        # P1-23：通道资产（WebSocketServer 连接/锁）所属事件循环的 getter，
        # 由装配层注入——调度线程直接 asyncio.run 跨循环调用属未定义行为
        self._delivery_loop_getter: Callable[[], Any] | None = None
        # P1-51：web_disabled 账本事件每用户每日至多一条（旧实现每 tick 写一行，
        # 关闭态反而涨得最快）
        self._disabled_event_day: dict[str, str] = {}
        # skip 账本治理（2026-10-12）：llm_wait_window（96% 账本噪声）与静默闸
        # （现状只 debug 不落账）套用同款每用户每日至多一条口径。与上面三个
        # 账本同走 init/load/persist/purge 四处同步纪律。
        self._wait_window_event_day: dict[str, str] = {}
        self._quiet_event_day: dict[str, str] = {}
        # 模型闸（2026-10-15 质量回调批）：model_degraded 账本事件每用户每日
        # 至多一条（与 quiet_hours 同口径噪声治理）。与上面各账本同走
        # init/load/persist/purge 四处同步纪律。
        self._model_degraded_event_day: dict[str, str] = {}
        # 确定性节奏闸账本治理（2026-10-17 批）：min_interval /
        # cooldown_after_reply / unanswered_limit / token_window_dead /
        # token_window_dying 这类**确定性** skip 按 「user|reason」每日至多
        # 一条（生产实证：unanswered_limit 每 5 分钟一行刷屏，P1-51 的每日
        # 治理当时只覆盖了 wait_window/quiet/disabled 三个 reason）。
        self._gate_skip_event_day: dict[str, str] = {}
        # 投递失败最近一次底层原因（供账本分类入账；_send_targeted 写入）。
        self._last_deliver_error: str = ""

        self._scheduler: Any = None
        self._active_tasks: dict[str, bool] = {}

        # 情绪时间衰减的独立基准（2026-09-22 修复，同日二次根治）。
        # 旧实现误用「距上次 ASE tick」当衰减时长——ASE 每 5 分钟跑一次并刷新
        # 基准，每日维护时刻 hours≈0.08，`apply_time_decay`（线性按小时）形同
        # 关闭，用户级引擎情绪永不冷却。
        # 🔴 首版修复（同日 9a54fca）以 `None` 作「本进程尚未衰减」哨兵，而
        # `_hours_since_last_emotion_decay()` 对 None 返回 0.0、`if hours > 0`
        # 永假、**推进基准的赋值语句又在该 if 内** —— 基准永远停在 None，
        # 衰减依然从未发生（换了一种死法，生产实证 00:05 日志 hours=0.01）。
        # 现改为**落盘 + 缺失即回填**：
        #   · 真源 = `data/scheduler_config.json` 的 `last_emotion_decay`
        #     （跨进程/重启一致，走 utils.json_state 原子写 + flock）；
        #   · 文件缺失/损坏 → 回填为「昨日维护时刻」，使下次维护得到 24h
        #     量级时长（而非 0），衰减立刻生效且不重复计（离线冷却另由引擎
        #     restore 按真实离线时长承担）。
        self._last_emotion_decay: datetime | None = self._load_last_emotion_decay()

        # 通道注册表（支持多通道投递）
        self._channels: dict[str, Callable[[], Any]] = {}        # name → sender_factory
        self._channel_instances: dict[str, Callable | None] = {}  # name → instantiated sender
        self._health_check_interval = 60  # 秒
        # 提醒到期投递任务（api 装配层注入；每分钟轮询，豁免静默时段）
        self._reminder_task: Callable[[], None] | None = None
        self._quiet_hours = (23, 7)       # 23:00-07:00 免打扰（web 端可调）
        # 块4（缺陷 D）：**暂停**此前只存在于 `orch._ase._paused`（单 worker 内存），
        # 生产 4 uvicorn worker 下 POST 落在任一 worker、调度器只在 master ——
        # 于是「按了暂停，她照发」；重启亦归零。现与 quiet_hours 同级：文件为真源、
        # master 每 tick 重载、LLM 决策**之前**生效。
        self._paused = False
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
        # 节流账本从同一真源回填（必须在 _load_config_file 之后：两者共用
        # `_CONFIG_PATH`，且账本不参与上面那几个开关的同步逻辑）。
        self._load_throttle_ledger()

        logger.info("ProactiveScheduler initialized (APScheduler=%s)", HAS_APSCHEDULER)

    def set_llm_provider(self, llm: Any | None) -> None:
        """注入 LLM，供主动消息决策（P1：LLM 判时机与文案）。"""
        self._llm_provider = llm

    def set_session_llm_resolver(self, resolver: Callable) -> None:
        self._session_llm_resolver = resolver

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

    def _record_outbound(
        self, message: str, session_key: str | None, character_id: str | None = None,
    ) -> None:
        """定向投递成功后把这句写进该会话历史（无会话键的广播无法归属，不写）。

        🔴 2026-09-22：必须带 **character_id**。出站 assistant 行此前无归属，
        而角色过滤含 `OR character_id = ''`（空归属恒保留）→ 切角色后新角色
        把上一角色的主动消息当成自己说过的。
        """
        if not session_key:
            return
        mem = self._resolve_memory()
        record = getattr(mem, "record_outbound_message", None)
        if record is None:
            return
        try:
            record(
                message=message,
                session_id=str(session_key),
                character_id=self._resolve_character_id(str(session_key)) if character_id is None else character_id,
                channel="proactive",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("主动消息回写历史失败 session=%s: %s", session_key, e)

    @staticmethod
    def _resolve_character_id(session_key: str) -> str:
        """会话键 → 角色 id（唯一 owner：utils.character_resolver）。

        `"default"`（内置十四）是合法值非"解析失败"；解析不到时回落内置卡。
        """
        try:
            from api.deps import deps

            gf = getattr(deps, "gf", None)
        except Exception:  # noqa: BLE001
            gf = None
        cid = character_resolver.resolve_character_id(session_key, gf)
        if character_resolver.is_builtin(cid):
            return session_key_mod.character_suffix_of(session_key) or cid
        return cid

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

    def _primary_model_ok(self) -> bool:
        """首选供应商（链首位）是否可用：熔断开闸期间主动消息不生成。

        2026-10-15 质量回调批：生产实证 agnes 熔断窗内全量流量降级免费档
        智谱，主动消息出客服腔——宁可不发也不让降级模型代写（用户裁决）。
        网关无该方法（旧对象/stub）→ True（不拦，向后兼容）。
        ⚠️ getattr 防御：既有测试以 __new__ 构造 stub 不设 _llm_provider，
        真实实例 __init__ 必然有该属性。
        """
        llm = getattr(self, "_llm_provider", None)
        if llm is None:
            with contextlib.suppress(Exception):
                from api.deps import deps
                comps = getattr(getattr(deps, "orch", None), "components", None)
                if isinstance(comps, dict):
                    llm = comps.get("llm")
        if llm is None:
            return True
        try:
            status = getattr(llm, "primary_provider_status", None)
            if not callable(status):
                return True
            return bool(status()[1])
        except Exception:  # noqa: BLE001
            return True

    def _record_model_degraded(self, user_key: str, character_id: str = "") -> None:
        """model_degraded 账本落账（每用户每日至多一条，与 quiet_hours 同口径）。

        两处共用（2026-10-16 三连同义批 A）：① 事前闸 `_primary_model_ok` 命中；
        ② 决策调用 allow_fallback=False 抛 ProviderError（in-call 降级被禁）——
        事前闸只覆盖「tick 时已熔断」，覆盖不到「调用当场超时、链内静默降级」。
        getattr 防御：__new__ stub 只补部分账本属性（真实实例 __init__ 必有）。
        时钟用函数内局部导入（与 `_llm_proactive_one_user` 同规）——测试对
        `ase_engine._local_now` 的 monkeypatch 才能生效。
        """
        from proactive.ase_engine import _local_now

        today_m = _local_now().date().isoformat()
        degraded_day = getattr(self, "_model_degraded_event_day", None)
        if degraded_day is None:
            degraded_day = self._model_degraded_event_day = {}
        if degraded_day.get(str(user_key)) != today_m:
            degraded_day[str(user_key)] = today_m
            from shisi.agent_plane.runtime import append_proactive_event

            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="model_degraded",
                character_id=character_id or self._resolve_character_id(str(user_key)),
            )
            self._persist_throttle_ledger()
        logger.debug("proactive 首选模型不可用（禁降级跳过）user=%s", user_key)

    def _emit_skip_event_daily(
        self,
        user_key: str,
        reason: str,
        *,
        character_id: str = "",
        wait_minutes: int | None = None,
        message: str = "",
    ) -> bool:
        """确定性 skip 账本落账（**每 user|reason 每日至多一条**，2026-10-17 批）。

        生产实证：unanswered_limit 硬闸命中期间每 5 分钟 tick 写一条完全相同的
        skip 事件（一天 288 行零信息量），把账本里真正有信号的事件（决策结果/
        投递失败）淹掉——与 P1-51 治理 llm_wait_window 前的 96% 噪声同型。
        口径与 `_record_model_degraded` 一致：首日首条落账，当日后续命中只
        留 debug；账本变更即落盘。返回是否真落了账（测试钉降噪语义用）。
        """
        from proactive.ase_engine import _local_now

        today_s = _local_now().date().isoformat()
        dedup_key = f"{user_key}|{reason}"
        day_map = getattr(self, "_gate_skip_event_day", None)
        if day_map is None:
            day_map = self._gate_skip_event_day = {}
        if day_map.get(dedup_key) == today_s:
            logger.debug("proactive 确定性闸命中（当日已落账，降噪跳过）user=%s reason=%s", user_key, reason)
            return False
        day_map[dedup_key] = today_s
        from shisi.agent_plane.runtime import append_proactive_event

        append_proactive_event(
            session_key=user_key,
            sent=False,
            reason=reason,
            wait_minutes=wait_minutes,
            message=message,
            character_id=character_id or self._resolve_character_id(str(user_key)),
        )
        self._persist_throttle_ledger()
        return True

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
                self._safe_job_wrapper(self._run_reminder_check, "reminder_check"),
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
            # 缺陷 A 收口：这里是全部后台任务的唯一咽喉 —— 谁真的跑过 job，谁
            # 才证明"驻留运行时活着"。四 worker 部署里只有 master 会走到这里，
            # 于是别的过程第一次能跨进程读出「调度主在跑 / 多久没跳 / 宿主是谁」。
            self._beat_resident_runtime()
            try:
                return job_fn(*args, **kwargs)
            except Exception as e:
                logger.error("Scheduled job '%s' failed: %s", job_name, e, exc_info=True)
        return wrapper

    def _beat_resident_runtime(self) -> None:
        """向控制面续驻留心跳；控制面缺席（单进程开发）时静默跳过。"""
        rp = self._plane()
        if rp is None:
            return
        try:
            rp.runtime_beat("scheduler")
        except Exception as e:  # noqa: BLE001
            logger.debug("驻留运行时心跳失败: %s", e)

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
                    self._safe_job_wrapper(self._run_reminder_check, "reminder_check"),
                    IntervalTrigger(minutes=1),
                    id="reminder_check",
                    name="提醒到期投递检查",
                    replace_existing=True,
                    misfire_grace_time=30,
                    coalesce=True,
                )

            # 8. 热点知识采集（每60分钟）
            #    开关与真实间隔由 config/hot_topics.yaml 单一真源判——
            #    collect_if_due 内部自限速，注册频率高于配置间隔只是空转 skip。
            self._scheduler.add_job(
                self._safe_job_wrapper(self._run_hot_topics_collect, "hot_topics_collect"),
                IntervalTrigger(minutes=60),
                id="hot_topics_collect",
                name="热点知识采集",
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=True,
            )

            # 9. 跨 worker 控制命令消费（每5秒；W3 缺陷 E）
            #    API worker 里 3/4 没有调度器（run_api 选主把非 master 的
            #    components['scheduler'] 置 None），手动发送只能落持久命令；
            #    本进程是 master，执行后回写**真实回执**（受理才算数）。
            self._scheduler.add_job(
                self._safe_job_wrapper(self._drain_control_commands, "control_commands"),
                IntervalTrigger(seconds=5),
                id="control_commands",
                name="控制命令消费",
                replace_existing=True,
                misfire_grace_time=10,
                coalesce=True,
                max_instances=1,
            )

            self._scheduler.start()
            # 装配完成当场打一拍：最快的心跳 job 也要等 5 分钟才首跑，新 master
            # 若等首拍才有心跳，这段窗口会被读成「驻留运行时死了」。
            self._beat_resident_runtime()
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
        data = json_state.read_json(cls._CONFIG_PATH, default={})
        return data if isinstance(data, dict) else {}

    @classmethod
    def write_config_file(
        cls,
        quiet_hours: tuple[int, int] | None = None,
        vault_enabled: bool | None = None,
        vault_interval: int | None = None,
        follow_up: dict[str, Any] | None = None,
        llm_proactive: dict[str, Any] | None = None,
        proactive_paused: bool | None = None,
    ) -> dict[str, Any]:
        # P1-53 续（2026-09-21）：读-改-写必须**跨进程**互斥。旧实现只持
        # `cls._CONFIG_LOCK`（进程内 threading.Lock），而本文件是 4 个 uvicorn
        # worker 共读的跨 worker 真源 —— 多 worker 同时保存仍是陈旧快照互相覆盖，
        # 注释宣称的「加锁」在生产拓扑下不成立。现委托 `utils.json_state`
        # （flock + os.replace），全项目状态文件统一走这一个 owner。
        def _mutate(data: dict[str, Any]) -> None:
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
            if not isinstance(vault, dict):
                vault = {}
            if vault_enabled is not None:
                vault["enabled"] = bool(vault_enabled)
            if vault_interval is not None:
                vault["interval_minutes"] = max(10, int(vault_interval))
            data["vault"] = vault
            if proactive_paused is not None:
                blk = data.get("proactive")
                if not isinstance(blk, dict):
                    blk = {}
                blk["paused"] = bool(proactive_paused)
                data["proactive"] = blk

        try:
            return json_state.update_json(cls._CONFIG_PATH, _mutate)
        except Exception as e:  # noqa: BLE001
            logger.warning("调度器配置保存失败: %s", e)
            return cls._read_config_file()

    def get_proactive_jitter_seconds(self) -> float:
        """主动等待窗抖动上限（T-4，2026-10-27）：默认 120s，0 = 关闭。

        真源：``data/scheduler_config.json`` → ``llm_proactive.jitter_seconds``
        （热读；坏值/缺失回落默认；上限 900s 防手改放大成「永远不回」）。
        """
        try:
            from utils import json_state

            data = json_state.read_json(self._CONFIG_PATH, default={})
            block = data.get("llm_proactive") if isinstance(data, dict) else None
            value = block.get("jitter_seconds", 120) if isinstance(block, dict) else 120
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 120.0
            return max(0.0, min(900.0, float(value)))
        except Exception:  # noqa: BLE001
            return 120.0
        from proactive.llm_proactive import DEFAULT_WEB_CONFIG, read_web_proactive_config

        cfg = read_web_proactive_config()
        qs, qe = self.get_quiet_hours() if hasattr(self, "get_quiet_hours") else (23, 7)
        return {
            **DEFAULT_WEB_CONFIG,
            **cfg,
            "quiet_hours_start": qs,
            "quiet_hours_end": qe,
        }

    # ── 迟到容忍窗（爱语 ILinkService.j():710-740 [目标, 目标+窗] 语义对齐）──

    #: ``humanize.proactive_window_minutes`` 缺省 10 分钟（爱语键位）。
    _PROACTIVE_WINDOW_DEFAULT_MINUTES = 10
    #: 窗口时长的读侧闭区间；越界/类型不符 → 回落默认
    #: （与 ``utils/humanize_config`` 逐字段容错同口径，不让一个手改坏的
    #: 真源文件把决策链炸掉）。
    _PROACTIVE_WINDOW_MIN_MINUTES = 1
    _PROACTIVE_WINDOW_MAX_MINUTES = 24 * 60

    def _proactive_window_config(self) -> tuple[bool, int]:
        """热读 ``data/scheduler_config.json`` 的 ``humanize.proactive_window_*``。

        返回 ``(是否启用, 窗口分钟数)``；默认 ``(False, 10)`` = **窗口关闭**，
        保持「到点即触发」现状（零行为变更，见 :meth:`_llm_proactive_one_user`
        的窗判定注释）。读侧**每次热读、无进程缓存**（照
        ``utils/humanize_config.read_humanize`` 的 4-worker 口径：任一进程
        原子写改键后，其他进程下一次决策立即看到）。
        """
        try:
            data = self._read_config_file() or {}
        except Exception:  # noqa: BLE001
            return False, self._PROACTIVE_WINDOW_DEFAULT_MINUTES
        block = data.get("humanize")
        if not isinstance(block, dict):
            return False, self._PROACTIVE_WINDOW_DEFAULT_MINUTES
        enable = block.get("proactive_window_enable")
        enabled = enable if isinstance(enable, bool) else False
        minutes = block.get("proactive_window_minutes")
        if (
            isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or not (self._PROACTIVE_WINDOW_MIN_MINUTES <= minutes <= self._PROACTIVE_WINDOW_MAX_MINUTES)
        ):
            minutes = self._PROACTIVE_WINDOW_DEFAULT_MINUTES
        return enabled, minutes

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
        # 块4（缺陷 D）：暂停同样以文件为准回填，并按当前值同步引擎侧闸门
        # （引擎内那份 `_paused` 只是 tick 级闸，LLM 决策链要的是调度器这份）。
        proactive_blk = data.get("proactive")
        if isinstance(proactive_blk, dict) and "paused" in proactive_blk:
            self._paused = bool(proactive_blk["paused"])
            self._sync_paused_to_engines()
        vault = data.get("vault") or {}
        if "enabled" in vault:
            self._vault_enabled = bool(vault["enabled"])
        if "interval_minutes" in vault:
            self._vault_interval_min = max(10, int(vault["interval_minutes"]))

    # ── 暂停（web 开关；跨 worker / 重启一致的主动消息总闸）──

    def set_paused(self, paused: bool) -> bool:
        """暂停/恢复主动消息。**只**改暂停开关，绝不借道改写频率参数
        （DECISION_LEDGER:118 明令禁止用「暂停」恢复硬频率闸）。"""
        self._paused = bool(paused)
        self.write_config_file(proactive_paused=self._paused)
        self._sync_paused_to_engines()
        logger.info("主动消息暂停开关: paused=%s", self._paused)
        return self._paused

    def is_paused(self) -> bool:
        return bool(self._paused)

    def _sync_paused_to_engines(self) -> None:
        """把暂停态扇出到 ASE 引擎（tick 级闸与调度级闸同一口径）。"""
        if self.ase is None or not hasattr(self.ase, "apply_runtime_config"):
            return
        with contextlib.suppress(Exception):
            self.ase.apply_runtime_config(paused=self._paused)

    @staticmethod
    def _plane():
        """控制面 ``proactive.runtime_plane`` —— fail-soft，不可用时返回 None。

        与 ``api.run_api._plane`` / ``wechat_direct.wechat_connector._plane`` 同口径：
        缺控制面只意味着「没有跨 worker 命令面」，本机手动发送仍可直投。
        """
        try:
            from proactive import runtime_plane

            return runtime_plane
        except Exception as e:  # noqa: BLE001
            logger.debug("控制面不可用，手动发送退回本机直投: %s", e)
            return None

    def manual_send_now(
        self, session_key: str, message_type: str | None = None,
    ) -> dict[str, Any]:
        """控制台自测：在**指定会话**上生成一条主动消息并走真实 `_deliver` 定向投递。

        🔴 2026-09-27（W3 缺陷 E）三条纪律，旧实现三条都反着做：

        1. **目标显式**：只投 ``session_key`` 这一路会话。旧实现取 hub「最近一个」
           引擎（``resolve_engine_for_manual_send``）再 ``_send_to_all`` 广播 ——
           A 的自测消息打给所有绑定用户。
        2. **复用真实投递**：``_deliver`` 的返回值就是 ``delivered``；静默时段在
           生成**之前**拒绝（旧实现照样烧一次 LLM，再 ``create_task`` 即报
           delivered=True，通道拒收/无归属连接也一起算成功）。
        3. **不预记账**：``sent_history`` 只在受理后写。

        配额仍归还（2026-09-18 生产实证：自测几次耗尽当日 8 条额度使自动消息
        全天停发），但保留生成时写入的 30 分钟冷却。
        """
        key = str(session_key or "").strip()
        if not key:
            return {"status": "rejected", "delivered": False, "reason": "no_target"}
        hub = self.ase
        getter = getattr(hub, "get", None)
        if not callable(getter):
            logger.error("手动发送跳过：ASE 注入非 hub（%s）", type(hub).__name__)
            return {"status": "rejected", "delivered": False, "reason": "no_engine"}
        if self._is_quiet_hours():
            logger.warning(
                "免打扰时段(%s-%s)：手动发送未生成未投递 session=%s",
                self._quiet_hours[0], self._quiet_hours[1], key,
            )
            return {
                "status": "rejected", "delivered": False, "reason": "quiet_hours",
                "session_key": key,
            }

        eng = getter(key)
        quota_before = int(getattr(eng, "_daily_message_count", 0) or 0)
        try:
            if message_type:
                from proactive.ase_engine import ProactiveType

                try:
                    chosen = ProactiveType(str(message_type))
                except ValueError:
                    return {
                        "status": "rejected", "delivered": False,
                        "reason": f"unknown_message_type:{message_type}",
                        "session_key": key,
                    }
                result = eng._generate_and_return(chosen)
            else:
                eng._update_urgency(eng._hours_since_last_chat())
                scene = eng._check_scene_triggers()
                if scene and float(getattr(eng.urgency, "total", 0.0)) >= 2.0:
                    result = eng._record_and_return(scene)
                else:
                    result = eng._generate_and_return(eng._select_type_by_urgency())
            if not result:
                logger.warning("手动发送生成失败 session=%s", key)
                return {"status": "failed", "delivered": False, "reason": "generation_failed",
                        "session_key": key}

            message = str(result.get("message") or "")
            delivered = bool(
                self._deliver(message, session_key=key,
                              character_id=self._resolve_character_id(key))
            )
            public = {k: v for k, v in result.items() if not str(k).startswith("_")}
            if not delivered:
                logger.warning("手动发送未获投递受理 session=%s", key)
                return {"status": "not_delivered", "delivered": False,
                        "reason": "deliver_rejected", "session_key": key, **public}
            # 受理之后才记账（旧实现发送前就写 sent_history）
            recorder = getattr(eng, "record_sent_entry", None)
            if callable(recorder):
                recorder(result)
            logger.info("手动发送已定向投递 session=%s [%s]", key, public.get("type"))
            return {"status": "sent", "delivered": True, "session_key": key, **public}
        finally:
            if int(getattr(eng, "_daily_message_count", quota_before)) != quota_before:
                logger.info("手动发送归还当日配额：%d -> %d",
                            getattr(eng, "_daily_message_count", 0), quota_before)
                eng._daily_message_count = quota_before

    def _drain_control_commands(self) -> None:
        """消费跨 worker 转投的控制命令（APScheduler 任务，只在 master 跑）。

        API worker 里没有调度器（``run_api._ensure_scheduler_singleton`` 把非
        master 的 components['scheduler'] 置 None），手动发送只能落持久命令，
        由 master 执行并回写**真实回执**；``CLAIM_TTL`` 过期自动重投。
        """
        rp = self._plane()
        if rp is None:
            return
        try:
            while True:
                row = rp.claim_next_control("proactive_manual")
                if not row:
                    break
                cid = int(row.get("id") or 0)
                sk = str(row.get("session_key") or "")
                try:
                    payload = json.loads(str(row.get("payload") or "{}"))
                except Exception:  # noqa: BLE001
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                result = self.manual_send_now(sk, message_type=payload.get("message_type"))
                rp.complete_control(
                    cid,
                    ok=bool(result.get("delivered")),
                    result=json.dumps(result, ensure_ascii=False),
                    reason=str(result.get("reason") or ""),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("控制命令消费异常（下轮重试）: %s", e)

    def _refresh_binding_truth(self) -> None:
        """把角色绑定缓存回灌到 SQLite 真源（块4，缺陷 D）。

        本进程是调度器 master，而「切角色 / 新绑定」的写路径落在
        处理该 HTTP 请求的那个 worker（只热更本进程缓存 + DB）。不回灌则
        master 永远按启动快照解析角色：主动消息、祝福、提醒文案都按旧角色口吻
        开口。UserManager 内部按 30s 节流，非 SQLite 部署自行跳过。
        """
        try:
            from api.deps import deps as _deps

            gf = getattr(_deps, "gf", None)
            fn = getattr(gf, "refresh_bindings_from_db", None)
            if callable(fn):
                fn()
        except Exception as e:  # noqa: BLE001
            logger.debug("绑定真源回灌跳过: %s", e)

    def _run_reminder_check(self) -> None:
        """提醒到期任务的外壳：先回灌跨 worker 真源，再执行注入的 task。

        提醒文案里的角色名按会话解析（`utils/character_resolver`），缓存陈旧
        就会用旧角色名提醒。task 本身仍由 api 装配层注入，此处只加前置步。
        """
        self._refresh_binding_truth()
        task = self._reminder_task
        if task is not None:
            task()

    def _save_config_file(self) -> None:
        self.write_config_file(
            quiet_hours=self._quiet_hours,
            vault_enabled=self._vault_enabled,
            vault_interval=self._vault_interval_min,
        )

    def reload_config(self) -> None:
        """master worker 周期性从文件重载（其他 worker 的写 ≤5 分钟内生效）。"""
        before = (self._quiet_hours, self._vault_enabled, self._vault_interval_min,
                  self._paused)
        self._load_config_file()
        after = (self._quiet_hours, self._vault_enabled, self._vault_interval_min,
                 self._paused)
        if before != after:
            self._sync_vault_job()
            logger.info(
                "调度器配置已重载: quiet=%s vault=%s paused=%s",
                self._quiet_hours, self.get_vault_config(), self._paused,
            )

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

    def _run_hot_topics_collect(self) -> None:
        """热点知识采集（同步、自限速、吞异常返回 dict，永不抛出）。

        开关/间隔真源在 config/hot_topics.yaml，本方法只负责按拍触发
        （原接线契约文档已随 2026-09-29 文档清理批删除，契约已落地为本注册）。
        """
        from shisi.knowledge.hot_topics import collect_if_due

        stats = collect_if_due()
        if stats.get("skipped"):
            logger.debug("热点采集跳过: %s", stats["skipped"])
        elif stats.get("ok"):
            logger.info(
                "热点采集完成: 新增 %s 条，池 %s 条",
                stats.get("added"), stats.get("pool_size"),
            )
        else:
            logger.warning("热点采集未成功: %s", str(stats)[:200])

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
                        # 2026-09-22 收口：拆 owner 走唯一 owner（禁手写 split）
                        left, right = session_key_mod.split_owner(key)
                        if left and left == uid and right:
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
                    # 2026-09-22 收口：拆 owner 走唯一 owner（禁手写 split）
                    left, right = session_key_mod.split_owner(key)
                    if left and left == str(owner_id) and right:
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
        # 块4（缺陷 D）：本进程是 master，绑定/换卡写在别的 worker —— 先回灌真源
        self._refresh_binding_truth()
        if not self.ase:
            logger.warning("ASE 引擎未注入（components['ase'] 为空），主动消息检查跳过")
            return

        self.reload_config()

        try:
            from proactive.ase_hub import ASEHub

            if not isinstance(self.ase, ASEHub):
                # 6b 项7：旧 `_check_ase_global` 兼容路径已删——生产唯一装配是
                # ASEHub（_init_mixin 注入）；非 hub 注入只可能是错误装配。
                logger.error(
                    "ASE 注入非 ASEHub（%s），主动消息跳过——装配缺陷，不静默走旧路径",
                    type(self.ase).__name__,
                )
                return
            self._check_ase_per_user()
        except Exception as e:  # noqa: BLE001
            logger.error("ASE check failed: %s", e)

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

    def _vital_tick_for_user(self, user_key: str, character_id: str = "") -> None:
        """D12-L：把生理读数接进既有 per-user 拍（不新建第二条调度器）。

        有事件才有状态：`VitalSignsEngine.tick` 在无状态时返回基准且**不落库**，
        于是读侧继续按「示意值，非真实生理信号」自证，而不是把没演算过的
        基准当事实写进表。异常一律吞成 debug —— 生理读数断了不能拖垮主动消息链。
        """
        try:
            from shisi.vital_signs.vital_engine import get_vital_engine, vital_state_key

            key = vital_state_key(str(user_key or ""), str(character_id or ""))
            if not key:
                return
            get_vital_engine().tick(key)
        except Exception as e:  # noqa: BLE001
            logger.debug("生理读数同拍 tick 失败 user=%s: %s", user_key, e)

    def _proactive_sent_today(self, eng: Any, now: Any) -> bool:
        """该用户引擎状态里，今天（本地日）是否已成功主动发过消息。

        读 ``eng._last_delivery_time``（UTC aware，仅 ``commit_sent`` 投递成功
        才推进；``_last_proactive_time`` 会被 on_chat 拨到用户说话时刻，不能
        兼任「今天是否已投递」判据）；无引擎/无记录 → False（宁可不触发下限，
        也不误判已发）。
        """
        if eng is None:
            return False
        try:
            last_sent = getattr(eng, "_last_delivery_time", None)
            if last_sent is None:
                return False
            from datetime import timedelta as _td
            from datetime import timezone

            beijing = timezone(_td(hours=8))
            sent_local = last_sent.astimezone(beijing).date()
            now_local = now.astimezone(beijing).date() if now.tzinfo else now.date()
            return sent_local == now_local
        except Exception:  # noqa: BLE001
            return False

    def _llm_proactive_one_user(self, hub: Any, user_key: str) -> None:
        from proactive.ase_engine import _local_now, sanitize_message
        from proactive.llm_proactive import (
            PROACTIVE_DEDUP_WINDOW,
            account_pacing_state,
            account_unanswered_streak,
            build_proactive_context,
            clamp_wait_minutes,
            decide_proactive,
            is_duplicate_proactive,
            load_proactive_knowledge,
            proactive_floor_extra,
            read_web_proactive_config,
            recent_messages_for_dedup,
            should_skip_proactive_decision,
        )
        from proactive.proactive_hooks import load_live_hooks
        from shisi.agent_plane.runtime import (
            append_proactive_event,
            get_profile_prompt_block,
            project_profile_for,
        )

        web_cfg = read_web_proactive_config()
        # D12-L：同拍演算生理读数。放在所有决策闸**之前** —— 暂停/等待窗只是
        # 「此刻不开口」，不是心跳停了；放在闸后会让她在 LLM 自判的长等待窗里
        # 读数整段冻结（tick 是唯一的回稳与噪声来源）。
        self._vital_tick_for_user(str(user_key))
        if self._paused:
            # 块4（缺陷 D）：暂停必须在**生成之前**短路。旧实现只把 paused 存在
            # ASE 引擎里（tick 级），而生产主动消息走的是这条 LLM 决策链 ——
            # 引擎里的闸从未被本链读到，表现为「控制台按了暂停，她照发」。
            append_proactive_event(
                session_key=user_key, sent=False, reason="paused",
                character_id=self._resolve_character_id(str(user_key)),
            )
            return
        if not web_cfg.get("enabled", True):
            # P1-51：关闭态账本事件每用户每日至多一条（旧实现每 tick 写一行，
            # enabled=false 反而让 agent_plane.db 涨得最快）
            today = _local_now().date().isoformat()
            if self._disabled_event_day.get(str(user_key)) != today:
                self._disabled_event_day[str(user_key)] = today
                append_proactive_event(session_key=user_key, sent=False, reason="web_disabled")
                # 块E：账本变更即落盘（否则重启当日再写一行）
                self._persist_throttle_ledger()
            return
        # P1-49：静默时段在 LLM 决策**之前**前置闸。旧实现静默只挡投递层，
        # 23:00-07:00 每 5 分钟照调一次远端 LLM（消息不发、token 恒流失）。
        if self._is_quiet_hours():
            # skip 账本治理（2026-10-12）：静默闸此前只 debug 不落账，决策
            # 链整夜不可观测；补每用户每日一条（与 llm_wait_window 同口径）。
            today_q = _local_now().date().isoformat()
            if self._quiet_event_day.get(str(user_key)) != today_q:
                self._quiet_event_day[str(user_key)] = today_q
                append_proactive_event(session_key=user_key, sent=False, reason="quiet_hours")
                self._persist_throttle_ledger()
            logger.debug("proactive LLM 前置静默闸命中 user=%s，跳过决策", user_key)
            return
        # 模型闸（2026-10-15 质量回调批）：首选供应商熔断开闸期间跳过主动消息
        # 决策——降级模型（免费档）写出来的主动消息是客服腔（生产实证），宁缺毋滥。
        if not self._primary_model_ok():
            self._record_model_degraded(str(user_key))
            return
        # token 窗口烧前闸（2026-10-17 批，生产实证驱动）：微信平台会话窗口
        # 24h TTL——窗口已死的会话，「决策→生成→投递」注定被平台 prepare failed
        # 拒收（10-16 08:11 起主通道连续 11 次白烧决策调用的实证）。决策前确定
        # 性跳过：省 LLM 调用，并把「静默死」变成每日一条可见账本事件
        # （token_window_dead）；dying（临期 4h 内）放行但落 token_window_dying
        # 告警事件 + WARNING 日志——窗口死不死不该再靠用户发现。
        # 口径纪律：**只拦确证 dead**（凭证文件在、条目在、年龄超 TTL——三者
        # 俱全才是铁证）；missing/empty/error 一律放行（文件缺席可能是新用户
        # 从未说话，也可能是探针视野外——宁宽勿误，其投递失败会经
        # deliver_failed 的 |window_rejected 分类入账，可观测性不丢）。
        if session_key_mod.is_wechat_key(str(user_key)):
            _tw: dict | None = None
            try:
                from wechat_direct.wechat_connector import token_window_state

                _owner_prefix = session_key_mod.split_owner(str(user_key))[0]
                _tw = token_window_state(_owner_prefix)
            except Exception:  # noqa: BLE001
                _tw = None
            if _tw is not None:
                _tw_state = str(_tw.get("state") or "error")
                if _tw_state == "dead":
                    # 日志跟随账本首次（每日一条）：生产 3 死窗用户 × 288 tick
                    # 曾日产 864 行同款 INFO，把真信号淹掉。降噪后同日后续
                    # tick 只剩 _emit_skip_event_daily 内部的 debug。
                    if self._emit_skip_event_daily(str(user_key), "token_window_dead"):
                        logger.info(
                            "proactive 会话窗口已死，烧前跳过 user=%s age=%sh（TTL %sh）",
                            user_key, _tw.get("age_hours"), _tw.get("ttl_hours"),
                        )
                    return
                if _tw_state == "dying" and self._emit_skip_event_daily(
                    str(user_key), "token_window_dying"
                ):
                    logger.warning(
                        "proactive 会话窗口临期 user=%s token 年龄 %sh（TTL %sh）"
                        "——用户再不发消息，主动消息将被平台拒收",
                        user_key, _tw.get("age_hours"), _tw.get("ttl_hours"),
                    )
        # 执行 LLM 上次决策的 wait_minutes（模型自判时机，非策略闸）
        import time as _time

        _now = _time.time()
        _next_ok = self._llm_proactive_next_ok.get(str(user_key))
        if _next_ok and _now < _next_ok:
            # P1-51 同款每用户每日至多一条（2026-10-12 账本治理）：等待窗内
            # 每 5 分钟 tick 写一行曾是 96% 的账本噪声（生产 4165/4348 行）。
            today_w = _local_now().date().isoformat()
            if self._wait_window_event_day.get(str(user_key)) != today_w:
                self._wait_window_event_day[str(user_key)] = today_w
                append_proactive_event(
                    session_key=user_key,
                    sent=False,
                    reason="llm_wait_window",
                    wait_minutes=int(max(0, (_next_ok - _now) // 60)),
                )
                # 块E：账本变更即落盘（否则重启当日再写一行）
                self._persist_throttle_ledger()
            return
        # 迟到容忍窗（2026-10-16，对齐爱语 ILinkService.j():710-740）：
        # LLM 自判的目标时刻 `_next_ok` 只在 [目标, 目标+窗口] 内有效——
        # 窗口内到点即触发；迟到 >窗口（机器休眠/重启错过 tick）→ 放弃本条
        # （过期不补）：记 window_expired 账本事件、出账，不投递、不烧决策调用。
        # 默认 humanize.proactive_window_enable=false = 零行为变更（保持
        # 「到点即触发」现状，仅当 pending 目标存在时才热读一次窗口配置）。
        if _next_ok:
            _win_on, _win_min = self._proactive_window_config()
            if _win_on and (_now - _next_ok) > _win_min * 60.0:
                _late_min = int((_now - _next_ok) // 60)
                # 出账使本条 pending 不再复活：下一拍走全新 LLM 决策（过期不补 ≠
                # 永久静音）。账本变更即落盘（块E 纪律：重启不重置出账状态）。
                self._llm_proactive_next_ok.pop(str(user_key), None)
                append_proactive_event(
                    session_key=user_key,
                    sent=False,
                    reason="window_expired",
                    # 事件 schema 的 wait_minutes 字段此处记录**迟到分钟数**
                    # （已过目标时刻的时长），账本自描述「放弃时晚了多久」。
                    wait_minutes=_late_min,
                    character_id=self._resolve_character_id(str(user_key)),
                )
                self._persist_throttle_ledger()
                logger.info(
                    "proactive 窗口过期放弃 user=%s 迟到 %d 分钟（窗口 %d 分钟）",
                    user_key, _late_min, _win_min,
                )
                return

        eng = hub.get(user_key) if hasattr(hub, "get") else None
        # D16（2026-10-12「谁扫码谁聊」）：节奏按**账号**聚合（`account_pacing_state`
        # 单一真源）——同 owner 前缀的多条通道取「最近一次投递」与「最近一次
        # 聊天」，消除同账号双通道各记各账、几分钟内连发（生产实证残留根因）。
        hours, last_proactive_at = account_pacing_state(hub, str(user_key), eng)
        # 节奏双闸（2026-10-12 因果连贯批，生产实证驱动）：①距上次主动投递不足
        # 60min（08:05→08:10 连发实证，模型自述「仅5分钟…还是发」）②用户最后
        # 一条消息后不足 15min（用户刚表达不满 2min 即"缓和气氛"式主动）。
        # 确定性跳过——不烧 LLM 调用；eng 缺席时不拦（闸依赖引擎状态）。
        # ③unanswered_limit（2026-10-12 未回复连击批）：连 MAX_UNANSWERED_PROACTIVE
        # 条零回应 → 停发（组内取最小，任一通道被回复即解除）。
        if eng is not None:
            skip, gate_reason = should_skip_proactive_decision(
                hours_since_last_chat=hours,
                last_proactive_at=last_proactive_at,
                unanswered_streak=account_unanswered_streak(hub, str(user_key), eng),
            )
            if skip:
                # 账本降噪（2026-10-17 批）：确定性闸的命中事件从「每 tick 一条」
                # 收敛为「每 user|reason 每日一条」——生产实证 unanswered_limit
                # 曾每 5 分钟写一行刷屏（信号被噪声淹没）。闸本身行为不变。
                self._emit_skip_event_daily(
                    str(user_key),
                    gate_reason,
                    character_id=self._resolve_character_id(str(user_key)),
                )
                return
        urgency = None
        if eng is not None:
            try:
                eng.tick(hours, dry_run=True)
                urgency = float(getattr(getattr(eng, "urgency", None), "total", 0.0) or 0.0)
            except Exception:  # noqa: BLE001
                urgency = None
        # 块D：决策层的接地事实（用户最后一句 / 注意力热度）。
        # 取不到即留空/None，提示词相应段落整段不出现（宁缺毋串，不注入假值）。
        last_user_message = ""
        response_rate: float | None = None
        if eng is not None:
            with contextlib.suppress(Exception):
                last_user_message = str(getattr(eng, "_last_user_message", "") or "")
            with contextlib.suppress(Exception):
                _rr = getattr(eng, "response_rate", None)
                response_rate = float(_rr) if _rr is not None else None
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
        # 身份来自当前会话绑定，不把缓存引擎首次创建时的人设当成当前角色。
        # 2026-10-15 统一真源：完整版人设（含【回复示例】【通用禁令】），
        # 与生成线/祝福润色同走 proactive_context 单一 owner。
        char_id = self._resolve_character_id(str(user_key))
        from proactive.proactive_context import load_full_persona_hint
        # 2026-10-21 R7：人设注入侧的括号剥离开关也跟随该会话模式
        persona = load_full_persona_hint(char_id, str(user_key or "") or None) if char_id else ""
        # 缺陷 H：真实可分享内容（角色知识库 + 热点池）经统一读接口进**决策链**。
        # 角色 id 用当轮解析值，不用引擎创建时缓存的 `_knowledge_character_id`
        # （切角色后那是旧角色 → 把别人的角色内容当谈资）。
        knowledge = load_proactive_knowledge(char_id) if char_id else ""
        recent = []
        memory = self._resolve_memory()
        getter = getattr(memory, "get_chat_context", None)
        if getter is not None:
            try:
                recent, _ = getter(session_id=user_key, keep_recent=10, character_id=char_id, summarize=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("主动上下文读取失败 user=%s: %s", user_key, exc)
        if recent:
            last_user_message = next(
                (str(row.get("content") or "") for row in reversed(recent) if row.get("role") == "user"), "",
            )
        elif str(getattr(eng, "_knowledge_character_id", "") or "") != char_id:
            last_user_message = ""
        # 2026-10-21 FIX-5：决策 prompt 的【当前角色最近对话原文】此前直接吃**原始库行**
        # ——既没过 sanitize_llm_history（连续同角色合并 / 系统占位过滤），也没过回灌
        # 净化（沉浸式括号动作 / 元记忆解释 / 归属翻转）。于是上一轮写坏、已落库的
        # assistant 行被当「我自己说过的话」few-shot 进决策，再由决策器写出主动消息
        # ——与主链 R4 要切断的自我强化回路同构，只是发生在主动线。
        # 与主链（orchestrator._prepare_context）共用同一真源函数，防规则分叉。
        # ⚠️ 只净化**副本**：last_user_message 与 build_proactive_context 内的否决
        # 检测/window 语义仍按原文 recent 计算（见上方取值处），不改 recent 本体。
        scrubbed_recent: list[Any] = recent
        if recent:
            try:
                # ⚠️ 用模块别名而非 `from ... import read_reply_mode`：本函数另有
                # 模块级导入的 read_reply_mode（出站守卫 reply_mode 实参，见 :1722），
                # 局部 `from X import read_reply_mode` 会让它在整个函数作用域变成
                # 局部名 → recent 为空时 :1722 抛 UnboundLocalError（回归实证）。
                import utils.reply_mode as _reply_mode
                from utils.prompt_sanitize import (
                    sanitize_llm_history,
                    scrub_history_rows,
                )

                # 2026-10-21 R7：按**该会话**的模式净化（per-user 覆盖优先）。
                # user_key 在此函数内已有（即会话键），空时回落全局默认。
                scrubbed_recent = scrub_history_rows(
                    sanitize_llm_history(recent),
                    _reply_mode.read_reply_mode(str(user_key or "") or None)
                    == _reply_mode.REPLY_MODE_IMMERSIVE,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("主动决策历史净化失败（回退原文）: %s", exc)
                scrubbed_recent = recent
        # 活材料钩子（2026-10-16 三连同义批 B）：爱语逆向对照——话题感来自活
        # 材料注入（未答澄清/将到提醒/未闭环承诺/近日热点），非模型自觉。
        hooks = load_live_hooks(str(user_key), memory)
        now = _local_now()
        quiet = None
        try:
            quiet = self.get_quiet_hours() if hasattr(self, "get_quiet_hours") else None
        except Exception:  # noqa: BLE001
            quiet = None
        # 关怀下限（2026-10-11 批，arXiv 2510.23853 依据）：失联超 48h 且今日
        # 零主动 → 给决策器一句显式提示，破"低热度→永远别打扰"死循环
        # （2026-10-08 起账本实证：连续 4 天 15-25 次决策全部 should=False）。
        sent_today = self._proactive_sent_today(eng, now)
        floor_extra = proactive_floor_extra(
            hours_since_last_chat=float(hours or 0.0), sent_today=sent_today,
        )
        ctx = build_proactive_context(
            session_key=str(user_key),
            hours_since_last_chat=float(hours or 0.0),
            local_time=now.strftime("%Y-%m-%d %H:%M %A"),
            profile=profile,
            relationship_hint=rel,
            urgency_signal=urgency,
            persona_hint=persona,
            knowledge_hint=knowledge,
            recent_messages=scrubbed_recent,
            web_config=web_cfg,
            quiet_hours=quiet,
            # 🔴 2026-09-22 二次根治（块D 接地贯通）：补用户最后一句 + 注意力热度。
            # 生成层早已接上这两项，**决策层被漏掉** —— 决策层更早决定"是否开口"，
            # 缺了它模型只能按时间泛泛开口，接不住用户上一轮提到的具体事。
            last_user_message=last_user_message,
            response_rate=response_rate,
            extra=floor_extra,
            # 2026-10-15 称呼根治批：角色 id 传入（称呼白名单段按角色名派生
            # 自动禁用——「夏夏」= 林挽夏叠字，无需用户发现即兜住）。
            character_id=char_id,
            # 2026-10-16 三连同义批 B：活材料钩子节（有活材料才有话题意识）。
            hooks_text=hooks.section,
        )
        llm = self._session_llm_resolver(user_key) if self._session_llm_resolver else self._resolve_proactive_llm(eng)
        if llm is None:
            logger.info("proactive LLM unavailable user=%s skip", user_key)
            append_proactive_event(session_key=user_key, sent=False, reason="llm_unavailable", character_id=char_id)
            return
        # A（同批）：禁 in-call 降级——allow_fallback=False，首选失败抛
        # ProviderError → 记 model_degraded 跳过本轮（宁可不发也不让降级模型
        # 代写）。D（同批）：timeout=25s 放宽决策线超时，让强模型写完慢尾。
        # hook_keywords：多候选竞争择优（B）。三参均按 decide_proactive 签名
        # 过滤后传入——被 monkeypatch 的 2 参替身不受影响。
        from llm_provider.llm_gateway import ProviderError
        from proactive.llm_proactive import PROACTIVE_DECISION_TIMEOUT

        _decide_kwargs: dict[str, Any] = {
            "strict": True,
            "allow_fallback": False,
            "timeout": PROACTIVE_DECISION_TIMEOUT,
            "hook_keywords": hooks.keywords,
        }
        try:
            import inspect as _inspect

            _sig = _inspect.signature(decide_proactive)
            _decide_kwargs = {k: v for k, v in _decide_kwargs.items() if k in _sig.parameters}
        except (TypeError, ValueError):
            _decide_kwargs = {}
        try:
            decision = decide_proactive(llm, ctx, **_decide_kwargs)
        except ProviderError as e:
            logger.info("proactive 首选模型失败（禁降级跳过）user=%s err=%s", user_key, e)
            self._record_model_degraded(str(user_key), char_id)
            return
        if char_id != self._resolve_character_id(str(user_key)):
            append_proactive_event(session_key=user_key, sent=False, reason="character_changed", character_id=char_id)
            return
        logger.info(
            "proactive LLM decision user=%s should=%s wait=%s reason=%s",
            user_key,
            decision.get("should_contact"),
            decision.get("wait_minutes"),
            (decision.get("reason") or "")[:80],
        )
        wait_m = clamp_wait_minutes(decision.get("wait_minutes"))
        if isinstance(wait_m, int) and wait_m > 0:
            # T-4（2026-10-27 仿真人机制）：模型给出的等待窗加随机抖动，
            # 避免「精确到分钟」的机器节奏；jitter_seconds=0 时行为不变。
            _jitter = self.get_proactive_jitter_seconds()
            self._llm_proactive_next_ok[str(user_key)] = apply_wait_jitter(
                _now, wait_m, _jitter,
            )
            if _jitter > 0:
                logger.debug(
                    "proactive wait jitter user=%s max=%.0fs", user_key, _jitter,
                )
            # 块4（缺陷 J）：模型自判的等待窗必须落盘 —— 旧实现只有投递失败的
            # 退避侧落盘（块E），成功/skip 侧留内存，重启即归零 → 她会在模型
            # 明确说「X 分钟后再说」的窗口里提前开口。
            self._persist_throttle_ledger()
        if not decision.get("should_contact"):
            _false_reason = str(decision.get("reason") or "")
            # 契约拒收退避（2026-10-17 对抗验收发现→当场修复）：空 message /
            # 零信息问候被输出契约翻转后，若模型未自填有效 wait，此处不设窗
            # 会导致下一 tick 零约束重试、同上下文必然重犯 → 每 5 分钟白烧
            # 一次决策 LLM。与 duplicate_suppressed 同款退避语义（真源=
            # llm_proactive.is_contract_rejection / CONTRACT_REJECT_BACKOFF_MINUTES）。
            from proactive.llm_proactive import (
                CONTRACT_REJECT_BACKOFF_MINUTES,
                is_contract_rejection,
            )

            if is_contract_rejection(_false_reason) and not (
                isinstance(wait_m, int) and wait_m > 0
            ):
                self._llm_proactive_next_ok[str(user_key)] = (
                    _now + float(CONTRACT_REJECT_BACKOFF_MINUTES) * 60.0
                )
                self._persist_throttle_ledger()
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason=_false_reason or "llm_false",
                wait_minutes=decision.get("wait_minutes"),
                character_id=char_id,
            )
            return
        message = sanitize_message(str(decision.get("message") or ""))
        if not message:
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="empty_message_after_sanitize",
                wait_minutes=decision.get("wait_minutes"),
                character_id=char_id,
            )
            return
        # 终审守卫（2026-10-14 对抗验收执行位矩阵抓到的缺口）：主动链投递前
        # 此前只有 sanitize_message（剥时间前缀），错误哨兵/表情复读/退化回复
        # 会绕过终审直达用户。与主链共用同一真源 terminal_reply_guard。
        # 主动链无当轮用户消息（独白场景），user_msg 传空串——表情复读剥除
        # 在无用户消息时不触发（is_pure_emoji_message 空串=False），只拦哨兵/退化。
        # 2026-10-21 FIX-1：补 reply_mode —— 缺省空串使沉浸式括号动作不剥。
        from utils.prompt_sanitize import terminal_reply_guard

        # 2026-10-21 R7：守卫的 reply_mode 跟随该会话（per-user 覆盖）
        message = terminal_reply_guard(
            message, user_msg="", character_name="",
            reply_mode=read_reply_mode(str(user_key or "") or None),
        )
        if not message:
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="terminal_guard_rejected",
                wait_minutes=decision.get("wait_minutes"),
                character_id=char_id,
            )
            return
        # 出站去重硬闸（scallopbot 同族：投递前确定性判重）。生产实证同一用户
        # 连收三条「哼，你终于想起我了？」变体 —— ase_engine 生成线的
        # avoid_block 管不到决策线。比对集取**同 owner 组**合并（2026-10-12：
        # 单引擎取样面下，兄弟通道刚发过的同义换皮对内容闸不可见）；两线投递
        # 成功都经 commit_sent 汇合于 eng._recent_messages，跨重启亦恢复。
        # 🔴 window 必须覆盖整个合并集：固定 window 会把前半段（本引擎消息）
        # 整段挤出，恰在目标场景退化为单引擎现状（TestSqueezeTrap 锁死）。
        eng_recent: list[str] = []
        if eng is not None:
            with contextlib.suppress(Exception):
                eng_recent = recent_messages_for_dedup(hub, str(user_key), eng)
        if is_duplicate_proactive(
            message, eng_recent,
            window=max(PROACTIVE_DEDUP_WINDOW, len(eng_recent)),
        ):
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason="duplicate_suppressed",
                message=message[:80],
                character_id=char_id,
            )
            # 拦截即退避 5 分钟：同义变体不会因为立刻重试而变得不同
            self._llm_proactive_next_ok[str(user_key)] = _time.time() + 5 * 60.0
            # 等待窗随写随落盘（与块4/块E 纪律一致，重启不重置）
            self._persist_throttle_ledger()
            logger.info("proactive LLM duplicate_suppressed user=%s msg=%s", user_key, message[:40])
            return
        # 2026-10-27 Q3：把本轮决策用的最近对话摘要留给 post-draft judge
        # （投递时弹出；无摘要时 judge 只凭正文 + 时间判定）。
        try:
            _recent_texts = [
                str(row.get("content") or "") for row in (scrubbed_recent or [])
                if isinstance(row, dict) and row.get("content")
            ][-5:]
            if not isinstance(getattr(self, "_judge_recent", None), dict):
                self._judge_recent = {}
            self._judge_recent[str(user_key)] = _recent_texts
        except Exception:  # noqa: BLE001
            pass
        if self._deliver(message, session_key=user_key, character_id=char_id):
            if eng is not None and hasattr(eng, "commit_sent"):
                # 注意：模块顶部已导入 contextlib。此处**禁止**再写函数内局部导入
                # —— 局部导入会让该名在整个函数作用域被视为局部，任何在它
                # 之前的引用都会 UnboundLocalError（2026-09-22 块D 引入上方
                # contextlib.suppress 到接地解析时即刻踩中）。
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
                character_id=char_id,
            )
            logger.info("proactive LLM delivered user=%s %s", user_key, message[:40])
            if self._deliver_fail_counts.pop(str(user_key), None) is not None:
                # 块E：成功送达清零退避（仅当确实有计数时落盘）
                self._persist_throttle_ledger()
        else:
            # P1-22：投递失败指数退避（5m→15m→45m→2h 封顶），成功送达即清零。
            # 旧实现失败侧无任何 backoff：不可达用户每 5 分钟「生成→失败→再生成」，
            # 一天 288 次 LLM 零投递（v1.13 把记账移到投递后是对的，但没补退避）。
            fails = self._deliver_fail_counts.get(str(user_key), 0) + 1
            self._deliver_fail_counts[str(user_key)] = fails
            backoff_min = min(5 * (3 ** (fails - 1)), 120)
            self._llm_proactive_next_ok[str(user_key)] = _time.time() + backoff_min * 60.0
            # 块E：退避阶梯与下一次可试时刻一并落盘 —— 重启不重置阶梯，
            # 否则 5m 永远重新开始、永远升不到 120m 封顶。
            self._persist_throttle_ledger()
            logger.info(
                "proactive 投递失败 user=%s 连续第%d次 → 退避 %d 分钟",
                user_key, fails, backoff_min,
            )
            # 失败分类入账（2026-10-17 批）：窗口拒收/通道掉线/其他异常是三
            # 种病——生产实证连续 11 次失败全是「平台窗口拒收」，账本此前
            # 只写 backoff 看不出病型（_last_deliver_error 由 _send_targeted
            # 失败分支写入；分类真源=_classify_deliver_error）。
            _err_class = _classify_deliver_error(
                str(getattr(self, "_last_deliver_error", "") or "")
            )
            append_proactive_event(
                session_key=user_key,
                sent=False,
                reason=f"deliver_failed_backoff_{backoff_min}m{_err_class}",
                message=message[:80],
                wait_minutes=decision.get("wait_minutes"),
                character_id=char_id,
            )

    def set_delivery_loop(self, getter: Callable[[], Any]) -> None:
        """P1-23：装配层注入「通道资产所属事件循环」的 getter（如 ws 线程 loop）。

        调度任务跑在 APScheduler worker 线程；WebSocketServer 的连接与
        asyncio.Lock 属另一条循环。旧实现直接 asyncio.run 在新循环里 await
        这些资产 = 跨循环未定义行为：轻则误判未送达，重则 future 永不
        resolve、占死 executor worker（ase_check 无 max_instances）。
        """
        self._delivery_loop_getter = getter

    def _run_blocking(self, coro_factory: Callable[[], Any]) -> Any:
        """桥到通道所属循环；无注入时用共享循环。等待确认完成后才允许记账。"""
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
                return fut.result()
            raise RuntimeError("投递循环与当前运行循环相同，阻塞等待必死锁")
        from utils.async_utils import run_on_shared_loop

        return run_on_shared_loop(coro_factory())

    def _deliver(
        self, message: str, session_key: str | None = None, character_id: str | None = None,
    ) -> bool:
        """投递主动消息。session_key 非空时**定向**到该会话，否则广播（旧路径）。

        D13 同意门禁：定向会话的归属账号未同意/已撤回/旧版本时停发
        （返回 False，调用方按 skip 处理，不扣配额、不写送达）。

        🔴 2026-09-22 二次根治：异常分支曾回落 `self._send(message)`（生产 =
        `logger.info` 包装的纯日志通道，见 `_LOG_ONLY_CHANNELS` 的告诫），
        随后 `_record_outbound` + `return True` —— **从未发出的消息被记成
        「已送达」**：扣配额、写冷却、置位场景标记、并把这句写进对话历史
        （下轮 prompt 里出现她"说过"但从没说过的话 = 自问自答）。
        现异常一律返回 False（`_send_targeted` 自身已用返回值表达失败，
        `_run_blocking` 的异常属基础设施故障，同样不能算送达），
        由调用方走失败退避；记账只由「投递成功」触发。

        2026-10-14 对抗验收执行位矩阵：三条 LLM 生成→投递路径（决策线 :1381 已
        在调用方接守卫、手动发送线、重要日期祝福线）统一由本入口兜底终审——
        决策线在上游已守卫（去重闸需要守卫后内容），手动/祝福线在此被覆盖。
        """
        from utils.prompt_sanitize import terminal_reply_guard

        # 2026-10-15 称呼根治批：守卫扩面第三类（非法称呼呼语剥除）需会话上下文
        # ——decision/手动/祝福三条主动线全经本入口，此处统一传参即全覆盖
        # （session_key/character_id 均可能为 None，按守卫契约归一为空串）。
        # 2026-10-21 FIX-1：补 reply_mode —— 本入口是手动/祝福线的唯一兜底，
        # 不传则沉浸式括号动作（含上游已剥一次的决策线之外的路径）原样出站。
        # 2026-10-21 R7：模式跟随该会话（per-user 覆盖）；session_key 为空
        # （广播/手动线）→ 回落全局默认，与旧行为一致。
        guarded = terminal_reply_guard(
            message,
            user_msg="",
            character_name="",
            session_key=str(session_key or ""),
            character_id=str(character_id or ""),
            reply_mode=read_reply_mode(str(session_key or "") or None),
        )
        if not guarded:
            logger.warning("[主动消息] 终审守卫拦截（哨兵/退化回复），不投递")
            return False
        message = guarded
        # 2026-10-27 Q3：post-draft judge（只判发/不发，fail-closed）。
        # 决策线在投递前已把最近 5 条对话摘要放入 `_judge_recent`；手动/祝福线
        # 没有决策上下文 → 只凭正文 + 时间判定。judge 不可用一律不投递。
        recent_for_judge = []
        try:
            _stash = getattr(self, "_judge_recent", None)
            if isinstance(_stash, dict):
                recent_for_judge = _stash.pop(str(session_key or ""), []) or []
        except Exception:  # noqa: BLE001
            recent_for_judge = []
        try:
            from proactive.post_draft_judge import judge_proactive_message

            _quiet = None
            try:
                _quiet = self.get_quiet_hours() if hasattr(self, "get_quiet_hours") else None
            except Exception:  # noqa: BLE001
                _quiet = None
            verdict = judge_proactive_message(
                message,
                llm=getattr(self, "_llm_provider", None),
                recent=recent_for_judge,
                quiet_hours=_quiet,
                session_key=str(session_key or ""),
            )
            if not verdict.allow:
                logger.info(
                    "[主动消息] post-draft judge 拒绝投递 session=%s reason=%s",
                    session_key, verdict.reason,
                )
                return False
        except Exception as e:  # noqa: BLE001
            logger.warning("[主动消息] judge 异常（fail-closed 不投递）: %s", e)
            return False
        if session_key:
            from api.consent import outbound_allowed_for_session_sync

            if not outbound_allowed_for_session_sync(str(session_key)):
                logger.info("[主动消息] 账号未同意协议，停发 session=%s", session_key)
                return False
        if session_key:
            current_character = self._resolve_character_id(str(session_key))
            if character_id is not None and character_id != current_character:
                logger.info("主动草稿角色已切换，取消投递 session=%s", session_key)
                return False
            character_id = current_character
        try:
            delivered = bool(
                self._run_blocking(lambda: self._send_targeted(message, session_key))
            )
        except Exception as e:  # noqa: BLE001
            logger.error("主动消息投递失败（不记账、不扣配额）: %s", e)
            return False
        if delivered:
            # API 受理后只记录发起发送时的身份，不把已发送文字改归新角色。
            self._record_outbound(message, session_key, character_id=character_id)
        return delivered

    @staticmethod
    def _is_wechat_session_key(session_key: str) -> bool:
        """是否微信会话键（判据唯一真源：`utils.session_key`）。

        旧实现在此手写「含 `@im.wechat` **或** 二段式（左段数字）」两套规则，
        与 `reminder_delivery`（只认 `@im.wechat`）、`run_api`（只认二段）
        三处口径互不一致 —— 收敛到单一 owner。生产实测键为
        `N:wxid@im.wechat`（见 utils.session_key docstring 取样）。
        """
        return session_key_mod.is_wechat_key(session_key)

    async def _send_targeted(self, message: str, session_key: str | None = None) -> bool:
        # 投递失败分类（2026-10-17 批）：每次尝试先清零，失败分支各自落短因，
        # 供调用方账本分类（deliver_failed_backoff_XXm|<class>）——旧实现失败
        # 只有 warning 日志，账本看不出「窗口拒收」与「通道掉线」是两种病。
        self._last_deliver_error = ""
        if self._is_quiet_hours():
            self._last_deliver_error = "quiet_hours"
            logger.warning(
                "免打扰时段(%s-%s)：消息未投递（无紧急旁路），内容不计数不扣配额",
                self._quiet_hours[0], self._quiet_hours[1],
            )
            return False

        if session_key:
            if not self._is_wechat_session_key(session_key):
                # 非微信（web / WS）会话键：**定向**到 websocket 通道。
                # 2026-09-22：旧实现落到 `_send_to_all` 全连接广播——A 的主动
                # 消息/提醒推给所有打开控制台的人（跨用户可见），且「广播成功
                # 即送达」把无归属连接也算进去。现与微信分支同纪律：通道缺失、
                # 旧签名不收 session_key、或 0 送达 → 判失败，绝不降级广播。
                sender = self._channel_instances.get("websocket")
                if sender is not None:
                    try:
                        if asyncio.iscoroutinefunction(sender):
                            await sender(message, session_key=session_key)
                        else:
                            sender(message, session_key=session_key)
                        logger.info("主动消息已定向投递: websocket session=%s", session_key)
                        return True
                    except TypeError as e:
                        logger.warning(
                            "websocket 通道不接受 session_key，定向消息拒绝降级为广播: session=%s %s",
                            session_key, e,
                        )
                        return False
                    except Exception as e:  # noqa: BLE001
                        logger.warning("web 定向投递失败 session=%s: %s", session_key, e)
                        self._channel_instances["websocket"] = None
                logger.warning(
                    "web 会话定向投递未成功（通道不可用），不回退广播: session=%s", session_key
                )
                return False

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
                    self._last_deliver_error = f"session_key_unsupported: {e}"
                    logger.warning(
                        "wechat 通道不接受 session_key，定向消息拒绝降级为广播: session=%s %s",
                        session_key, e,
                    )
                    return False
                except Exception as e:  # noqa: BLE001
                    self._last_deliver_error = str(e)[:200]
                    logger.warning("定向投递失败 session=%s: %s", session_key, e)
                    self._channel_instances["wechat"] = None
            # P1-21：微信会话键在 wechat 通道缺失/失败时直接返回 False。
            # 旧实现落到 _send_to_all —— instance=None 时整段跳过（微信形态
            # 零送达却继续白烧 LLM），有实例时变跨用户广播 + 记「定向成功」。
            if not self._last_deliver_error:
                self._last_deliver_error = "channel_unavailable"
            logger.warning("微信定向投递未成功（通道不可用），不回退广播: session=%s", session_key)
            return False

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
            result = run_curator_all_known(sm, llm=llm, llm_resolver=self._session_llm_resolver)
            logger.info("memory curator done: %s", result.get("sessions"))
        except Exception as e:  # noqa: BLE001
            logger.warning("memory curator failed: %s", e)
        # P1-51：账本保留（30 天）——旧实现 event_ledger 只 append 不 prune，
        # chat/tool/profile/web_disabled 全类型常驻 → agent_plane.db 无界增长。
        # 2026-09-22：画像事件（profile_update/correct）是画像唯一写权威，
        # 永久豁免——否则 30 天不活跃用户的画像事件被清，缓存丢失即画像清零。
        try:
            from shisi.agent_plane.event_ledger import (
                EVENT_PROFILE_CORRECT,
                EVENT_PROFILE_UPDATE,
                default_ledger,
            )

            removed = default_ledger().prune(
                retention_days=30,
                preserve_types=(EVENT_PROFILE_UPDATE, EVENT_PROFILE_CORRECT),
            )
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

        # 情感时间衰减 — 审计 item45：打向**每用户存活引擎**（UserManager 真态）
        # + orchestrator 请求级引擎缓存（web 路径主引擎，UserManager 不覆盖）。
        # 2026-09-22：衰减时长改用独立基准 `_hours_since_last_emotion_decay`
        # （旧实现误用距上次 ASE tick 的 ≈5 分钟，衰减实际从未发生）；
        # 同日二次修复：基准落盘 + 缺失回填，见 `_load_last_emotion_decay`。
        try:
            hours = self._hours_since_last_emotion_decay()
            if hours > 0:
                from api.deps import deps as _deps
                user_mgr = getattr(_deps, "gf", None)
                decayed = 0
                if user_mgr is not None and hasattr(user_mgr, "apply_time_decay_all"):
                    decayed += user_mgr.apply_time_decay_all(hours)
                # 🔴 请求级引擎（web 对话路径）此前完全不衰减 —— 只打
                # UserManager 引擎时，web 用户情绪永不冷却（审计 item45 半修）。
                try:
                    orch = getattr(_deps, "orch", None)
                    if orch is not None and hasattr(orch, "apply_request_emotion_decay"):
                        decayed += int(orch.apply_request_emotion_decay(hours) or 0)
                except Exception:  # noqa: BLE001
                    logger.debug("请求级情绪衰减失败（忽略）", exc_info=True)
                # 衰减线性于 hours，成功后推进基准；失败不推进（下次把
                # 未衰减的时长补上，总量守恒）。基准落盘，重启后不丢。
                new_base = datetime.now(tz=timezone.utc)
                self._last_emotion_decay = new_base
                self._persist_last_emotion_decay(new_base)
                logger.info(
                    "情感时间衰减已应用: %.2f 小时，覆盖 %d 个引擎", hours, decayed
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
        """重要日期祝福（生日/纪念日）：LLM 生成、模板兜底、**按会话定向**投递。

        ⚠️ 2026-09-19 修复：原只由 00:05 每日维护调用，恒落在免打扰时段内被
        `_send_to_all()` 静默丢弃——生日/纪念日祝福从未送达过。现由独立的
        每小时任务在静默结束后第一时间补发，当日只发一次。

        ⚠️ 2026-09-22 多用户根治（此前是单用户时代遗存）：
        - 旧实现只查角色级全局日期配置，`_deliver(message)` 不带 session_key
          → 广播给**所有** owner 的全部绑定 peer；dedup 键无用户维度——
          A 的生日发过，同日生日的 B 就被吞掉。
        - 现按用户两路检查：① **用户画像生日**（EventLedger 投影，公历可解析
          形态；农历表述宁缺毋错不发）；② 角色级全局日期（保留既有配置语义），
          但投递逐会话定向、dedup 键带 user_key。
        """
        # 块4（缺陷 D）：祝福口吻的角色名同样要按当前真源解析
        self._refresh_binding_truth()
        if self._is_quiet_hours():
            logger.info(
                "[重要日期] 处于免打扰时段(%02d-%02d)，等待静默结束后补发",
                self._quiet_hours[0], self._quiet_hours[1],
            )
            return
        try:
            from utils.important_dates import check_today, parse_birthday_hint

            now_local_ts = _local_now()
            today_key = f"{now_local_ts:%Y-%m-%d}"
            mmdd = now_local_ts.strftime("%m-%d")
            user_keys = self._collect_ase_user_keys()

            # 全局角色级日期（既有配置语义，逐用户定向化）
            active_id = ""
            try:
                from api.deps import deps as _deps

                cm = getattr(getattr(_deps, "shisi_reg", None), "character_manager", None)
                active_id = (cm.get_active_id() if cm else "") or ""
            except Exception:  # noqa: BLE001
                active_id = ""
            global_hits = check_today(active_id, now_local_ts) if active_id else []

            if not user_keys:
                return
            for user_key in user_keys:
                # ① 用户画像生日（公历 + 农历双通道，2026-09-22 裁决引入农历：
                #    复用既有依赖 lunarcalendar；闰月生日只在真闰月命中）
                birthday_hit = False
                try:
                    from shisi.agent_plane.runtime import project_profile_for
                    from utils.important_dates import (
                        lunar_birthday_matches,
                        parse_birthday_hint,
                    )

                    hint = str((project_profile_for(user_key) or {}).get("birthday") or "")
                    birthday_hit = bool(
                        parse_birthday_hint(hint) == mmdd
                        or lunar_birthday_matches(hint, now_local_ts)
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("[重要日期] 画像生日读取失败 user=%s: %s", user_key, e)
                if birthday_hit:
                    self._send_date_wish(
                        user_key, today_key, label="你的生日",
                        kind="birthday", names="你的生日",
                    )
                # ② 全局角色级日期（同人当日只发一条：生日与全局命中并列时合并）
                if global_hits and not birthday_hit:
                    names = "、".join(h.get("name", "") for h in global_hits)
                    kinds = "/".join(sorted({h.get("kind", "custom") for h in global_hits}))
                    self._send_date_wish(
                        user_key, today_key, label=names,
                        kind="birthday" if "birthday" in kinds else "anniversary",
                        names=names,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("重要日期检查失败: %s", e)

    def _send_date_wish(self, user_key: str, today_key: str, *,
                        label: str, kind: str, names: str) -> None:
        """向单个会话定向发送一条祝福（当日幂等 + LLM 口吻润色 + 模板兜底）。"""
        dedup_key = f"{today_key}|{user_key}|{label}"
        if dedup_key in self._important_dates_sent:
            return
        char_id = self._resolve_character_id(str(user_key))
        wish = "生日快乐" if kind == "birthday" else "纪念日快乐"
        message = f"今天是个特别的日子（{names}）。{wish}呀！"
        # 生成、发送与记账共用角色快照；不另建一套角色解析规则。
        # 2026-10-15 统一真源：祝福润色与决策线同走完整版人设（旧裁剪版缺
        # 【回复示例】【通用禁令】，祝福口吻曾与角色卡脱钩）。
        try:
            from proactive.proactive_context import load_full_persona_hint

            persona_hint = load_full_persona_hint(char_id) or ""
            llm = self._session_llm_resolver(user_key) if self._session_llm_resolver else self._resolve_proactive_llm()
            if llm is not None and hasattr(llm, "chat_sync"):
                polished = llm.chat_sync(
                    query=(
                        f"以角色口吻给对方发一条{'生日' if kind == 'birthday' else '纪念日'}祝福，"
                        f"提到「{names}」，2-3 句话，真挚不说教："
                    ),
                    system_prompt=persona_hint or "",
                    max_tokens=150, temperature=0.8,
                )
                cleaned = sanitize_message(str(polished or ""))
                if cleaned:
                    message = cleaned
        except Exception as e:  # noqa: BLE001
            logger.debug("[重要日期] LLM 润色失败，用模板: %s", e)

        if char_id != self._resolve_character_id(str(user_key)):
            logger.info("[重要日期] 生成期间角色切换，取消旧草稿 user=%s", user_key)
            return
        logger.info("[重要日期] 命中 %s（user=%s），定向发送祝福", names, user_key)
        if self._deliver(message, session_key=user_key, character_id=char_id):
            self._important_dates_sent.add(dedup_key)
            # 块E：当日幂等记录落盘（否则重启当日重复发同一祝福）
            self._persist_throttle_ledger()
            logger.info("[重要日期] 祝福已送达: %s user=%s", names, user_key)
        else:
            logger.warning(
                "[重要日期] 祝福未送达，将在下一个非静默小时重试: %s user=%s",
                names, user_key,
            )

    def _reset_daily(self) -> None:
        """每日重置"""
        if self.ase and hasattr(self.ase, "reset_daily_count"):
            self.ase.reset_daily_count()
            n = 1
            if hasattr(self.ase, "iter_engines"):
                n = sum(1 for _ in self.ase.iter_engines())
            logger.info("Daily ASE count reset (engines=%d)", n)
        self._save_state()

    def _save_state(self) -> None:
        """持久化ASE引擎状态"""
        if self.ase and hasattr(self.ase, "save_state"):
            try:
                self.ase.save_state()
            except Exception as e:  # noqa: BLE001
                logger.warning("State save failed: %s", e)

    # ── 工具方法 ─────────────────────────────────────────

    def _hours_since_last_emotion_decay(self) -> float:
        """距上次**成功执行**情绪时间衰减的小时数（衰减专用基准）。

        2026-09-22：取代误用作衰减时长的 `_hours_since_last_check`（后者是
        「距上次 ASE tick」，≤5 分钟，导致每日衰减形同关闭）。

        🔴 同日二次修复：首版以 `None` 作哨兵并在 None 时返回 `0.0`，使
        `if hours > 0` 永假 + 赋值语句位于该 if 内 = **永久自我锁死**。
        现基准**必定非 None**（构造时即由 `_load_last_emotion_decay()` 回填），
        故此处只需做差；异常回填零时长亦不会退化为锁死（下次仍有真实差值）。
        """
        if self._last_emotion_decay is None:  # 理论不可达，防御性兜底
            self._last_emotion_decay = self._default_last_emotion_decay()
            return 0.0
        now_utc = datetime.now(tz=timezone.utc)
        base = self._last_emotion_decay
        if base.tzinfo is None:  # 兼容历史 naive 串
            base = base.replace(tzinfo=timezone.utc)
        delta = now_utc - base
        return max(0.0, delta.total_seconds() / 3600)

    @staticmethod
    def _default_last_emotion_decay() -> datetime:
        """无历史基准时的回填值 —— 「昨日维护时刻」（UTC）。

        为什么不回填 `now`：回填 now 会让本次维护 hours=0 → 衰减不执行 →
        （若能再次回填）仍是死循环的另一种形态。回填「昨日维护时刻」使本次
        维护得到 ~24h 的真实时长，衰减立刻生效；且 00:05 每日只跑一次，
        不会重复计。
        """
        now_utc = datetime.now(tz=timezone.utc)
        yesterday = now_utc - timedelta(days=1)
        # 锚定到维护时刻（00:05 本地 = 16:05 UTC 前一日）
        return yesterday.replace(hour=now_utc.hour, minute=now_utc.minute,
                                 second=0, microsecond=0)

    def _load_last_emotion_decay(self) -> datetime:
        """从 `data/scheduler_config.json` 读取衰减基准；缺失/损坏即回填并落盘。"""
        raw = ""
        try:
            data = json_state.read_json(self._CONFIG_PATH, default={}) or {}
            raw = str(data.get("last_emotion_decay") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("读取情绪衰减基准失败，按默认回填: %s", e)
        if raw:
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except ValueError as e:
                logger.warning("情绪衰减基准格式非法（%s），按默认回填: %s", raw, e)
        fallback = self._default_last_emotion_decay()
        self._persist_last_emotion_decay(fallback)
        return fallback

    def _persist_last_emotion_decay(self, value: datetime) -> None:
        """把衰减基准原子落盘（跨进程/重启一致），失败仅告警不阻断。"""
        try:
            def _mutate(data: dict[str, Any]) -> None:
                data["last_emotion_decay"] = value.isoformat()

            json_state.update_json(self._CONFIG_PATH, _mutate)
        except Exception as e:  # noqa: BLE001
            logger.warning("持久化情绪衰减基准失败: %s", e)

    # ── 节流账本持久化（块E：重启不再丢失退避/节流状态）──────────

    #: `data/scheduler_config.json` 中承载节流账本的键
    _THROTTLE_KEY = "throttle"

    def _load_throttle_ledger(self) -> None:
        """从 `data/scheduler_config.json` 的 `throttle` 段回填四个账本。

        容错原则：**逐字段独立**解析，任一字段损坏只丢该字段（不整段丢弃），
        更不抛异常 —— 账本属优化型状态，构造期崩掉会让调度器整个起不来。
        数值侧统一做有界钳制（失败计数 ≥0、时间戳有限正数），避免外部改坏
        文件后把退避算成负数/NaN。
        """
        try:
            data = json_state.read_json(self._CONFIG_PATH, default={}) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("读取节流账本失败（按空账本启动）: %s", e)
            return
        raw = data.get(self._THROTTLE_KEY)
        if not isinstance(raw, dict):
            return

        next_ok = raw.get("llm_proactive_next_ok")
        if isinstance(next_ok, dict):
            loaded: dict[str, float] = {}
            for k, v in next_ok.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv) and fv > 0:
                    loaded[str(k)] = fv
            self._llm_proactive_next_ok = loaded

        fails = raw.get("deliver_fail_counts")
        if isinstance(fails, dict):
            loaded_f: dict[str, int] = {}
            for k, v in fails.items():
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                if iv > 0:
                    loaded_f[str(k)] = iv
            self._deliver_fail_counts = loaded_f

        disabled = raw.get("disabled_event_day")
        if isinstance(disabled, dict):
            self._disabled_event_day = {
                str(k): str(v) for k, v in disabled.items() if str(v or "")
            }

        wait_day = raw.get("wait_window_event_day")
        if isinstance(wait_day, dict):
            self._wait_window_event_day = {
                str(k): str(v) for k, v in wait_day.items() if str(v or "")
            }

        quiet_day = raw.get("quiet_event_day")
        if isinstance(quiet_day, dict):
            self._quiet_event_day = {
                str(k): str(v) for k, v in quiet_day.items() if str(v or "")
            }

        degraded_day = raw.get("model_degraded_event_day")
        if isinstance(degraded_day, dict):
            self._model_degraded_event_day = {
                str(k): str(v) for k, v in degraded_day.items() if str(v or "")
            }

        gate_skip_day = raw.get("gate_skip_event_day")
        if isinstance(gate_skip_day, dict):
            self._gate_skip_event_day = {
                str(k): str(v) for k, v in gate_skip_day.items() if str(v or "")
            }

        sent = raw.get("important_dates_sent")
        if isinstance(sent, list):
            self._important_dates_sent = {str(x) for x in sent if str(x or "")}

    def _persist_throttle_ledger(self) -> None:
        """把四个账本原子落盘；失败仅告警不阻断（内存态仍然生效）。

        只在**账本真的变化**时调用（见各处调用点注释）——本函数每次都全量覆写
        该段，若挂到热路径上会造成无谓的磁盘写。
        """
        try:
            def _mutate(data: dict[str, Any]) -> None:
                data[self._THROTTLE_KEY] = {
                    "llm_proactive_next_ok": {
                        k: float(v) for k, v in self._llm_proactive_next_ok.items()
                    },
                    "deliver_fail_counts": {
                        k: int(v) for k, v in self._deliver_fail_counts.items()
                    },
                    "disabled_event_day": dict(self._disabled_event_day),
                    # skip 账本治理（2026-10-12）：等待窗/静默闸的每日去重键
                    "wait_window_event_day": dict(self._wait_window_event_day),
                    "quiet_event_day": dict(self._quiet_event_day),
                    # 模型闸（2026-10-15）：getattr 防御——既有测试以 __new__ 构造
                    # stub 且只补部分账本属性，真实实例 __init__ 必然有该键。
                    "model_degraded_event_day": dict(getattr(self, "_model_degraded_event_day", {})),
                    # 确定性节奏闸每日降噪账本（2026-10-17）：键为 "user|reason"，
                    # 与模型闸同 getattr 防御。
                    "gate_skip_event_day": dict(getattr(self, "_gate_skip_event_day", {})),
                    # 只保留最近 7 天（防无界增长；口径与 dedup_key 的日期前缀一致）
                    "important_dates_sent": sorted(self._important_dates_sent)[-200:],
                }

            json_state.update_json(self._CONFIG_PATH, _mutate)
        except Exception as e:  # noqa: BLE001
            logger.warning("持久化节流账本失败（内存态仍生效）: %s", e)

    def purge_throttle_for(self, owner_id: int, session_keys: list[str] | None = None) -> int:
        """账号生命周期（W9）：清除该账号在节流账本（五个字典账本+日期集）中的条目。

        键形态在本 owner 内解析：``llm_proactive_next_ok`` /
        ``deliver_fail_counts`` / ``disabled_event_day`` / ``wait_window_event_day``
        / ``quiet_event_day`` / ``model_degraded_event_day``（2026-10-15 模型闸）
        以会话键（或裸 uid 历史键）为键，按 owner 段精确
        判定；``important_dates_sent`` 的条目是 ``"{date}|{user_key}|{label}"``，
        按中段 user_key 匹配。内存账本与跨 worker 配置文件同步清理，防止下次
        _persist 把已删账号的条目复活。返回移除的条目总数。
        """
        uid = int(owner_id)
        explicit = {str(k) for k in (session_keys or []) if str(k)}

        def _owned(key: str) -> bool:
            if key in explicit:
                return True
            return session_key_mod.owner_of(key) == uid

        def _dedup_user_key(entry: str) -> str:
            # "{date}|{user_key}|{label}" 幂等条目 → 第二段 user_key（会话键），
            # 归属判定随后统一走 utils.session_key 唯一真源
            return entry.partition("|")[2].partition("|")[0]

        removed = 0
        for ledger in (
            self._llm_proactive_next_ok,
            self._deliver_fail_counts,
            self._disabled_event_day,
            # skip 账本治理（2026-10-12）新增的每日去重账本：漏这里会让已删
            # 账号条目残留并随 _persist 复活（与上方 docstring 的自设纪律一致）
            self._wait_window_event_day,
            self._quiet_event_day,
            # 模型闸（2026-10-15）新增每日去重账本：漏这里会让已删账号条目
            # 残留并随 _persist 复活；getattr 防御见 _persist_throttle_ledger 注。
            getattr(self, "_model_degraded_event_day", {}),
        ):
            victims = [k for k in list(ledger) if _owned(str(k))]
            for k in victims:
                ledger.pop(k, None)
                removed += 1
        # 确定性闸每日降噪账本（2026-10-17）：键形为 "{user_key}|{reason}"，
        # 归属判定须先剥 reason 尾段再走 _owned（直接整键过 owner_of 会把
        # "|unanswered_limit" 当成键的一部分误判）。
        gate_skip_map = getattr(self, "_gate_skip_event_day", None)
        if isinstance(gate_skip_map, dict):
            victims = [
                k for k in list(gate_skip_map)
                if _owned(str(k).rpartition("|")[0])
            ]
            for k in victims:
                gate_skip_map.pop(k, None)
                removed += 1
        date_victims = [
            x for x in list(self._important_dates_sent)
            if _owned(_dedup_user_key(str(x)))
        ]
        for x in date_victims:
            self._important_dates_sent.discard(x)
            removed += 1
        if removed:
            self._persist_throttle_ledger()
        return removed

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
        """健康检查

        ⚠️ 缺陷 A：``running`` 只证明**本进程**的 APScheduler 对象还活着；
        驻留运行时的真实判据是控制面心跳（谁跑过 job 谁续拍，跨进程可读）。
        两者并报，僵尸运行时（进程在、job 不再触发）才看得见。
        """
        result = {
            "running": self._scheduler is not None and self._scheduler.running,
            "apscheduler_available": HAS_APSCHEDULER,
            "jobs": len(self.get_jobs()) if self._scheduler else 0,
        }
        rp = self._plane()
        beat: dict = {"holder": "", "age_seconds": None, "alive": False}
        if rp is not None:
            with contextlib.suppress(Exception):
                beat = rp.runtime_status("scheduler")
        result["beat_alive"] = bool(beat.get("alive"))
        result["beat_holder"] = str(beat.get("holder") or "")
        result["beat_age_seconds"] = beat.get("age_seconds")
        return result
