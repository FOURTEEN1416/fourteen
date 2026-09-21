"""EventLedger — 智能体状态平面骨架（任务包 AX 阶段 C）。

append-only 事件账本：记录「本轮会话/工具/画像/关系」的因果事件，
供回放探针与后续投影读模型使用。P0 不改生产热路径读行为。

设计原则（方案 §B3/B5）：
- 完整 session_key 为主键部分（禁止剥 owner，防串台）
- 只追加，不原地改写
- 失败不得阻塞聊天（写账本异常上抛给调用方决定；生产接入时应吞掉）
- 与 affinity_records 兼容：ledger 可镜像 affinity_delta，但不替代 scale 真源
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 事件类型契约（可扩展，未知类型读取时保留）
EVENT_USER_MESSAGE = "user_message"
EVENT_ASSISTANT_REPLY = "assistant_reply"
EVENT_PROFILE_UPDATE = "profile_update"
EVENT_PROFILE_CORRECT = "profile_correct"
EVENT_MEMORY_WRITE = "memory_write"
EVENT_MEMORY_REINFORCE = "memory_reinforce"
EVENT_AFFINITY_DELTA = "affinity_delta"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PROMPT_SLOTS = "prompt_slots"
EVENT_PROACTIVE_SEND = "proactive_send"
EVENT_PROACTIVE_SKIP = "proactive_skip"
EVENT_ERROR = "error"

KNOWN_TYPES = frozenset(
    {
        EVENT_USER_MESSAGE,
        EVENT_ASSISTANT_REPLY,
        EVENT_PROFILE_UPDATE,
        EVENT_PROFILE_CORRECT,
        EVENT_MEMORY_WRITE,
        EVENT_MEMORY_REINFORCE,
        EVENT_AFFINITY_DELTA,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        EVENT_PROMPT_SLOTS,
        EVENT_PROACTIVE_SEND,
        EVENT_PROACTIVE_SKIP,
        EVENT_ERROR,
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    character_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    reply_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'system',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_ledger_session
    ON event_ledger(session_key, created_at);
CREATE INDEX IF NOT EXISTS idx_event_ledger_turn
    ON event_ledger(session_key, turn_id);
CREATE INDEX IF NOT EXISTS idx_event_ledger_type
    ON event_ledger(session_key, event_type);
"""


@dataclass
class LedgerEvent:
    event_id: str
    session_key: str
    character_id: str
    event_type: str
    turn_id: str
    reply_id: str
    actor: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class ReplayBundle:
    """一次回放：按 turn 或 reply 聚合的因果切片。"""

    session_key: str
    turn_id: str = ""
    reply_id: str = ""
    events: list[LedgerEvent] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)
    profile_ops: list[dict[str, Any]] = field(default_factory=list)
    memory_ops: list[dict[str, Any]] = field(default_factory=list)
    tool_ops: list[dict[str, Any]] = field(default_factory=list)
    affinity_ops: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "reply_id": self.reply_id,
            "event_count": len(self.events),
            "slot_keys": list(self.slots.keys()),
            "profile_ops": len(self.profile_ops),
            "memory_ops": len(self.memory_ops),
            "tool_ops": len(self.tool_ops),
            "affinity_ops": len(self.affinity_ops),
            "errors": len(self.errors),
        }


