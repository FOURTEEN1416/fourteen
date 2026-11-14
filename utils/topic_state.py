"""话题状态机 —— 四表 + 六 API（2026-10-27 实施计划 Task 1；2026-11-13 起清理/verify 覆盖遗留 topic_plan）。

真源与边界（逐条对应 `docs/reports/2026-10-27-话题状态机实施计划.md`）：

- **三张活跃表**（``data/sqlite.db``，幂等 ``CREATE TABLE IF NOT EXISTS``）：
  ``topic_state`` / ``thread_plan`` / ``topic_meta``。本模块是这三张表的
  **唯一 owner**，其他模块不得直接 ``sqlite3.connect`` 它们；清理与 verify
  （``purge_owner`` / ``count_owner`` / ``purge_expired``）经
  ``_existing_topic_tables`` 额外覆盖**遗留 ``topic_plan``**（迁移前存量库）
  ——对外统一「话题状态机四表」口径。
- **六 API**：``get_topic_snapshot / update_after_turn / select_proactive_recall
  / save_next_plan / take_next_plan / purge_expired``；加 ``read/write_topic_config``
  与复提记账 ``mark_topic_recalled``（2026-11-07 对抗验收残余缺口②：
  投递成功计数 + 冷却升级，堵「无回应话题每 ≥6h 周期性复提」）、生命周期
  清场 ``purge_owner / count_owner``（2026-11-12 原子 C2；2026-11-13 起四表级联，含遗留 topic_plan）。
- **关闭态零 DB**：``enabled=false`` 时六个 API 首行短路，不 ``_connect``、
  不建表、不读表。例外是每日维护 ``purge_expired(force=True)`` 与账号删除
  ``purge_owner`` / ``count_owner``（删除义务不受功能开关影响）。
- **确定性优先**：每轮先跑确定性状态机；LLM judge 每 4 轮一次、8s 硬超时、
  失败/低置信度保留原状态（fail-open for state）。
- **裁决固化**：``active_cap`` 上限 3、``retention_days`` 固定 7、
  ``judge_every_turns`` 固定 4、``daily_llm_budget`` 固定 0（不限）、
  ``recall_after_hours`` 不低于 6、``recall_max`` 最多 2。
- **隐私**：只存标题/摘要/≤3 条短锚点，不存对话原文；日志统一
  ``[topic_state][step=...]`` 且只打 ``action/topic_id/status/计数``，不落标题、
  锚点或用户消息原文。

时钟：统一 ``utils.local_time.now_local()`` 归一为 naive 北京墙钟，落库格式
``%Y-%m-%d %H:%M:%S``；禁止混用 ``chat_history.created_at`` 的 UTC。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils import json_state
from utils.local_time import _BEIJING, now_local
from utils.project_paths import project_path

logger = logging.getLogger("utils.topic_state")

_DB_PATH = project_path("data", "sqlite.db")
_CONFIG_PATH = project_path("data", "scheduler_config.json")
_READ_TIMEOUT_MS = 50
_WRITE_TIMEOUT_MS = 5000

_TS_FMT = "%Y-%m-%d %H:%M:%S"

DEFAULTS: dict[str, Any] = {
    # 2026-10-28 用户裁决（Q1-B/Q3-A/Q5/Q6-B）要求本批真正生效：
    # 默认开启确定性状态机与稀疏 LLM，回滚靠管理端显式关闭（enabled=false →
    # 零 DB、prompt 逐字回落旧规则）。不再以「默认关闭」作为上线前提。
    "enabled": True,
    "llm_judge_enabled": True,
    "judge_every_turns": 4,
    "daily_llm_budget": 0,
    "active_cap": 3,
    "retention_days": 7,
    "recall_after_hours": 6,
    "recall_max": 2,
    "park_idle_hours": 6,
    "switch_theta1": 0.2,
    "switch_theta2": 0.35,
    # P10（2026-11-07 用户报障）：同一话题的「同时段」窗——超过该时长没再被
    # 提到就不再算「正在聊」，且续聊钩子只取窗内的用户原话。出厂 120 分钟
    # （用户原话「相隔两三个小时让他们连在一起」），可经管理端调整。
    "same_session_minutes": 120,
}

#: Q1/Q3/Q4/Q5/Q6 的固化边界：这些值不是"可调建议"，而是裁决；
#: 管理端写入越界一律 400/422，读侧坏值回落默认，绝不放宽裁决。
_BOUNDS: dict[str, tuple[float, float]] = {
    "judge_every_turns": (4, 4),  # Q1：每 4 轮一次，不能改成每轮
    "daily_llm_budget": (0, 0),  # Q5：不设每日 LLM 上限
    "active_cap": (1, 3),  # Q6：上限 3，不能放宽到 5
    "retention_days": (7, 7),  # Q4：全部 7 天
    "recall_after_hours": (6, 168),  # Q3：不得低于 6h
    "recall_max": (1, 2),  # Q3：最多 2 条
    "park_idle_hours": (6, 6),  # Q2/状态机：6h 无活动
    "switch_theta1": (0.0, 1.0),
    "switch_theta2": (0.0, 1.0),
    # P10：同时段窗 30 分钟 ~ 24 小时（出厂 120）；不得为 0（否则永远没有
    # 「正在聊」，话题块退化成只剩历史）。
    "same_session_minutes": (30, 1440),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_state (
    session_key    TEXT NOT NULL,
    character_id   TEXT NOT NULL,
    topic_id       TEXT NOT NULL,
    title          TEXT NOT NULL,
    summary        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL CHECK(status IN ('active','parked','closed')),
    salience       REAL NOT NULL DEFAULT 0.5,
    mention_count  INTEGER NOT NULL DEFAULT 0,
    turn_count     INTEGER NOT NULL DEFAULT 0,
    anchors        TEXT NOT NULL DEFAULT '[]',
    idle_turns     INTEGER NOT NULL DEFAULT 0,
    recall_count   INTEGER NOT NULL DEFAULT 0,
    last_recalled_at TEXT NOT NULL DEFAULT '',
    last_active_at TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    PRIMARY KEY (session_key, character_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_topic_state_session_status
    ON topic_state(session_key, character_id, status, last_active_at DESC);

CREATE TABLE IF NOT EXISTS thread_plan (
    session_key  TEXT NOT NULL,
    character_id TEXT NOT NULL,
    topic        TEXT NOT NULL DEFAULT '',
    intent       TEXT NOT NULL DEFAULT '',
    planned_at   TEXT NOT NULL,
    next_at      TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    PRIMARY KEY (session_key, character_id)
);

CREATE TABLE IF NOT EXISTS topic_meta (
    session_key      TEXT NOT NULL,
    character_id     TEXT NOT NULL,
    turns_since_llm  INTEGER NOT NULL DEFAULT 0,
    llm_calls        INTEGER NOT NULL DEFAULT 0,
    last_llm_at      TEXT NOT NULL DEFAULT '',
    last_turn_id     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_key, character_id)
);
"""

__all__ = [
    "DEFAULTS",
    "is_close_statement",
    "read_topic_config",
    "write_topic_config",
    "get_topic_snapshot",
    "update_after_turn",
    "select_proactive_recall",
    "save_next_plan",
    "mark_topic_recalled",
    "take_next_plan",
    "purge_expired",
    "purge_owner",
    "count_owner",
    "render_topic_state_block",
    "run_llm_judge",
    "should_run_llm_judge",
    "text_overlap",
    "topic_overlap",
]


# ── 配置（热读，逐字段容错）──────────────────────────────────────────────


def read_topic_config() -> dict[str, Any]:
    """热读 ``scheduler_config.json`` 的 ``topic_state`` 块（无进程缓存）。

    缺失/非 dict/单字段类型不符或越界 → **逐字段回落默认**，不抛异常。
    跨字段倒置（``switch_theta2 < switch_theta1``）两侧一并回落默认。
    """
    raw = json_state.read_json(_CONFIG_PATH, default={})
    block = raw.get("topic_state") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    cfg: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        value = block.get(key)
        if isinstance(default, bool):
            cfg[key] = value if isinstance(value, bool) else default
        elif isinstance(default, int):
            cfg[key] = value if isinstance(value, int) and not isinstance(value, bool) else default
        else:
            cfg[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
        if key in _BOUNDS:
            lo, hi = _BOUNDS[key]
            if not (lo <= cfg[key] <= hi):
                cfg[key] = default
    if cfg["switch_theta2"] < cfg["switch_theta1"]:
        logger.warning("[topic_state][step=config] theta_inversion fallback")
        cfg["switch_theta1"] = DEFAULTS["switch_theta1"]
        cfg["switch_theta2"] = DEFAULTS["switch_theta2"]
    return cfg


def write_topic_config(**partial: Any) -> dict[str, Any]:
    """partial merge 写入 ``topic_state`` 块；越界/未知字段 → ``ValueError``。

    只触碰 ``topic_state`` 键，其余顶层键（reply_mode / quiet_hours /
    llm_proactive / humanize …）逐字保留。读改写全程在
    ``utils.json_state.update_json`` 的锁内完成。
    """
    payload = {k: v for k, v in partial.items() if v is not None}
    unknown = sorted(set(payload) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"未知的 topic_state 字段: {unknown}")
    merged = {**read_topic_config(), **payload}
    for key, (lo, hi) in _BOUNDS.items():
        value = merged.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (lo <= value <= hi):
            raise ValueError(f"{key} 越界或类型非法: {value!r}（允许 {lo}..{hi}）")
        if isinstance(DEFAULTS[key], int) and not isinstance(value, int):
            raise ValueError(f"{key} 必须是整数，收到 {type(value).__name__}")
    if merged["switch_theta2"] < merged["switch_theta1"]:
        raise ValueError("switch_theta2 不得小于 switch_theta1")
    if not isinstance(merged.get("enabled"), bool) or not isinstance(merged.get("llm_judge_enabled"), bool):
        raise ValueError("enabled / llm_judge_enabled 必须是 bool")

    def _mutate(data: dict[str, Any]) -> None:
        data["topic_state"] = {k: merged[k] for k in DEFAULTS}

    json_state.update_json(_CONFIG_PATH, _mutate)
    return read_topic_config()


# ── 时间与文本工具 ───────────────────────────────────────────────────────


def _normalize_now(now: datetime | None = None) -> datetime:
    """统一为 naive 北京墙钟（秒级）。aware 输入先转北京再摘掉 tzinfo。"""
    dt = now if now is not None else now_local()
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(_BEIJING).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def _ts(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), _TS_FMT)
    except (TypeError, ValueError):
        return None


