"""时间口径单一真源 —— 存储一律 UTC naive，墙钟只在读边界本地化。

**2026-11-21 时间口径统一批**：全库历史存在三套时钟（chat_history 的 SQLite
UTC naive、topic_state/reminders/agent_core 的北京本地 naive、event_ledger 的
aware 本地 +08:00）。本模块新增三个 UTC 函数作为**写入口径唯一入口**：

- :func:`now_utc_naive`：当前 UTC（naive）—— 所有落库时间戳的默认取值；
- :func:`to_utc_naive`：任意 datetime → UTC naive（naive 视为已是 UTC，幂等）；
- :func:`utc_to_local_naive`：UTC → 北京墙钟 naive，**只允许在读边界调用**
  （静默窗、日期界、prompt 渲染、API 展示）。

另提供 :func:`local_naive_to_utc`（用户输入的北京墙钟 → 存储 UTC）、
:func:`local_text_to_utc_text` / :func:`utc_text_to_local_text`（提醒等
文本时间列的双向边界转换）与 :func:`to_api_utc`（API 序列化带 +00:00）。

墙钟语义（现在几点 / 今天是哪天）仍走 :func:`now_local`；它只用于读侧判断，
不得把返回值直接写进数据库。

**背景（2026-09-20 修复）**：`shisi/memory/legacy/memory_pipeline.py` 与
`my_character/enhanced_prompt_engine.py` 曾用 ``datetime.now(tz=timezone.utc)``
取「小时 / 日期」做墙钟判定。对 UTC+8 部署（生产服务器 TZ=Asia/Beijing）后果：

- 深夜时段判定错位 8 小时 —— ``_is_late_night``（23:00–05:00）实际在
  **本地 07:00–13:59** 触发，真正的本地深夜反而不触发，
  "深夜情感词 → 强制存事实 + 重要性 +0.3"完全落到上午/中午；
- 日记 / 维护按 UTC 切日 —— 本地 00:00–08:00 的消息被归入前一天。

本实现的逻辑原在 ``proactive/ase_engine._local_now``（全项目唯一正确的一处，
``proactive/scheduler.py`` 已明确与之共用）。本次提为公共真源，避免"多套时钟"。

**使用纪律**：``now_local`` 只用于墙钟字段（``hour`` / ``date()`` / ``weekday()`` /
``strftime``）。**不要**拿它做跨时区的时间差运算 —— 历史时间戳的落库/比较
路径统一走 UTC（见上方三个函数），混用会算错经过时长。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# UTC+8 的秒级偏移（北京时区）
_UTC8_OFFSET_SECONDS = 8 * 3600
# 偏差容忍：超过 1 小时即认为系统时区不是北京时区
_UTC8_TOLERANCE_SECONDS = 3600
# 北京固定时区（中国无夏令时，用固定偏移，避免依赖 tzdata）
_BEIJING = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """返回当前本地时间（北京时区语义）。

    优先用系统本地时间（服务器应配置 Asia/Shanghai）；若系统时区非 UTC+8
    （如容器内默认 UTC），强制回退到 UTC+8，使墙钟判定不随部署环境漂移。

    返回值形态与原 ``ase_engine._local_now`` 完全一致：系统时区正确时返回
    **naive** datetime，回退分支返回 **aware**（UTC+8）datetime —— 调用方因此
    必须只读墙钟字段，不得与历史时间戳做跨时区算术。

    ⚠️ 本函数是**读边界**工具（静默窗/日期界/prompt 渲染）。所有写库时间戳
    必须走 :func:`now_utc_naive`，禁止把本函数返回值直接落库。
    """
    # 系统时区偏移（秒）：夏令时生效时用 altzone
    offset_sec = (
        -time.altzone if time.daylight and time.localtime().tm_isdst else -time.timezone
    )
    if abs(offset_sec - _UTC8_OFFSET_SECONDS) > _UTC8_TOLERANCE_SECONDS:
        return datetime.now(tz=timezone.utc).astimezone(_BEIJING)
    return datetime.now()


# ── UTC 写入口径（2026-11-21 时间口径统一批）────────────────────────


def now_utc_naive() -> datetime:
    """当前 UTC 时间（naive）—— 全库时间戳写入口径的唯一真源。

    返回 naive 是为了与 SQLite ``CURRENT_TIMESTAMP`` 的
    ``YYYY-MM-DD HH:MM:SS`` 文本口径逐字对齐；需要 aware 时调用方自行补
    ``tzinfo=timezone.utc``。
    """
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime) -> datetime:
    """任意 datetime → UTC naive（幂等）。

    - aware：按真实时刻换算到 UTC，再摘掉 tzinfo；
    - naive：视为**已经是 UTC 存储口径**，原样返回（不重复减 8 小时）。

    用户输入的北京墙钟请先用 :func:`local_naive_to_utc` 标记时区再进入本函数。
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def local_naive_to_utc(value: datetime) -> datetime:
    """北京墙钟 naive → UTC naive（LLM/用户输入、旧本地口径迁移用）。

    aware 输入按真实时刻换算；naive 输入一律按北京墙钟解释。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=_BEIJING)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local_naive(value: datetime) -> datetime:
    """UTC naive/aware → 北京墙钟 naive（**只允许在读边界调用**）。

    静默窗、日期界（每日总结/早安）、prompt 渲染与 API 展示统一经此转换；
    转换结果不得再写回数据库时间列。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_BEIJING).replace(tzinfo=None)


