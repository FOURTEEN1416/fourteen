"""AffinityEnhancer — 好感度更新、衰减、解锁、审计。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_config
from .decay_engine import DecayEngine
from .unlock_manager import UnlockEvent, UnlockManager

logger = logging.getLogger("shisi.affinity.enhancer")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class AffinityEnhancer:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        self._decay = DecayEngine()
        self._unlock = UnlockManager()
        self._min = get_config("affinity", "min_value", 0)
        self._max = get_config("affinity", "max_value", 100)
        self._audit_enabled = get_config("affinity", "audit_enabled", True)
        self._values: dict[str, float] = {}
        self._last_interaction: dict[str, datetime] = {}

    @property
    def unlock_manager(self) -> UnlockManager:
        return self._unlock

    def update(
        self,
        character_id: str,
        delta: float,
        reason: str = "",
        source: str = "chat",
    ) -> tuple[float, list[UnlockEvent]]:
        old = self._values.get(character_id, 0.0)
        new = max(self._min, min(self._max, old + delta))
        self._values[character_id] = new
        self._last_interaction[character_id] = datetime.now()

        unlocks = self._unlock.check_unlocks(character_id, old, new)

        self._record_affinity(character_id, old, new, delta, reason, source)
        if self._audit_enabled:
            self._audit(character_id, "update", f"delta={delta}, reason={reason}")

        return new, unlocks

    def apply_decay(self, character_id: str, now: datetime | None = None) -> float:
        if character_id not in self._values:
            return 0.0
        last = self._last_interaction.get(character_id, datetime.now())
        decay = self._decay.calculate_decay(self._values[character_id], last, now)
        if decay > 0:
            old = self._values[character_id]
            new = max(self._min, old - decay)
            self._values[character_id] = new
            self._record_affinity(character_id, old, new, -decay, "decay", "system")
        return decay

    def get_progress(self, character_id: str) -> dict[str, Any]:
        value = self._values.get(character_id, 0.0)
        return {
            "character_id": character_id,
            "affinity": value,
            "min": self._min,
            "max": self._max,
            "percentage": (value - self._min) / (self._max - self._min) * 100 if self._max > self._min else 0,
            "unlocks": [u.__dict__ for u in self._unlock.get_unlocks_at(value)],
        }

    def get_value(self, character_id: str) -> float:
        return self._values.get(character_id, 0.0)

    def _record_affinity(self, cid: str, old: float, new: float, delta: float, reason: str, source: str) -> None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT INTO affinity_records (character_id, old_value, new_value, delta, reason, source) VALUES (?,?,?,?,?,?)",
                    (cid, old, new, delta, reason, source),
                )
        except Exception as e:
            logger.warning("记录好感度变更失败: %s", e)

    def _audit(self, cid: str, action: str, detail: str) -> None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT INTO affinity_audit (character_id, action, detail) VALUES (?,?,?)",
                    (cid, action, detail),
                )
        except Exception as e:
            logger.warning("审计记录失败: %s", e)