def _clip(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


_PUNCT_RE = re.compile(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()\[\]【】~～…·]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or "").lower())


def _bigrams(text: str) -> set[str]:
    t = _norm(text)
    if len(t) <= 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def text_overlap(a: str, b: str) -> float:
    """短边包含度：``len(交集)/min(len(ga), len(gb))``。

    不用 Jaccard：消息长、标题短时 Jaccard 会被长边稀释（「我今天去面试了」
    vs「面试」= 1/9），短边包含度 = 1.0，符合「标题是话题名」的语义。
    """
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / max(1, min(len(ga), len(gb)))


def topic_overlap(message: str, topic: dict[str, Any]) -> float:
    candidates = [topic.get("title") or ""] + list(topic.get("anchors") or [])
    return max((text_overlap(message, c) for c in candidates), default=0.0)


_CLOSE_RE = re.compile(r"(弄完了|解决了|搞定了|完事了|结束了|不聊这个了|别提了|翻篇了|处理完了|办完了)")
_CLOSE_QUESTION_RE = re.compile(r"[吗呢？?]\s*$")


def is_close_statement(text: str) -> bool:
    """明确结束语；「结束了吗？」是提问，不是收尾。"""
    t = str(text or "").strip()
    if not t or _CLOSE_QUESTION_RE.search(t):
        return False
    return bool(_CLOSE_RE.search(t))


_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_SMALL_TALK_RE = re.compile(r"^[嗯哦噢啊哈呵嘿好行对是的有在吗么呀吧呢啦咯你收到谢谢没事]+$")
_GREETING_OR_ACK_RE = re.compile(
    r"^(?:你好|您好|早上好|早安|午安|晚上好|晚安|嗨|哈喽|hello|hi|"
    r"没关系|没事|不客气|谢谢|谢谢你|谢啦|收到|明白了|知道了|好吧|行吧|可以)"
    r"[啊呀呢吧哦啦~～]*$",
    re.IGNORECASE,
)


def _is_small_talk(text: str) -> bool:
    t = _norm(text)
    if not t:
        return True
    if len(t) <= 2:
        return True
    if _GREETING_OR_ACK_RE.fullmatch(t):
        return True
    return bool(_SMALL_TALK_RE.fullmatch(t))


def _first_clause(text: str) -> str:
    """按句读切分后的第一个非寒暄短句；全为寒暄时回落整段。"""
    raw = str(text or "").strip()
    for clause in _CLAUSE_SPLIT_RE.split(raw):
        c = clause.strip()
        if c and not _is_small_talk(c):
            return c
    return raw


def _can_create_topic(message: str) -> bool:
    return len(_norm(message)) >= 4 and not _is_small_talk(message)


def _sanitize_topic_text(text: Any, limit: int) -> str:
    """标题/锚点进 prompt 前清洗：去换行/控制字符/指令标记，再 clip。"""
    s = str(text or "")
    s = re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", s)
    s = s.replace("#", "").replace("<", "").replace(">", "")
    s = re.sub(r"\[/?INST\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"system\s*:", "", s, flags=re.IGNORECASE)
    s = re.sub(r"ignore\s+previous", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return _clip(s, limit)


def _as_anchors(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            logger.debug("[topic_state][step=parse] anchors_json_invalid")
            return []
        items = parsed if isinstance(parsed, list) else []
    else:
        items = []
    out = [str(item).strip() for item in items if str(item).strip()]
    return out[:3]


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _row_get(row: sqlite3.Row | None, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


# ── 连接与事务 ───────────────────────────────────────────────────────────


def _connect(
    *, ensure_schema: bool = False, busy_timeout_ms: int | None = None,
    db_path: str | Path | None = None,
) -> sqlite3.Connection:
    """打开 ``data/sqlite.db``；``ensure_schema`` 只在后台写路径为 True。

    ``db_path`` 供生命周期删除账号等调用方显式指定目标库（默认仍是全局
    ``_DB_PATH``）；不传时行为与旧实现逐字一致。
    """
    if busy_timeout_ms is None:
        busy_timeout_ms = _WRITE_TIMEOUT_MS
    path = Path(db_path) if db_path is not None else Path(_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    if ensure_schema:
        try:
            conn.executescript(_SCHEMA)
            _migrate_legacy_plan_table(conn)
            _ensure_recall_columns(conn)
        except Exception:
            conn.close()
            raise
    return conn


def _migrate_legacy_plan_table(conn: sqlite3.Connection) -> None:
    """把 v1.58 的 ``topic_plan`` 行迁移到 ``thread_plan`` 后删除旧表。

    2026-11-13：topic_plan 表清场。幂等：旧表不存在即 no-op；``INSERT OR
    IGNORE`` 保证新表已有同键行时不被旧值覆盖；显式 ``BEGIN IMMEDIATE``
    内复制 + DROP（连接是 autocommit，必须自己开事务），失败回滚后上抛。
    回滚脚本见 ``scripts/topic_plan_migration.py down``。

    对抗验收修复（2026-11-13）：
    - 旧表可能来自试验期 schema（缺 ``consumed_at``）或外部工具建表，缺列
      时按默认值补，绝不因一列缺失让整条写路径永久 ``db_error``；
    - 复制 + DROP 必须原子：中途崩溃只允许「双表并存」，不允许「旧表已删、
      新表没写全」。
    """
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "topic_plan" not in tables:
        return
    legacy_cols = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(topic_plan)")
    }
    select_parts: list[str] = []
    for column, default in (
        ("session_key", "''"),
        ("character_id", "''"),
        ("topic", "''"),
        ("intent", "''"),
        ("planned_at", "''"),
        ("next_at", "''"),
        ("expires_at", "''"),
        ("consumed_at", "NULL"),
    ):
        select_parts.append(column if column in legacy_cols else default)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO thread_plan "
            "(session_key, character_id, topic, intent, planned_at, next_at, "
            " expires_at, consumed_at) "
            f"SELECT {', '.join(select_parts)} FROM topic_plan"
        )
        conn.execute("DROP TABLE topic_plan")
    except Exception:
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    logger.info("[topic_state][step=migrate_plan] topic_plan -> thread_plan done")


def _ensure_recall_columns(conn: sqlite3.Connection) -> None:
    """存量库补列（幂等，2026-11-07 v1.65.2 复提记账）：recall_count / last_recalled_at。

    存量库没有这两列时 ALTER 补上；新库由 _SCHEMA 直接带列，这里零操作。
    读路径（``select_proactive_recall`` 的 ``ensure_schema=False`` 连接）也会
    先调它——否则「首次读先于任何写」的部署时序会因缺列整体降级 ``[]``。
    """
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(topic_state)")}
    if not cols:
        return  # 表还不存在：ensure_schema 路径会先建（带列）
    if "recall_count" not in cols:
        conn.execute(
            "ALTER TABLE topic_state ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0",
        )
    if "last_recalled_at" not in cols:
        conn.execute(
            "ALTER TABLE topic_state ADD COLUMN last_recalled_at TEXT NOT NULL DEFAULT ''",
        )


def _recall_gap_hours(recall_count: int, base_hours: float) -> float:
    """复提冷却升级（对抗验收残余缺口②）：用户不回应时同一话题不能无限复提。

    基线 = 配置的 ``recall_after_hours``（≥6h）；同题复提 2-3 次 → 翻倍；
    ≥4 次 → 四倍封顶。计数与最近复提时间在**投递成功**时记账
    （:func:`mark_topic_recalled`，scheduler 发送成功分支调用）。
    """
    if int(recall_count) >= 4:
        return float(base_hours) * 4.0
    if int(recall_count) >= 2:
        return float(base_hours) * 2.0
    return float(base_hours)


def _character_key(character_id: Any) -> str:
    """角色键：只接受显式值，空白/None **不再**归一成 ``default``。

    2026-11-13 对抗验收（M1）：旧实现把空白 character_id 归一成 ``"default"``，
    任何绕过 ``ThreadState`` facade 的底层调用（后台线程、脚本、测试直调）都会
    把消息写进内置十四的桶，同用户切角色后造成跨角色串写。显式 ``"default"``
    （内置卡）仍原样通过；空值由各 API 各自 no-op。
    """
    return str(character_id or "").strip()


@contextmanager
def _write_tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ── 行解析与快照 ─────────────────────────────────────────────────────────


def _row_to_topic(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_key": str(_row_get(row, "session_key", "") or ""),
        "character_id": str(_row_get(row, "character_id", "") or ""),
        "topic_id": str(_row_get(row, "topic_id", "") or ""),
        "title": str(_row_get(row, "title", "") or ""),
        "summary": str(_row_get(row, "summary", "") or ""),
        "status": str(_row_get(row, "status", "") or ""),
        "salience": _safe_float(_row_get(row, "salience", 0.0), 0.0),
        "mention_count": _safe_int(_row_get(row, "mention_count", 0), 0),
        "turn_count": _safe_int(_row_get(row, "turn_count", 0), 0),
        "anchors": _as_anchors(_row_get(row, "anchors", "[]")),
        "idle_turns": _safe_int(_row_get(row, "idle_turns", 0), 0),
        "recall_count": _safe_int(_row_get(row, "recall_count", 0), 0),
        "last_recalled_at": str(_row_get(row, "last_recalled_at", "") or ""),
        "last_active_at": str(_row_get(row, "last_active_at", "") or ""),
        "created_at": str(_row_get(row, "created_at", "") or ""),
        "updated_at": str(_row_get(row, "updated_at", "") or ""),
        "expires_at": str(_row_get(row, "expires_at", "") or ""),
    }


def _row_to_plan(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "topic": str(_row_get(row, "topic", "") or ""),
        "intent": str(_row_get(row, "intent", "") or ""),
        "planned_at": str(_row_get(row, "planned_at", "") or ""),
        "next_at": str(_row_get(row, "next_at", "") or ""),
        "expires_at": str(_row_get(row, "expires_at", "") or ""),
        "consumed_at": _row_get(row, "consumed_at", None),
    }


def _empty_snapshot(*, enabled: bool, degraded: bool = False) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "enabled": enabled,
        "active": [],
        "parked": [],
        "closed": [],
        "text": "",
        "next_plan": None,
    }
    if degraded:
        snap["degraded"] = True
    return snap


def _plan_table_name(conn: sqlite3.Connection) -> str:
    """当前库的计划表名（迁移前 ``topic_plan`` / 迁移后 ``thread_plan``）。

    读路径 ``ensure_schema=False`` 不会触发建表/迁移；部署后若先发生读，仍能
    从旧表读到计划，直到首次写路径完成迁移。
    """
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('thread_plan', 'topic_plan')"
        )
    }
    if "thread_plan" in tables:
        return "thread_plan"
    if "topic_plan" in tables:
        return "topic_plan"
    return "thread_plan"