def local_text_to_utc_text(value: object) -> str:
    """北京墙钟文本 ``YYYY-MM-DD HH:MM[:SS]`` → UTC 文本 ``YYYY-MM-DD HH:MM:SS``。

    用于提醒等「用户/模型给本地时间、数据库存 UTC」的文本时间列。空值返回空串；
    畸形值原样返回（由上层校验报错，不在这里静默吞掉）。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    for fmt in (_SQLITE_TS_FMT, "%Y-%m-%d %H:%M"):
        try:
            local_dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return local_naive_to_utc(local_dt).strftime(_SQLITE_TS_FMT)
    return raw


def utc_text_to_local_text(value: object) -> str:
    """UTC 文本 ``YYYY-MM-DD HH:MM:SS`` → 北京墙钟文本（读边界展示用）。

    空值返回空串；畸形值原样返回（fail-open，旧数据/合成值不炸）。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        utc_dt = datetime.strptime(raw, _SQLITE_TS_FMT)
    except ValueError:
        return raw
    return utc_to_local_naive(utc_dt).strftime(_SQLITE_TS_FMT)


def parse_storage_ts(value: object) -> datetime | None:
    """解析存储时间值 → UTC naive datetime；不可解析返回 None（fail-open）。

    兼容 ``YYYY-MM-DD HH:MM[:SS]`` 与 ISO（带 ``T`` / 微秒 / 时区偏移）。
    naive 一律按 UTC 解释；aware 先换算到 UTC 再摘 tzinfo。
    """
    if isinstance(value, datetime):
        return to_utc_naive(value)
    raw = str(value or "").strip()
    if not raw:
        return None
    # Python 3.10 的 fromisoformat 不认尾缀 Z；统一换成显式偏移。
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in (_SQLITE_TS_FMT, "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return to_utc_naive(parsed)


def to_api_utc(value: object) -> str | None:
    """数据库时间值 → 带 ``+00:00`` 的 ISO（API 序列化统一口径）。

    浏览器把不带时区的 ISO 当**本地时间**解析（审计实证：UTC naive 串在
    UTC+8 浏览器上整体偏移 8 小时）。API 边界一律输出显式时区，前端
    ``new Date(...)`` 才能正确换算。空值/畸形返回 ``None``。
    """
    parsed = parse_storage_ts(value)
    if parsed is None:
        return None
    return parsed.replace(microsecond=0).isoformat() + "+00:00"


# SQLite 的 CURRENT_TIMESTAMP / datetime('now') 输出的格式（UTC 墙钟）
_SQLITE_TS_FMT = "%Y-%m-%d %H:%M:%S"


def _current_utc_offset() -> timedelta:
    """本地墙钟相对 UTC 的偏移（北京时区 = +8h）。

    ⚠️ 取**此刻**的两种读数求差，因此**与传入的 datetime 无关** ——
    时区偏移是主机/时区的属性，不是某个具体时刻的属性。
    （早期版本误用"传入时刻 − 当前 UTC"，当传入的是历史/构造时刻时
    会算出完全错误的偏移；由 `test_explicit_now_is_inside_its_own_window` 抓出。）
    """
    local = now_local()
    if local.tzinfo is not None:  # UTC+8 回退分支
        return local.utcoffset() or timedelta(0)
    # naive：用同一瞬间的 UTC 读数求差
    utc_naive = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    return local - utc_naive


def local_day_utc_bounds(now: datetime | None = None) -> tuple[str, str]:
    """返回「**本地当天**」对应的 UTC 时刻区间 ``[start, end)``。

    格式与 SQLite ``CURRENT_TIMESTAMP`` 一致（``YYYY-MM-DD HH:MM:SS``），
    以便直接用于 ``created_at`` 一类的列做字符串区间比较。

    **为何需要它**：``created_at`` 由 ``DEFAULT CURRENT_TIMESTAMP`` 写入（UTC），
    而 SQLite 的 ``date('now')`` **也是 UTC 日** —— 两者同源却都不是本地日，
    于是 ``date(created_at) = date('now')`` 在 UTC+8 上使"今天"从**本地 08:00**
    才换日：凌晨 00:00–08:00 的对话会被算进"昨天"，而"今日摘要"的取数窗口
    与本地日期标签脱钩。改为在应用层按本地日划窗口即可两处同时对齐。

    Args:
        now: 指定"哪一天"的本地时刻（便于测试）；缺省取 :func:`now_local`。
            时区偏移始终按**此刻**的真实偏移计算，与该参数无关。

    Returns:
        ``(start, end)`` —— 均为 UTC 墙钟字符串，左闭右开。
    """
    local = now if now is not None else now_local()
    offset = _current_utc_offset()
    # 统一按"墙钟读数"运算：先剥掉 tzinfo，再用偏移换算成 UTC 读数
    wall = local.replace(tzinfo=None)
    start = wall.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        (start - offset).strftime(_SQLITE_TS_FMT),
        (start + timedelta(days=1) - offset).strftime(_SQLITE_TS_FMT),
    )



