"""转发管理器 — 跨角色转发记忆（落库持久化，批6b 项8）。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("shisi.memory.forward_manager")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"
# 保留上限：超出即裁最旧（旧为进程内 list 只增且重启丢）
_MAX_LOG = 1000


class ForwardManager:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        # 迁移未跑到位时自愈（与建表 SQL 同源，见 shisi/migrations.py）
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_forwards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_character TEXT NOT NULL,
                to_character TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                content TEXT DEFAULT '',
                forwarded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        return conn

    def forward(
        self,
        from_character: str,
        to_character: str,
        memory_id: str,
        memory_content: str = "",
    ) -> int:
        """转发并生成可追溯目标侧派生记录（缺陷 F）。

        Returns:
            forward_id（>0 成功）；失败返回 0。旧 bool 契约由 int 真值投影
            保持（`if not ok` / `assert mgr.forward(...)` 语义不变）。
            完整回执见 :meth:`forward_receipt`。
        """
        receipt = self.forward_receipt(
            from_character, to_character, memory_id, memory_content
        )
        return int(receipt.get("forward_id") or 0) if receipt.get("ok") else 0

    def forward_receipt(
        self,
        from_character: str,
        to_character: str,
        memory_id: str,
        memory_content: str = "",
    ) -> dict[str, Any]:
        """转发完整回执 `{ok, forward_id, from, to, memory_id, content}`。"""
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO memory_forwards (from_character, to_character, memory_id, content)"
                " VALUES (?,?,?,?)",
                (from_character, to_character, memory_id, memory_content),
            )
            forward_id = int(cur.lastrowid or 0)
            conn.execute(
                "DELETE FROM memory_forwards WHERE id NOT IN"
                " (SELECT id FROM memory_forwards ORDER BY id DESC LIMIT ?)",
                (_MAX_LOG,),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("记忆转发落库失败: %s", e)
            return {"ok": False, "error": str(e), "forward_id": 0}
        finally:
            conn.close()
        logger.info(
            "记忆转发: %s → %s, memory_id=%s forward_id=%s",
            from_character, to_character, memory_id, forward_id,
        )
        return {
            "ok": True,
            "forward_id": forward_id,
            "from": from_character,
            "to": to_character,
            "memory_id": memory_id,
            "content": memory_content,
        }

    def get_forwards(self, character_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                'SELECT id, from_character AS "from", to_character AS "to",'
                " memory_id, content, forwarded_at"
                " FROM memory_forwards WHERE to_character=? ORDER BY id DESC",
                (character_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