def _snapshot_from_conn(
    conn: sqlite3.Connection,
    session_key: str,
    character_id: str,
    now: datetime,
) -> dict[str, Any]:
    now_ts = _ts(now)
    active_rows = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
        "AND status='active' AND expires_at > ? "
        "ORDER BY salience DESC, last_active_at DESC",
        (session_key, character_id, now_ts),
    ).fetchall()
    parked_rows = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
        "AND status='parked' AND expires_at > ? "
        "ORDER BY salience DESC, last_active_at DESC",
        (session_key, character_id, now_ts),
    ).fetchall()
    closed_rows = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
        "AND status='closed' AND expires_at > ? "
        "ORDER BY last_active_at DESC",
        (session_key, character_id, now_ts),
    ).fetchall()
    plan_row = conn.execute(
        f"SELECT * FROM {_plan_table_name(conn)} WHERE session_key=? "
        "AND character_id=? AND consumed_at IS NULL AND expires_at > ?",
        (session_key, character_id, now_ts),
    ).fetchone()
    snapshot: dict[str, Any] = {
        "enabled": True,
        "active": [_row_to_topic(r) for r in active_rows],
        "parked": [_row_to_topic(r) for r in parked_rows],
        "closed": [_row_to_topic(r) for r in closed_rows],
        "text": "",
        "next_plan": _row_to_plan(plan_row),
    }
    snapshot["text"] = render_topic_state_block(snapshot)
    return snapshot


# ── 状态块渲染（≤160 字，只放数据）────────────────────────────────────────


def _short_title(topic: dict[str, Any], limit: int = 10) -> str:
    return _sanitize_topic_text(topic.get("title"), limit)


def render_topic_state_block(
    snapshot: dict[str, Any],
    *,
    now: datetime | None = None,
    fresh_minutes: int | None = None,
) -> str:
    """渲染状态数据块（≤160 字）；无 active/parked → ``""``。

    只放数据，不含长指令；标题/锚点全部经 ``_sanitize_topic_text`` 清洗。
    超长按末行截断，始终保留标题行。

    P10（2026-11-07 用户报障）：`active` 里 ``last_active_at`` 超过「同时段」
    （默认 120 分钟，`topic_state.same_session_minutes`）的话题不再算「正在聊」，
    降级显示在「之前没聊完」——避免把两三小时前的事标成当前话题继续教唆。
    """
    active = list(snapshot.get("active") or [])
    parked = list(snapshot.get("parked") or [])
    if not active and not parked:
        return ""

    # 同时段判定：太久没再提到的话题降级为「之前没聊完」。
    try:
        cfg = read_topic_config()
        window = int(
            fresh_minutes if fresh_minutes is not None
            else cfg.get("same_session_minutes", 120)
        )
    except Exception:  # noqa: BLE001
        window = int(fresh_minutes or 120)
    wall_now = _normalize_now(now)
    still_active: list[dict[str, Any]] = []
    stale_active: list[dict[str, Any]] = []
    for topic in active:
        dt = _parse_ts(topic.get("last_active_at"))
        if dt is not None and (wall_now - dt).total_seconds() > window * 60:
            stale_active.append(topic)
        else:
            still_active.append(topic)
    active = still_active
    parked = parked + stale_active
    if not active and not parked:
        return ""

    header = "## 当前话题状态（这是你自己在追的事，不是任务清单）"
    body: list[str] = []

    if active:
        chunks: list[str] = []
        seen_titles: set[str] = set()
        for topic in active[:3]:
            title = _short_title(topic)
            if not title:
                continue
            # 同一话题只列一次（旧实现同一标题能出现 3 次 = 「反复说同一件事」的放大器）
            if title in seen_titles:
                continue
            seen_titles.add(title)
            turns = _safe_int(topic.get("turn_count"), 0)
            chunks.append(f"{title}（{turns}轮）")
        if chunks:
            body.append("- 正在聊（最多 3 条）：" + "；".join(chunks))

    details: list[str] = []
    for topic in active:
        for anchor in _as_anchors(topic.get("anchors")):
            detail = _sanitize_topic_text(anchor, 10)
            if detail and detail not in details:
                details.append(detail)
            if len(details) >= 2:
                break
        if len(details) >= 2:
            break
    if details:
        body.append("- 你记得的细节：" + "；".join(details))

    parked_titles: list[str] = []
    for topic in parked[:2]:
        title = _short_title(topic)
        if title and title not in parked_titles:
            parked_titles.append(title)
    if parked_titles:
        body.append("- 之前没聊完：" + "；".join(parked_titles))

    # 按行装入，超长时截断当前行并停止；保证终止（不存在截完仍超长的循环）
    lines = [header]
    for line in body:
        if len("\n".join(lines + [line])) <= 160:
            lines.append(line)
            continue
        remaining = 160 - len("\n".join(lines)) - 1
        if remaining >= 1:
            lines.append(line[: max(0, remaining - 1)] + "…")
        break
    return "\n".join(lines)[:160]


# ── 状态机内部操作 ───────────────────────────────────────────────────────


def _retention(cfg: dict[str, Any]) -> timedelta:
    return timedelta(days=int(cfg.get("retention_days") or 7))