# ── 时间锚点（2026-10-11 时间感知批）────────────────────────
#
# 生产背景：主链 system prompt 无任何时间注入，历史/尾巴也不带时间戳 ——
# 用户中秋发消息、国庆再发，模型把半个月前当"刚刚"。
#
# 格式参照（调研留痕 LOG 2026-10-11）：
# - hermes-agent issue #62369 的 `[time anchor]` 单行惯例（当前本地时间+星期+
#   距上条消息时长，~30 token，fail-open）；
# - SillyTavern 官方宏 {{isodate}}/{{weekday}}/{{idleDuration}} 的信号组合语义
#   （AGPL-3.0 项目，仅语义对齐，未抄代码）。

_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def weekday_cn(wall: datetime) -> str:
    """北京墙钟 datetime → 中文星期（会话分段渲染等注入侧共用）。"""
    return _WEEKDAY_CN[wall.weekday()]


def parse_db_timestamp(value: object) -> datetime | None:
    """解析落库时间戳（chat_history.created_at 等）为 **aware UTC** datetime。

    写入口径：``DEFAULT CURRENT_TIMESTAMP`` 的 naive UTC 串
    （``YYYY-MM-DD HH:MM:SS``）；兼容 isoformat（可能带 ``+00:00`` 或微秒）。
    **naive 一律按 UTC 解释**（created_at 的唯一写入口径）。
    畸形/缺失返回 None —— fail-open，调用方跳过时间信号而不是崩。
    """
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s, _SQLITE_TS_FMT)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def beijing_wall(value: object) -> datetime | None:
    """落库时间戳 → 北京墙钟（naive）。失败返回 None（fail-open）。"""
    dt = parse_db_timestamp(value)
    if dt is None:
        return None
    return dt.astimezone(_BEIJING).replace(tzinfo=None)