class EventLedger:
    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @staticmethod
    def default_path(root: Path | str | None = None) -> Path:
        base = Path(root) if root else Path(__file__).resolve().parents[2]
        return base / "data" / "agent_plane.db"

    @staticmethod
    def now_iso() -> str:
        # 与项目墙钟约定一致：本地时区 ISO（可测）
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with closing(self._conn()) as conn, conn:
            conn.executescript(_SCHEMA)

    def append(
        self,
        *,
        session_key: str,
        event_type: str,
        character_id: str = "",
        turn_id: str = "",
        reply_id: str = "",
        actor: str = "system",
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> LedgerEvent:
        sk = str(session_key or "").strip()
        et = str(event_type or "").strip()
        if not sk:
            raise ValueError("session_key_required")
        if not et:
            raise ValueError("event_type_required")
        ev = LedgerEvent(
            event_id=str(event_id or uuid.uuid4()),
            session_key=sk,
            character_id=str(character_id or ""),
            event_type=et,
            turn_id=str(turn_id or ""),
            reply_id=str(reply_id or ""),
            actor=str(actor or "system"),
            payload=dict(payload or {}),
            created_at=str(created_at or self.now_iso()),
        )
        with closing(self._conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO event_ledger
                (event_id, session_key, character_id, event_type,
                 turn_id, reply_id, actor, payload_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    ev.event_id,
                    ev.session_key,
                    ev.character_id,
                    ev.event_type,
                    ev.turn_id,
                    ev.reply_id,
                    ev.actor,
                    json.dumps(ev.payload, ensure_ascii=False),
                    ev.created_at,
                ),
            )
        return ev

    def append_many(self, events: Iterable[dict[str, Any]]) -> list[LedgerEvent]:
        out: list[LedgerEvent] = []
        for raw in events:
            data = dict(raw)
            out.append(self.append(**data))
        return out

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LedgerEvent:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:  # noqa: BLE001
            payload = {"_parse_error": True}
        return LedgerEvent(
            event_id=row["event_id"],
            session_key=row["session_key"],
            character_id=row["character_id"] or "",
            event_type=row["event_type"],
            turn_id=row["turn_id"] or "",
            reply_id=row["reply_id"] or "",
            actor=row["actor"] or "system",
            payload=payload if isinstance(payload, dict) else {"_value": payload},
            created_at=row["created_at"],
        )

    def query(
        self,
        *,
        session_key: str | None = None,
        event_type: str | None = None,
        turn_id: str | None = None,
        reply_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 200,
    ) -> list[LedgerEvent]:
        sql = ["SELECT * FROM event_ledger WHERE 1=1"]
        params: list[Any] = []
        if session_key is not None:
            sql.append("AND session_key = ?")
            params.append(str(session_key))
        if event_type is not None:
            sql.append("AND event_type = ?")
            params.append(str(event_type))
        if turn_id is not None:
            sql.append("AND turn_id = ?")
            params.append(str(turn_id))
        if reply_id is not None:
            sql.append("AND reply_id = ?")
            params.append(str(reply_id))
        if since:
            sql.append("AND created_at >= ?")
            params.append(str(since))
        if until:
            sql.append("AND created_at <= ?")
            params.append(str(until))
        sql.append("ORDER BY id ASC LIMIT ?")
        params.append(int(limit))
        with closing(self._conn()) as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def replay(
        self,
        *,
        session_key: str,
        turn_id: str = "",
        reply_id: str = "",
        limit: int = 500,
    ) -> ReplayBundle:
        """按 turn_id 或 reply_id 回放因果切片；二者至少给一个。"""
        if not turn_id and not reply_id:
            raise ValueError("turn_id_or_reply_id_required")
        events = self.query(
            session_key=session_key,
            turn_id=turn_id or None,
            reply_id=reply_id or None,
            limit=limit,
        )
        bundle = ReplayBundle(
            session_key=session_key,
            turn_id=turn_id,
            reply_id=reply_id,
            events=events,
        )
        for ev in events:
            et = ev.event_type
            if et == EVENT_PROMPT_SLOTS:
                bundle.slots = dict(ev.payload.get("slots") or ev.payload)
            elif et in (EVENT_PROFILE_UPDATE, EVENT_PROFILE_CORRECT):
                bundle.profile_ops.append({"type": et, **ev.payload})
            elif et in (EVENT_MEMORY_WRITE, EVENT_MEMORY_REINFORCE):
                bundle.memory_ops.append({"type": et, **ev.payload})
            elif et in (EVENT_TOOL_CALL, EVENT_TOOL_RESULT):
                bundle.tool_ops.append({"type": et, **ev.payload})
            elif et == EVENT_AFFINITY_DELTA:
                bundle.affinity_ops.append(dict(ev.payload))
            elif et == EVENT_ERROR:
                bundle.errors.append(dict(ev.payload))
        return bundle

    def count_by_session(self, session_key: str) -> dict[str, int]:
        with closing(self._conn()) as conn:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) AS c FROM event_ledger "
                "WHERE session_key = ? GROUP BY event_type",
                (str(session_key),),
            ).fetchall()
        return {r["event_type"]: int(r["c"]) for r in rows}

    def isolation_scan(self, foreign_session_key: str, turn_id: str) -> list[LedgerEvent]:
        """串台探针：查询「不属于该 session 的 turn」必须为空（仅按 session 过滤）。"""
        return self.query(session_key=str(foreign_session_key), turn_id=str(turn_id), limit=50)


_default_ledger: EventLedger | None = None


def default_ledger() -> EventLedger:
    global _default_ledger
    if _default_ledger is None:
        _default_ledger = EventLedger(EventLedger.default_path())
    return _default_ledger


def set_default_ledger(ledger: EventLedger | None) -> None:
    global _default_ledger
    _default_ledger = ledger


def event_to_dict(ev: LedgerEvent) -> dict[str, Any]:
    return asdict(ev)