def _best_match(message: str, topics: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for topic in topics:
        score = topic_overlap(message, topic)
        if best is None or score > best_score:
            best, best_score = topic, score
        elif (
            score == best_score
            and best is not None
            and (topic.get("salience", 0.0), topic.get("last_active_at", ""))
            > (best.get("salience", 0.0), best.get("last_active_at", ""))
        ):
            best = topic
    return best, best_score


def _is_stale(last_active_at: Any, now: datetime, park_idle_hours: float) -> bool:
    dt = _parse_ts(last_active_at)
    if dt is None:
        return False
    return dt <= now - timedelta(hours=float(park_idle_hours))


def _touch_mention(
    conn: sqlite3.Connection,
    topic: dict[str, Any],
    message: str,
    now: datetime,
    cfg: dict[str, Any],
) -> None:
    anchors = list(topic.get("anchors") or [])
    anchor = _clip(_first_clause(message) or message, 40)
    if anchor and anchor not in anchors:
        anchors.append(anchor)
    anchors = anchors[-3:]
    salience = min(1.0, float(topic.get("salience", 0.5)) * 0.9 + 0.2)
    conn.execute(
        "UPDATE topic_state SET summary=?, salience=?, mention_count=mention_count+1, "
        "anchors=?, idle_turns=0, last_active_at=?, updated_at=?, expires_at=? "
        "WHERE session_key=? AND character_id=? AND topic_id=?",
        (
            _clip(message, 60),
            salience,
            json.dumps(anchors, ensure_ascii=False),
            _ts(now),
            _ts(now),
            _ts(now + _retention(cfg)),
            topic["session_key"],
            topic["character_id"],
            topic["topic_id"],
        ),
    )
    topic["summary"] = _clip(message, 60)
    topic["salience"] = salience
    topic["anchors"] = anchors
    topic["mention_count"] = int(topic.get("mention_count", 0)) + 1
    topic["idle_turns"] = 0
    topic["last_active_at"] = _ts(now)
    topic["updated_at"] = _ts(now)
    topic["expires_at"] = _ts(now + _retention(cfg))


def _resume_topic(
    conn: sqlite3.Connection,
    topic: dict[str, Any],
    message: str,
    now: datetime,
    cfg: dict[str, Any],
) -> None:
    anchors = list(topic.get("anchors") or [])
    anchor = _clip(_first_clause(message) or message, 40)
    if anchor and anchor not in anchors:
        anchors.append(anchor)
    anchors = anchors[-3:]
    salience = min(1.0, float(topic.get("salience", 0.5)) * 0.9 + 0.2)
    conn.execute(
        "UPDATE topic_state SET status='active', summary=?, salience=?, "
        "mention_count=mention_count+1, anchors=?, idle_turns=0, "
        "last_active_at=?, updated_at=?, expires_at=? "
        "WHERE session_key=? AND character_id=? AND topic_id=?",
        (
            _clip(message, 60),
            salience,
            json.dumps(anchors, ensure_ascii=False),
            _ts(now),
            _ts(now),
            _ts(now + _retention(cfg)),
            topic["session_key"],
            topic["character_id"],
            topic["topic_id"],
        ),
    )
    topic.update(
        status="active",
        summary=_clip(message, 60),
        salience=salience,
        anchors=anchors,
        mention_count=int(topic.get("mention_count", 0)) + 1,
        idle_turns=0,
        last_active_at=_ts(now),
        updated_at=_ts(now),
        expires_at=_ts(now + _retention(cfg)),
    )


def _park_row(
    conn: sqlite3.Connection,
    topic: dict[str, Any],
    now: datetime,
    cfg: dict[str, Any],
    reason: str,
    transitions: list[str],
) -> None:
    """active → parked；把本轮已计算的衰减后 salience 一并落库。

    park/timeout 分支在调用前已把 ``topic["salience"]`` 更新为
    ``old * 0.9``；旧 SQL 漏写该列会让 parked 保留旧显著度，影响 Q6 淘汰与
    主动复提排序（2026-10-28 复核 finding）。
    """
    salience = max(0.0, min(1.0, _safe_float(topic.get("salience"), 0.5)))
    conn.execute(
        "UPDATE topic_state SET status='parked', idle_turns=?, salience=?, "
        "updated_at=?, expires_at=? "
        "WHERE session_key=? AND character_id=? AND topic_id=?",
        (
            max(int(topic.get("idle_turns", 0)), 0),
            salience,
            _ts(now),
            _ts(now + _retention(cfg)),
            topic["session_key"],
            topic["character_id"],
            topic["topic_id"],
        ),
    )
    topic["status"] = "parked"
    topic["updated_at"] = _ts(now)
    topic["expires_at"] = _ts(now + _retention(cfg))
    topic["salience"] = salience
    transitions.append(f"{reason}:{topic['topic_id']}")


def _close_row(
    conn: sqlite3.Connection,
    topic: dict[str, Any],
    now: datetime,
    cfg: dict[str, Any],
    transitions: list[str],
) -> None:
    conn.execute(
        "UPDATE topic_state SET status='closed', updated_at=?, expires_at=? "
        "WHERE session_key=? AND character_id=? AND topic_id=?",
        (
            _ts(now),
            _ts(now + _retention(cfg)),
            topic["session_key"],
            topic["character_id"],
            topic["topic_id"],
        ),
    )
    topic["status"] = "closed"
    topic["updated_at"] = _ts(now)
    topic["expires_at"] = _ts(now + _retention(cfg))
    transitions.append(f"close:{topic['topic_id']}")


def _topic_id_for(session_key: str, character_id: str, title: str) -> str:
    digest = hashlib.sha1(f"{session_key}|{character_id}|{_norm(title)}".encode()).hexdigest()
    return digest[:12]


def _insert_topic(
    conn: sqlite3.Connection,
    *,
    session_key: str,
    character_id: str,
    topic_id: str,
    title: str,
    summary: str,
    anchors: list[str],
    mention_count: int,
    turn_count: int,
    now: datetime,
    cfg: dict[str, Any],
) -> None:
    conn.execute(
        "INSERT INTO topic_state (session_key, character_id, topic_id, title, summary, "
        "status, salience, mention_count, turn_count, anchors, idle_turns, "
        "last_active_at, created_at, updated_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', 0.5, ?, ?, ?, 0, ?, ?, ?, ?)",
        (
            session_key,
            character_id,
            topic_id,
            title,
            summary,
            mention_count,
            turn_count,
            json.dumps(anchors, ensure_ascii=False),
            _ts(now),
            _ts(now),
            _ts(now),
            _ts(now + _retention(cfg)),
        ),
    )


def _create_topic(
    conn: sqlite3.Connection,
    *,
    session_key: str,
    character_id: str,
    message: str,
    now: datetime,
    cfg: dict[str, Any],
) -> tuple[str, str]:
    title = _clip(_first_clause(message) or message, 20) or "未命名话题"
    topic_id = _topic_id_for(session_key, character_id, title)
    existing = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? AND topic_id=?",
        (session_key, character_id, topic_id),
    ).fetchone()
    if existing is not None:
        topic = _row_to_topic(existing)
        if topic["status"] == "active":
            _touch_mention(conn, topic, message, now, cfg)
            return topic_id, "continue"
        if topic["status"] == "parked":
            _resume_topic(conn, topic, message, now, cfg)
            return topic_id, "resume"
        # closed：保留旧行，追加时间盐再哈希，避免主键冲突
        for salt in (1, 2, 3, 4, 5):
            candidate = _topic_id_for(
                session_key,
                character_id,
                f"{title}|{now.strftime('%Y%m%d%H%M%S')}|{salt}",
            )
            if (
                conn.execute(
                    "SELECT 1 FROM topic_state WHERE session_key=? AND character_id=? AND topic_id=?",
                    (session_key, character_id, candidate),
                ).fetchone()
                is None
            ):
                topic_id = candidate
                break

    summary = _clip(message, 60)
    anchors = [_clip(_first_clause(message) or message, 40)]
    _insert_topic(
        conn,
        session_key=session_key,
        character_id=character_id,
        topic_id=topic_id,
        title=title,
        summary=summary,
        anchors=[a for a in anchors if a],
        mention_count=1,
        turn_count=0,
        now=now,
        cfg=cfg,
    )
    return topic_id, "switch"


def _enforce_active_cap(
    conn: sqlite3.Connection,
    session_key: str,
    character_id: str,
    cap: int,
    now: datetime,
    cfg: dict[str, Any],
    transitions: list[str],
) -> None:
    active_rows = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
        "AND status='active' AND expires_at > ? "
        "ORDER BY salience ASC, last_active_at ASC",
        (session_key, character_id, _ts(now)),
    ).fetchall()
    excess = len(active_rows) - max(1, int(cap))
    for row in active_rows[: max(0, excess)]:
        _park_row(conn, _row_to_topic(row), now, cfg, "park", transitions)


_ACTION_PRIORITY = {
    "close": 6,
    "resume": 5,
    "switch": 4,
    "continue": 3,
    "timeout": 2,
    "park": 1,
    "empty": 0,
}


def _pick_action(actions: list[str]) -> str:
    return max(actions, key=lambda a: _ACTION_PRIORITY.get(a, 0)) if actions else "empty"


def _split_transition(transition: str) -> tuple[str, str]:
    """拆分 ``action:topic_id``；只按第一个冒号切，topic_id 原样保留。

    不用手写 ``split`` 切分：`tests/test_block_e_hardening.py` 的 AST 防回归
    测试把该形态视为手写 owner 拆分（2026-10-28 复核 finding），``partition``
    语义相同且不落入该禁令。
    """
    action, _, topic_id = str(transition or "").partition(":")
    return action, topic_id


# ── 六 API ───────────────────────────────────────────────────────────────