def _wall_clock(now: datetime | None) -> datetime:
    """统一 now 为北京 naive 墙钟（now_local 两形态归一）。"""
    local = now if now is not None else now_local()
    if local.tzinfo is not None:
        return local.astimezone(_BEIJING).replace(tzinfo=None)
    # naive：now_local 正常分支已是宿主北京墙钟，直接用
    return local


def humanize_gap_cn(seconds: float) -> str:
    """秒数 → 中文人性化时长（SillyTavern {{idleDuration}} 语义）。"""
    secs = max(0.0, float(seconds))
    if secs < 60:
        return "不到 1 分钟"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟"
    if secs < 48 * 3600:
        half_hours = round(secs / 1800.0) / 2.0
        text = f"{half_hours:.1f}".rstrip("0").rstrip(".")
        return f"{text} 小时"
    return f"{int(secs // 86400)} 天"


# 推理指引（2026-10-12 时间线推理批，≤3 行）：只给"现在几点"不够 ——
# 生产实证：用户 08:13 叙述清晨六点的事件，角色 08:14 仍按"正在发生"回应。
# 三条铁则随锚点一起注入 system prompt（persona_service 原样渲染 time_anchor 槽）。
TIME_REASONING_GUIDE = (
    "- 历史里用户提到的钟点/日期是用户叙述的事件（过去发生或计划），"
    "必须结合该条消息的发送时刻（行内 [MM-DD HH:MM] 前缀）与当前时间推理先后，"
    "不得当作正在发生；\n"
    "- 对用户当前所处位置/状态不确定时直接问，"
    "不要替用户假设（在宿舍/在操场/在教室这类）；\n"
    "- 不要虚构自己所处的环境、天气或状态当事实；行首 [MM-DD HH:MM] 是系统给历史"
    "消息加的时间注记（元数据），**你回复时绝对不要写出任何这类前缀**。"
)


def time_anchor_line(last_message_at: object = None, now: datetime | None = None) -> str:
    """时间锚点：当前日期+星期+时刻+距上一条消息时长 + ≤3 行推理指引。

    首行为 hermes-agent #62369 惯例的单行锚点；2026-10-12 起其后追加
    :data:`TIME_REASONING_GUIDE`（≤3 行）——生产实证只有锚点没有指引时，
    模型把用户叙述的钟点当正在发生。

    Args:
        last_message_at: 上一条消息的落库时间戳（UTC 口径，naive 串按 UTC）。
            None / 畸形 → 跳过间隔段（fail-open，不虚构"多久前"）。
        now: 指定"现在"（测试用）；缺省取 :func:`now_local`。

    生产注入点：主链 system prompt（persona_service 渲染 time_anchor 槽）。
    """
    wall = _wall_clock(now)
    head = (
        f"[时间锚点] {wall:%Y-%m-%d} {_WEEKDAY_CN[wall.weekday()]} {wall:%H:%M}（北京时间）"
    )
    last = beijing_wall(last_message_at)
    if last is None:
        head += " | 本会话暂无历史消息"
    else:
        gap = (wall - last).total_seconds()
        if gap <= 0:
            head += " | 上一条消息就在刚才"
        else:
            head += f" | 上一条消息：{humanize_gap_cn(gap)}前（{last:%m-%d %H:%M}）"
    return f"{head}\n{TIME_REASONING_GUIDE}"