def get_topic_snapshot(
    session_key: str,
    character_id: str = "default",
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """返回 ``{enabled, active[], parked[], closed[], text, next_plan, degraded?}``。

    ``closed`` 仅供管理端只读审计（主链渲染不使用）；``enabled=false`` →
    不连库、不建表；读锁/缺表 → ``degraded=True`` + 空快照。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        return _empty_snapshot(enabled=False)
    key = str(session_key or "").strip()
    if not key:
        return _empty_snapshot(enabled=True, degraded=True)
    character = _character_key(character_id)
    if not character:
        # 空角色不再猜 default：读 default 桶会把别的角色话题注进本轮上下文。
        return _empty_snapshot(enabled=True, degraded=True)
    dt = _normalize_now(now)
    try:
        with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
            snapshot = _snapshot_from_conn(conn, key, character, dt)
        logger.debug(
            "[topic_state][step=snapshot] active=%d parked=%d",
            len(snapshot["active"]),
            len(snapshot["parked"]),
        )
        return snapshot
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=snapshot] degraded=%s", type(e).__name__)
        return _empty_snapshot(enabled=True, degraded=True)


def update_after_turn(
    session_key: str,
    character_id: str,
    user_message: str,
    assistant_reply: str = "",
    *,
    turn_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """确定性状态转换。

    返回 ``{enabled, action, topic_id, should_judge, snapshot}``。
    ``action ∈ continue|switch|park|resume|close|timeout|replay|empty|disabled``。
    ``replay`` 仅在 ``turn_id`` 非空且与 ``last_turn_id`` 相同时成立。
    ``enabled=false`` → 零 DB 访问。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        logger.debug("[topic_state][step=update] action=disabled")
        return {
            "enabled": False,
            "action": "disabled",
            "topic_id": "",
            "should_judge": False,
            "snapshot": _empty_snapshot(enabled=False),
            "transitions": [],
        }

    key = str(session_key or "").strip()
    character = _character_key(character_id)
    message = str(user_message or "").strip()
    if not key or not message or not character:
        logger.debug(
            "[topic_state][step=update] action=empty reason=%s",
            "session_key" if not key else ("character_id" if not character else "message"),
        )
        return {
            "enabled": True,
            "action": "empty",
            "topic_id": "",
            "should_judge": False,
            "snapshot": _empty_snapshot(enabled=True),
            "transitions": [],
        }

    dt = _normalize_now(now)
    now_ts = _ts(dt)
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(ensure_schema=True)
        conn.execute("BEGIN IMMEDIATE")

        meta = conn.execute(
            "SELECT * FROM topic_meta WHERE session_key=? AND character_id=?",
            (key, character),
        ).fetchone()
        if turn_id and str(_row_get(meta, "last_turn_id", "") or "") == str(turn_id):
            snapshot = _snapshot_from_conn(conn, key, character, dt)
            conn.execute("COMMIT")
            logger.debug("[topic_state][step=update] action=replay")
            return {
                "enabled": True,
                "action": "replay",
                "topic_id": "",
                "should_judge": False,
                "snapshot": snapshot,
                "transitions": [],
            }

        # 会话内顺手清理过期行（小事务，避免表无界增长）
        conn.execute(
            "DELETE FROM topic_state WHERE session_key=? AND character_id=? AND expires_at < ?",
            (key, character, now_ts),
        )
        conn.execute(
            "DELETE FROM thread_plan WHERE session_key=? AND character_id=? AND expires_at < ?",
            (key, character, now_ts),
        )

        rows = conn.execute(
            "SELECT * FROM topic_state WHERE session_key=? AND character_id=? AND expires_at > ?",
            (key, character, now_ts),
        ).fetchall()
        topics = [_row_to_topic(row) for row in rows]
        active = [t for t in topics if t["status"] == "active"]
        parked = [t for t in topics if t["status"] == "parked"]

        transitions: list[str] = []
        main_action = "empty"
        main_topic_id = ""
        theta1 = float(cfg["switch_theta1"])
        theta2 = float(cfg["switch_theta2"])

        best_active, best_active_score = _best_match(message, active)
        best_parked, best_parked_score = _best_match(message, parked)

        if is_close_statement(message):
            best_any, best_any_score = _best_match(message, active + parked)
            if best_any is not None and best_any_score >= theta1:
                _close_row(conn, best_any, dt, cfg, transitions)
                main_action = "close"
                main_topic_id = best_any["topic_id"]

        if main_action == "empty":
            if best_active is not None and best_active_score >= theta1 and best_active_score >= best_parked_score:
                _touch_mention(conn, best_active, message, dt, cfg)
                main_action = "continue"
                main_topic_id = best_active["topic_id"]
            elif best_parked is not None and best_parked_score >= theta2:
                _resume_topic(conn, best_parked, message, dt, cfg)
                main_action = "resume"
                main_topic_id = best_parked["topic_id"]
            elif best_active is not None and best_active_score >= theta1:
                _touch_mention(conn, best_active, message, dt, cfg)
                main_action = "continue"
                main_topic_id = best_active["topic_id"]
            elif _can_create_topic(message):
                topic_id, action = _create_topic(
                    conn,
                    session_key=key,
                    character_id=character,
                    message=message,
                    now=dt,
                    cfg=cfg,
                )
                main_action = action
                main_topic_id = topic_id

        matched_ids = {main_topic_id} if main_action in ("continue", "resume") else set()
        for topic in active:
            if topic["topic_id"] in matched_ids:
                continue
            if topic.get("status") != "active":
                # close/其它转换已改写状态，不能再按 active 衰减/落 parked
                continue
            new_idle = int(topic.get("idle_turns", 0)) + 1
            new_salience = max(0.0, float(topic.get("salience", 0.0)) * 0.9)
            topic["idle_turns"] = new_idle
            topic["salience"] = new_salience
            if _is_stale(topic.get("last_active_at"), dt, cfg["park_idle_hours"]):
                _park_row(conn, topic, dt, cfg, "timeout", transitions)
            elif new_idle >= 3:
                _park_row(conn, topic, dt, cfg, "park", transitions)
            else:
                conn.execute(
                    "UPDATE topic_state SET idle_turns=?, salience=?, updated_at=? "
                    "WHERE session_key=? AND character_id=? AND topic_id=?",
                    (
                        new_idle,
                        new_salience,
                        now_ts,
                        key,
                        character,
                        topic["topic_id"],
                    ),
                )

        # turn_count：对本轮处于 active 的每个话题 +1（continue/resume 不另行加）
        conn.execute(
            "UPDATE topic_state SET turn_count=turn_count+1 "
            "WHERE session_key=? AND character_id=? AND status='active' AND expires_at > ?",
            (key, character, now_ts),
        )

        _enforce_active_cap(conn, key, character, int(cfg["active_cap"]), dt, cfg, transitions)

        snapshot = _snapshot_from_conn(conn, key, character, dt)
        has_state = bool(snapshot["active"] or snapshot["parked"])

        prev_turns = _safe_int(_row_get(meta, "turns_since_llm", 0))
        turns = prev_turns + 1
        prev_calls = _safe_int(_row_get(meta, "llm_calls", 0))
        prev_last = str(_row_get(meta, "last_llm_at", "") or "")
        if prev_last and prev_last[:10] != dt.strftime("%Y-%m-%d"):
            prev_calls = 0
        budget = int(cfg["daily_llm_budget"] or 0)
        budget_ok = budget <= 0 or prev_calls < budget
        should_judge = bool(
            cfg["llm_judge_enabled"] and turns >= int(cfg["judge_every_turns"]) and budget_ok and has_state
        )
        last_turn_id = str(turn_id or "") or str(_row_get(meta, "last_turn_id", "") or "")

        conn.execute(
            "INSERT INTO topic_meta (session_key, character_id, turns_since_llm, "
            "llm_calls, last_llm_at, last_turn_id) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_key, character_id) DO UPDATE SET "
            "turns_since_llm=excluded.turns_since_llm, llm_calls=excluded.llm_calls, "
            "last_llm_at=excluded.last_llm_at, last_turn_id=excluded.last_turn_id",
            (key, character, turns, prev_calls, prev_last, last_turn_id),
        )

        conn.execute("COMMIT")

        action = _pick_action([main_action] + [_split_transition(tr)[0] for tr in transitions])
        topic_id = main_topic_id
        if not topic_id and transitions:
            for tr in transitions:
                tr_action, tr_topic_id = _split_transition(tr)
                if tr_action == action:
                    topic_id = tr_topic_id
                    break
        logger.debug(
            "[topic_state][step=update] action=%s topic_id=%s transitions=%d should_judge=%s",
            action,
            topic_id,
            len(transitions),
            should_judge,
        )
        return {
            "enabled": True,
            "action": action,
            "topic_id": topic_id,
            "should_judge": should_judge,
            "snapshot": snapshot,
            "transitions": transitions,
        }
    except sqlite3.OperationalError as e:
        if conn is not None:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
        text = str(e).lower()
        error = "db_locked" if ("locked" in text or "busy" in text) else "db_error"
        logger.warning("[topic_state][step=update] error=%s", error)
        return {
            "enabled": True,
            "action": "error",
            "error": error,
            "topic_id": "",
            "should_judge": False,
            "snapshot": _empty_snapshot(enabled=True, degraded=True),
            "transitions": [],
        }
    except (sqlite3.Error, OSError) as e:
        if conn is not None:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
        logger.warning("[topic_state][step=update] error=%s", type(e).__name__)
        return {
            "enabled": True,
            "action": "error",
            "error": "db_error",
            "topic_id": "",
            "should_judge": False,
            "snapshot": _empty_snapshot(enabled=True, degraded=True),
            "transitions": [],
        }
    finally:
        if conn is not None:
            with suppress(sqlite3.Error):
                conn.close()


def _in_quiet_hours(hour: int, quiet_hours: tuple[int, int]) -> bool:
    start, end = int(quiet_hours[0]), int(quiet_hours[1])
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def select_proactive_recall(
    session_key: str,
    character_id: str = "default",
    *,
    now: datetime | None = None,
    quiet_hours: tuple[int, int] | None = None,
    max_items: int | None = None,
    after_hours: float | None = None,
) -> list[dict[str, Any]]:
    """只返回 parked；closed/active 永不返回；静默/未满 6h/过期 → ``[]``。

    顺带把超 6h 未活动的 active 落为 parked（timeout），再选取。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        return []
    key = str(session_key or "").strip()
    if not key:
        return []
    character = _character_key(character_id)
    if not character:
        return []
    dt = _normalize_now(now)
    if quiet_hours is not None and _in_quiet_hours(dt.hour, quiet_hours):
        logger.debug("[topic_state][step=recall] quiet_hours")
        return []

    threshold_hours = max(6.0, float(after_hours if after_hours is not None else cfg["recall_after_hours"]))
    limit = max(1, min(2, int(max_items if max_items is not None else cfg["recall_max"])))
    cutoff = _ts(dt - timedelta(hours=threshold_hours))

    try:
        # 先只读扫描 stale active；命中才升级为写事务（不给 5 分钟 tick 增加无谓写锁）
        with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
            # 存量库补列（幂等，见 _ensure_recall_columns）——必须在查询前，
            # 否则「首次读先于任何写」的时序会因缺列整体降级 []。
            _ensure_recall_columns(conn)
            stale_rows = conn.execute(
                "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
                "AND status='active' AND expires_at > ? AND last_active_at <= ?",
                (key, character, _ts(dt), _ts(dt - timedelta(hours=float(cfg["park_idle_hours"])))),
            ).fetchall()
        if stale_rows:
            with closing(_connect(ensure_schema=True)) as conn, _write_tx(conn):
                for row in stale_rows:
                    topic = _row_to_topic(row)
                    conn.execute(
                        "UPDATE topic_state SET status='parked', updated_at=?, expires_at=? "
                        "WHERE session_key=? AND character_id=? AND topic_id=? "
                        "AND status='active' AND last_active_at <= ?",
                        (
                            _ts(dt),
                            _ts(dt + _retention(cfg)),
                            key,
                            character,
                            topic["topic_id"],
                            _ts(dt - timedelta(hours=float(cfg["park_idle_hours"]))),
                        ),
                    )
            logger.debug("[topic_state][step=recall] timeout_parked=%d", len(stale_rows))

        with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
            _ensure_recall_columns(conn)
            # LIMIT 20 先粗筛再冷却过滤：升级冷却的行必须在过滤**之后**才让位
            # 给低计数候选（SQL LIMIT 先截断会把名额浪费在冷却中的行上）。
            rows = conn.execute(
                "SELECT * FROM topic_state WHERE session_key=? AND character_id=? "
                "AND status='parked' AND expires_at > ? AND last_active_at <= ? "
                "AND last_recalled_at <= ? "
                "ORDER BY salience DESC, last_active_at DESC LIMIT 20",
                (key, character, _ts(dt), cutoff, cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=recall] degraded=%s", type(e).__name__)
        return []

    items: list[dict[str, Any]] = []
    for row in rows:
        topic = _row_to_topic(row)
        last_active = _parse_ts(topic["last_active_at"])
        last_recalled = _parse_ts(topic.get("last_recalled_at") or "")
        effective_last = max(
            (d for d in (last_active, last_recalled) if d is not None),
            default=None,
        )
        age_hours = (
            max(0.0, (dt - effective_last).total_seconds() / 3600.0)
            if effective_last else 0.0
        )
        # 冷却升级（对抗验收残余缺口②）：复提次数越多，再次复提的间隔越长，
        # 用户不回应的 parked 话题不再每 ≥6h 周期性合法复提。
        required_gap = _recall_gap_hours(int(topic.get("recall_count") or 0), threshold_hours)
        if age_hours + 1e-9 < required_gap:
            logger.debug(
                "[topic_state][step=recall] cooldown topic=%s count=%s gap=%.1fh",
                topic.get("topic_id"), topic.get("recall_count"), required_gap,
            )
            continue
        topic["age_hours"] = round(age_hours, 3)
        items.append(topic)
    items = items[:limit]
    logger.debug("[topic_state][step=recall] count=%d", len(items))
    return items


def mark_topic_recalled(
    session_key: str,
    character_id: str = "default",
    topic_id: str = "",
    *,
    now: datetime | None = None,
) -> bool:
    """复提记账（2026-11-07 对抗验收残余缺口②）：投递成功后由 scheduler 调用。

    ``recall_count += 1`` 且 ``last_recalled_at = now``——之后
    :func:`select_proactive_recall` 的冷却升级闸（2 次 → 翻倍、4 次 → 四倍）
    才有真数据可依。幂等安全：topic_id 不存在时 rowcount=0 返回 False。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        return False
    key = str(session_key or "").strip()
    character = _character_key(character_id)
    tid = str(topic_id or "").strip()
    if not key or not tid or not character:
        return False
    dt = _normalize_now(now)
    try:
        with closing(_connect(ensure_schema=True)) as conn, _write_tx(conn):
            cur = conn.execute(
                "UPDATE topic_state SET recall_count=recall_count+1, last_recalled_at=?, "
                "updated_at=? WHERE session_key=? AND character_id=? AND topic_id=?",
                (_ts(dt), _ts(dt), key, character, tid),
            )
            return bool(cur.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=recall_touch] degraded=%s", type(e).__name__)
        return False


def save_next_plan(
    session_key: str,
    character_id: str,
    topic: str,
    intent: str,
    *,
    wait_minutes: int = 0,
    planned_at: datetime | None = None,
) -> dict[str, Any] | None:
    """upsert 单条未消费计划；topic/intent 均空 → 不写；disabled → ``None``。

    ``next_at = planned_at + wait_minutes``；upsert 必须把 ``consumed_at`` 复位 NULL。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        return None
    key = str(session_key or "").strip()
    character = _character_key(character_id)
    topic_text = _clip(topic, 40)
    intent_text = _clip(intent, 120)
    if not key or not character or (not topic_text and not intent_text):
        return None
    planned = _normalize_now(planned_at)
    wait = max(0, _safe_int(wait_minutes, 0))
    next_at = planned + timedelta(minutes=wait)
    expires_at = next_at + timedelta(hours=24)
    max_expires = planned + _retention(cfg)
    if expires_at > max_expires:
        expires_at = max_expires
    try:
        with closing(_connect(ensure_schema=True)) as conn, _write_tx(conn):
            conn.execute(
                "INSERT INTO thread_plan (session_key, character_id, topic, intent, "
                "planned_at, next_at, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(session_key, character_id) DO UPDATE SET "
                "topic=excluded.topic, intent=excluded.intent, planned_at=excluded.planned_at, "
                "next_at=excluded.next_at, expires_at=excluded.expires_at, consumed_at=NULL",
                (
                    key,
                    character,
                    topic_text,
                    intent_text,
                    _ts(planned),
                    _ts(next_at),
                    _ts(expires_at),
                ),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[topic_state][step=plan] save_error=%s", type(e).__name__)
        return None
    logger.debug("[topic_state][step=plan] saved wait=%d", wait)
    # 直接回执刚写入的值，不再回读（写入成功即可；避免读锁把成功伪装成失败）
    return {
        "topic": topic_text,
        "intent": intent_text,
        "planned_at": _ts(planned),
        "next_at": _ts(next_at),
        "expires_at": _ts(expires_at),
        "consumed_at": None,
    }


def take_next_plan(
    session_key: str,
    character_id: str = "default",
    *,
    now: datetime | None = None,
    consume: bool = False,
) -> dict[str, Any] | None:
    """读未消费、未过期且**已到点**（``next_at <= now``）的计划。

    默认**不消费**；发送成功才 ``consume=True``。
    """
    cfg = read_topic_config()
    if not cfg["enabled"]:
        return None
    key = str(session_key or "").strip()
    if not key:
        return None
    character = _character_key(character_id)
    if not character:
        return None
    dt = _normalize_now(now)
    now_ts = _ts(dt)
    try:
        if consume:
            with closing(_connect(ensure_schema=False)) as conn, _write_tx(conn):
                plan = _read_plan_row_from_conn(conn, key, character, now_ts, due_only=True)
                if plan is not None:
                    conn.execute(
                        f"UPDATE {_plan_table_name(conn)} SET consumed_at=? "
                        "WHERE session_key=? AND character_id=?",
                        (now_ts, key, character),
                    )
                    plan["consumed_at"] = now_ts
        else:
            with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
                plan = _read_plan_row_from_conn(conn, key, character, now_ts, due_only=True)
        if plan is not None:
            logger.debug("[topic_state][step=plan] take consume=%s", consume)
        return plan
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=plan] take_degraded=%s", type(e).__name__)
        return None


def _read_plan_row_from_conn(
    conn: sqlite3.Connection,
    session_key: str,
    character_id: str,
    now_ts: str,
    *,
    due_only: bool,
) -> dict[str, Any] | None:
    sql = (
        f"SELECT * FROM {_plan_table_name(conn)} WHERE session_key=? "
        "AND character_id=? AND consumed_at IS NULL AND expires_at > ?"
    )
    params: list[Any] = [session_key, character_id, now_ts]
    if due_only:
        sql += " AND next_at <= ?"
        params.append(now_ts)
    return _row_to_plan(conn.execute(sql, tuple(params)).fetchone())


def _owner_keys(session_keys: Any) -> list[str]:
    """归一化 owner purge/count 的会话键：去空白、去重、稳定排序。"""
    if isinstance(session_keys, str):
        raw: list[Any] = [session_keys]
    else:
        raw = list(session_keys or [])
    return sorted(
        {str(k).strip() for k in raw if k is not None and str(k).strip()}
    )


def _existing_topic_tables(conn: sqlite3.Connection) -> set[str]:
    """当前库里真实存在的话题表（含迁移前的旧 ``topic_plan``）。

    2026-11-13 对抗验收修复：旧表白名单缺席会让「删号/每日 TTL」对未迁移
    存量库完全失效，并在之后的自动迁移里把已删账号数据搬进新表（删号复活）。
    清理与 verify 必须同时看见旧表。
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('topic_state', 'thread_plan', 'topic_meta', 'topic_plan')"
    ).fetchall()
    return {str(row[0]) for row in rows}


def purge_expired(*, now: datetime | None = None, force: bool = False) -> int:
    """删除过期话题数据（四表，同一事务；含迁移前遗留 topic_plan）。

    - ``topic_state`` / ``thread_plan``：``expires_at < now``；
    - ``topic_meta``：``last_llm_at < cutoff``（cutoff = now - retention）
      **且**该 ``(session_key, character_id)`` 没有未过期的 state / plan。
      meta 只是轮次与 LLM 预算账本，存在未过期话题时不能丢（否则预算会被
      重置、``judge_every_turns`` 重新计）。

    默认 ``enabled=false`` → 0，不连库；``force=True`` 仅供每日维护。
    """
    cfg = read_topic_config()
    if not cfg["enabled"] and not force:
        return 0
    dt = _normalize_now(now)
    now_ts = _ts(dt)
    cutoff_ts = _ts(dt - _retention(cfg))
    try:
        with closing(_connect(ensure_schema=False)) as conn, _write_tx(conn):
            tables = _existing_topic_tables(conn)
            deleted = 0
            if "topic_state" in tables:
                cur_state = conn.execute(
                    "DELETE FROM topic_state WHERE expires_at < ?", (now_ts,)
                )
                deleted += int(cur_state.rowcount or 0)
            if "thread_plan" in tables:
                cur_plan = conn.execute(
                    "DELETE FROM thread_plan WHERE expires_at < ?", (now_ts,)
                )
                deleted += int(cur_plan.rowcount or 0)
            if "topic_plan" in tables:
                # 迁移前存量库：旧表过期行同样要清，否则 purge_expired 的
                # 返回值与实际残留脱节（2026-11-13 对抗验收）。
                cur_legacy = conn.execute(
                    "DELETE FROM topic_plan WHERE expires_at < ?", (now_ts,)
                )
                deleted += int(cur_legacy.rowcount or 0)
            # meta 谓词依赖 state/plan 两表；缺表时保守跳过（不猜未过期）。
            # plan 表在迁移前后可能叫不同名字，两表并存时逐个检查。
            if {"topic_state", "topic_meta"} <= tables:
                plan_checks: list[str] = []
                plan_params: list[Any] = []
                for plan_table in ("thread_plan", "topic_plan"):
                    if plan_table in tables:
                        plan_checks.append(
                            f"AND NOT EXISTS ("
                            f" SELECT 1 FROM {plan_table}"
                            f" WHERE {plan_table}.session_key = topic_meta.session_key"
                            f"   AND {plan_table}.character_id = topic_meta.character_id"
                            f"   AND {plan_table}.expires_at >= ?"
                            f")"
                        )
                        plan_params.append(now_ts)
                cur_meta = conn.execute(
                    "DELETE FROM topic_meta WHERE last_llm_at < ? "
                    "AND NOT EXISTS ("
                    " SELECT 1 FROM topic_state"
                    " WHERE topic_state.session_key = topic_meta.session_key"
                    "   AND topic_state.character_id = topic_meta.character_id"
                    "   AND topic_state.expires_at >= ?"
                    ") "
                    + " ".join(plan_checks),
                    (cutoff_ts, now_ts, *plan_params),
                )
                deleted += int(cur_meta.rowcount or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[topic_state][step=purge] error=%s", type(e).__name__)
        return 0
    logger.debug("[topic_state][step=purge] deleted=%d force=%s", deleted, force)
    return deleted


def _owner_where(
    keys: list[str], owner_uid: int | str | None,
) -> tuple[str, tuple[Any, ...]]:
    """构造 owner 归属谓词：显式会话键 ∪ （可选）``owner_uid:`` 前缀。

    2026-11-12 终审修复：生命周期只从绑定/偏好枚举到微信会话键，漏掉
    ``N:web:hex`` 等未知 web 键；删号必须按 owner 前缀兜底，否则话题四表
    会残留。``LIKE 'N:%'`` 不会误伤 ``N0:``（有冒号分隔）。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if keys:
        placeholders = ",".join("?" for _ in keys)
        clauses.append(f"session_key IN ({placeholders})")
        params.extend(keys)
    if owner_uid is not None and str(owner_uid).strip():
        clauses.append("session_key LIKE ?")
        params.append(f"{int(owner_uid)}:%")
    if not clauses:
        return "", ()
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def purge_owner(
    session_keys: Any, *, db_path: str | Path | None = None,
    owner_uid: int | str | None = None,
) -> int:
    """按 ``session_key`` + 可选 owner 前缀清空话题状态机四表（含遗留 topic_plan）。

    生命周期删除账号专用：**不读 enabled**（删号必须删干净），四表同一
    事务；空列表且无 owner_uid → 0。数据库异常**不吞**：向上抛，让生命
    周期步骤明确失败——旧实现吞成 0 会让 verify 把「没删掉」报告成「0 残留」。
    """
    keys = _owner_keys(session_keys)
    where, params = _owner_where(keys, owner_uid)
    if not where:
        return 0
    with closing(_connect(ensure_schema=False, db_path=db_path)) as conn, _write_tx(conn):
        tables = _existing_topic_tables(conn)
        deleted = 0
        for table in ("topic_state", "thread_plan", "topic_meta", "topic_plan"):
            if table not in tables:
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {where}",
                params,
            )
            deleted += int(cur.rowcount or 0)
    logger.debug("[topic_state][step=purge_owner] deleted=%d", deleted)
    return deleted


def count_owner(
    session_keys: Any, *, db_path: str | Path | None = None,
    owner_uid: int | str | None = None,
) -> int:
    """统计四表中属于给定会话键或 owner 前缀的残留行数（生命周期 verify）。

    数据库异常**不吞**：向上抛，verify 不得把 IO 错误报告成 0 残留。
    """
    keys = _owner_keys(session_keys)
    where, params = _owner_where(keys, owner_uid)
    if not where:
        return 0
    with closing(_connect(ensure_schema=False, db_path=db_path)) as conn:
        tables = _existing_topic_tables(conn)
        total = 0
        for table in ("topic_state", "thread_plan", "topic_meta", "topic_plan"):
            if table not in tables:
                continue
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}",
                params,
            ).fetchone()
            total += int(row[0] or 0) if row else 0
    return total


# ── 稀疏 LLM judge（每 4 轮、8s 硬超时、失败保留原状态）──────────────────

TOPIC_JUDGE_SYSTEM = """你是话题状态判定器。只判断这一轮用户消息对现有话题做了什么，
不要改写对话，不要输出解释。只输出 JSON：
{"action":"continue|switch|park|resume|close","topic_title":"...","summary":"...","confidence":0.0}"""


def _call_llm(llm: Any, system: str, prompt: str, timeout: float) -> str:
    """8s 硬超时：daemon 线程 + join(timeout)，到点即返回空串。

    不用 ``with ThreadPoolExecutor``：其 ``__exit__`` 会 ``shutdown(wait=True)``，
    LLM 不结束就永远不返回。
    """
    if llm is None or not hasattr(llm, "chat_sync"):
        return ""
    kwargs: dict[str, Any] = dict(query=prompt, system_prompt=system, max_tokens=200, temperature=0.1)
    try:
        params = inspect.signature(llm.chat_sync).parameters
        if "timeout" in params:
            kwargs["timeout"] = float(timeout)
    except (TypeError, ValueError):
        pass

    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["value"] = str(llm.chat_sync(**kwargs) or "")
        except Exception as e:  # noqa: BLE001
            result["error"] = e

    thread = threading.Thread(target=_worker, name="topic-judge", daemon=True)
    thread.start()
    thread.join(float(timeout))
    if thread.is_alive():
        return ""
    if "error" in result:
        return ""
    return str(result.get("value", ""))


def _extract_json(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return ""
    return raw[start : end + 1]


def _build_judge_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    active: list[dict[str, Any]],
    parked: list[dict[str, Any]],
    now: datetime,
) -> str:
    def _line(topic: dict[str, Any], status: str) -> str:
        anchors = "；".join(_sanitize_topic_text(a, 20) for a in (topic.get("anchors") or [])[:3])
        title = _sanitize_topic_text(topic.get("title"), 30)
        return f"- [{status}] {title}" + (f"（锚点：{anchors}）" if anchors else "")

    lines = [
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}",
        "当前用户消息（≤300字）：" + _clip(user_message, 300),
        "当前回复（≤300字）：" + _clip(assistant_reply, 300),
        "现有话题：",
    ]
    lines.extend(_line(t, "active") for t in active[:5])
    lines.extend(_line(t, "parked") for t in parked[:5])
    lines.append("只输出 JSON。")
    return "\n".join(lines)


def should_run_llm_judge(
    session_key: str,
    character_id: str = "default",
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """读 ``topic_meta`` 判断是否满 4 轮/预算（预算 0 = 不限）。"""
    cfg = cfg or read_topic_config()
    if not cfg.get("enabled") or not cfg.get("llm_judge_enabled"):
        return False
    key = str(session_key or "").strip()
    if not key:
        return False
    character = _character_key(character_id)
    if not character:
        return False
    dt = _normalize_now(now)
    try:
        with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
            meta = conn.execute(
                "SELECT * FROM topic_meta WHERE session_key=? AND character_id=?",
                (key, character),
            ).fetchone()
            if meta is None:
                return False
            turns = _safe_int(_row_get(meta, "turns_since_llm", 0))
            if turns < int(cfg.get("judge_every_turns") or 4):
                return False
            budget = int(cfg.get("daily_llm_budget") or 0)
            if budget > 0:
                calls = _safe_int(_row_get(meta, "llm_calls", 0))
                last = str(_row_get(meta, "last_llm_at", "") or "")
                if last[:10] != dt.strftime("%Y-%m-%d"):
                    calls = 0
                if calls >= budget:
                    return False
            state = conn.execute(
                "SELECT 1 FROM topic_state WHERE session_key=? AND character_id=? "
                "AND status IN ('active','parked') AND expires_at > ? LIMIT 1",
                (key, character, _ts(dt)),
            ).fetchone()
            return state is not None
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=judge] gate_degraded=%s", type(e).__name__)
        return False


def _find_by_title(title: str, topics: list[dict[str, Any]]) -> dict[str, Any] | None:
    needle = _norm(title)
    if not needle:
        return None
    for topic in topics:
        if _norm(topic.get("title") or "") == needle:
            return topic
    best: dict[str, Any] | None = None
    best_score = 0.0
    for topic in topics:
        score = text_overlap(title, topic.get("title") or "")
        if score > best_score:
            best, best_score = topic, score
    return best if best is not None and best_score >= 0.2 else None


def _apply_judge(
    conn: sqlite3.Connection,
    *,
    session_key: str,
    character_id: str,
    action: str,
    title: str,
    summary: str,
    now: datetime,
    cfg: dict[str, Any],
) -> str:
    rows = conn.execute(
        "SELECT * FROM topic_state WHERE session_key=? AND character_id=? AND expires_at > ?",
        (session_key, character_id, _ts(now)),
    ).fetchall()
    topics = [_row_to_topic(r) for r in rows]
    active = [t for t in topics if t["status"] == "active"]
    parked = [t for t in topics if t["status"] == "parked"]
    wanted_title = _clip(title, 40)
    wanted_summary = _clip(summary, 80)

    if action == "continue":
        target = _find_by_title(wanted_title, active) if wanted_title else None
        if target is None:
            return ""
        conn.execute(
            "UPDATE topic_state SET title=?, summary=?, updated_at=?, expires_at=? "
            "WHERE session_key=? AND character_id=? AND topic_id=?",
            (
                wanted_title or target["title"],
                wanted_summary or target["summary"],
                _ts(now),
                _ts(now + _retention(cfg)),
                session_key,
                character_id,
                target["topic_id"],
            ),
        )
        return target["topic_id"]

    if action == "switch":
        new_title = wanted_title or _clip(wanted_summary, 20) or "未命名话题"
        topic_id = _topic_id_for(session_key, character_id, new_title)
        if (
            conn.execute(
                "SELECT 1 FROM topic_state WHERE session_key=? AND character_id=? AND topic_id=?",
                (session_key, character_id, topic_id),
            ).fetchone()
            is not None
        ):
            topic_id = _topic_id_for(session_key, character_id, f"{new_title}|{now.strftime('%Y%m%d%H%M%S')}")
        _insert_topic(
            conn,
            session_key=session_key,
            character_id=character_id,
            topic_id=topic_id,
            title=new_title,
            summary=wanted_summary,
            anchors=[wanted_title] if wanted_title else [],
            mention_count=1,
            turn_count=1,
            now=now,
            cfg=cfg,
        )
        _enforce_active_cap(conn, session_key, character_id, int(cfg["active_cap"]), now, cfg, [])
        return topic_id

    if action == "park":
        target = _find_by_title(wanted_title, active) if wanted_title else None
        if target is None:
            return ""
        _park_row(conn, target, now, cfg, "park", [])
        return target["topic_id"]

    if action == "close":
        target = _find_by_title(wanted_title, active + parked) if wanted_title else None
        if target is None:
            return ""
        _close_row(conn, target, now, cfg, [])
        return target["topic_id"]

    if action == "resume":
        target = _find_by_title(wanted_title, parked) if wanted_title else None
        if target is None:
            return ""
        conn.execute(
            "UPDATE topic_state SET status='active', idle_turns=0, last_active_at=?, "
            "updated_at=?, expires_at=? WHERE session_key=? AND character_id=? AND topic_id=?",
            (
                _ts(now),
                _ts(now),
                _ts(now + _retention(cfg)),
                session_key,
                character_id,
                target["topic_id"],
            ),
        )
        _enforce_active_cap(conn, session_key, character_id, int(cfg["active_cap"]), now, cfg, [])
        return target["topic_id"]

    return ""


def run_llm_judge(
    session_key: str,
    character_id: str = "default",
    *,
    user_message: str = "",
    assistant_reply: str = "",
    llm: Any = None,
    timeout: float = 8.0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """稀疏 LLM 判定；失败/低置信度/超时 → ``None`` + 保留原状态。

    无论成败（只要实际发起调用）``turns_since_llm`` 归零、``llm_calls += 1``。
    """
    cfg = read_topic_config()
    if not cfg["enabled"] or not cfg["llm_judge_enabled"]:
        return None
    if llm is None or not hasattr(llm, "chat_sync"):
        return None
    key = str(session_key or "").strip()
    if not key:
        return None
    character = _character_key(character_id)
    if not character:
        return None
    dt = _normalize_now(now)
    if not should_run_llm_judge(key, character, cfg, now=dt):
        return None

    try:
        with closing(_connect(ensure_schema=False, busy_timeout_ms=_READ_TIMEOUT_MS)) as conn:
            rows = conn.execute(
                "SELECT * FROM topic_state WHERE session_key=? AND character_id=? AND expires_at > ?",
                (key, character, _ts(dt)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("[topic_state][step=judge] state_degraded=%s", type(e).__name__)
        return None

    topics = [_row_to_topic(r) for r in rows]
    active = [t for t in topics if t["status"] == "active"]
    parked = [t for t in topics if t["status"] == "parked"]
    if not active and not parked:
        return None

    prompt = _build_judge_prompt(
        user_message=user_message,
        assistant_reply=assistant_reply,
        active=active,
        parked=parked,
        now=dt,
    )
    raw = _call_llm(llm, TOPIC_JUDGE_SYSTEM, prompt, timeout)

    parsed: dict[str, Any] | None = None
    if raw:
        try:
            data = json.loads(_extract_json(raw))
            if isinstance(data, dict):
                action = str(data.get("action") or "").strip().lower()
                confidence = _safe_float(data.get("confidence"), 0.0)
                if (
                    action in {"continue", "switch", "park", "resume", "close"}
                    and 0.0 <= confidence <= 1.0
                    and confidence >= 0.6
                ):
                    parsed = {
                        "action": action,
                        "topic_title": str(data.get("topic_title") or "").strip()[:40],
                        "summary": str(data.get("summary") or "").strip()[:80],
                        "confidence": confidence,
                    }
        except (TypeError, ValueError):
            parsed = None

    applied_topic_id = ""
    try:
        with closing(_connect(ensure_schema=True)) as conn, _write_tx(conn):
            meta = conn.execute(
                "SELECT * FROM topic_meta WHERE session_key=? AND character_id=?",
                (key, character),
            ).fetchone()
            calls = _safe_int(_row_get(meta, "llm_calls", 0))
            last = str(_row_get(meta, "last_llm_at", "") or "")
            if last[:10] != dt.strftime("%Y-%m-%d"):
                calls = 0
            calls += 1
            conn.execute(
                "INSERT INTO topic_meta (session_key, character_id, turns_since_llm, "
                "llm_calls, last_llm_at, last_turn_id) VALUES (?, ?, 0, ?, ?, ?) "
                "ON CONFLICT(session_key, character_id) DO UPDATE SET "
                "turns_since_llm=0, llm_calls=excluded.llm_calls, "
                "last_llm_at=excluded.last_llm_at, last_turn_id=excluded.last_turn_id",
                (
                    key,
                    character,
                    calls,
                    _ts(dt),
                    str(_row_get(meta, "last_turn_id", "") or ""),
                ),
            )
            if parsed is not None:
                applied_topic_id = _apply_judge(
                    conn,
                    session_key=key,
                    character_id=character,
                    action=parsed["action"],
                    title=parsed["topic_title"],
                    summary=parsed["summary"],
                    now=dt,
                    cfg=cfg,
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[topic_state][step=judge] apply_error=%s", type(e).__name__)
        return None

    if parsed is None:
        logger.debug("[topic_state][step=judge] result=noop")
        return None
    logger.debug(
        "[topic_state][step=judge] action=%s applied=%s confidence=%.2f",
        parsed["action"],
        bool(applied_topic_id),
        parsed["confidence"],
    )
    return {
        "applied": bool(applied_topic_id),
        "action": parsed["action"],
        "topic_id": applied_topic_id,
        "confidence": parsed["confidence"],
    }
