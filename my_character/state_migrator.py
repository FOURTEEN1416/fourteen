"""
状态迁移器 — 人设切换时状态平滑过渡

管理人设变更时的状态迁移，确保人设切换/演化时
情感、记忆、好感度的平滑过渡，支持回滚和版本化。
"""

from __future__ import annotations

import logging
import time as time_mod
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("state_migrator")


@dataclass
class StateSnapshot:
    emotion_state: dict[str, Any] = field(default_factory=dict)
    affinity_level: int = 0
    affection_points: float = 0.0
    energy: float = 1.0
    total_chats: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "emotion_state": self.emotion_state,
            "affinity_level": self.affinity_level,
            "affection_points": self.affection_points,
            "energy": self.energy,
            "total_chats": self.total_chats,
            "timestamp": self.timestamp,
        }


@dataclass
class MigrationRecord:
    id: str
    from_persona: str
    to_persona: str
    snapshot_before: StateSnapshot
    snapshot_after: StateSnapshot
    timestamp: float
    success: bool
    strategy: str = "soft"


@dataclass
class MigrationResult:
    success: bool
    record: MigrationRecord | None = None
    error: str | None = None


class StateMigrator:
    """状态迁移器 — 人设切换时状态平滑过渡"""

    SOFT_AFFINITY_FACTOR = 0.7
    MAX_LOG_SIZE = 50
    MAX_SNAPSHOTS = 20

    def __init__(
        self,
        emotion_engine: Any | None = None,
        memory_pipeline: Any | None = None,
        emotion_memory: Any | None = None,
    ):
        self._emotion = emotion_engine
        self._memory = memory_pipeline
        self._emotion_memory = emotion_memory
        self._migration_log: list[MigrationRecord] = []
        self._snapshots: dict[str, StateSnapshot] = {}

    def set_engines(
        self,
        emotion_engine: Any,
        memory_pipeline: Any,
    ) -> None:
        self._emotion = emotion_engine
        self._memory = memory_pipeline

    def snapshot(self, persona_id: str) -> StateSnapshot | None:
        if self._emotion is None:
            logger.warning("EmotionEngine not set, cannot create snapshot")
            return None
        try:
            state = self._emotion._state if hasattr(self._emotion, "_state") else None
            if state is None:
                return None

            emotion_dict = {}
            if hasattr(state, "to_dict"):
                emotion_dict = state.to_dict()

            affinity = getattr(state, "affinity", 0)
            affection_points = getattr(state, "affection_points", 0.0)
            energy = getattr(state, "energy", 1.0)
            total_chats = getattr(self._emotion, "_total_chats", 0)

            snap = StateSnapshot(
                emotion_state=emotion_dict,
                affinity_level=affinity,
                affection_points=affection_points,
                energy=energy,
                total_chats=total_chats,
                timestamp=time_mod.time(),
            )
            self._snapshots[persona_id] = snap
            if len(self._snapshots) > self.MAX_SNAPSHOTS:
                oldest_key = next(iter(self._snapshots))
                del self._snapshots[oldest_key]
            logger.info("Snapshot created: %s, affinity=%d", persona_id, affinity)
            return snap
        except Exception as e:  # noqa: BLE001
            logger.error("Snapshot failed: %s", e)
            return None

    def migrate(
        self,
        from_id: str,
        to_id: str,
        strategy: str = "soft",
    ) -> MigrationResult:
        if self._emotion is None:
            return MigrationResult(success=False, error="EmotionEngine not set")

        before_snapshot = self.snapshot(from_id) or StateSnapshot()
        before_snapshot_copy = StateSnapshot(**{k: v for k, v in before_snapshot.to_dict().items()})

        try:
            if strategy == "hard":
                self._emotion.reset()
                after = self.snapshot(to_id) or StateSnapshot()
            elif strategy == "inherit":
                self._apply_inherit(before_snapshot)
                after = self.snapshot(to_id) or StateSnapshot()
            else:
                self._apply_soft(before_snapshot)
                after = self.snapshot(to_id) or StateSnapshot()

            record = MigrationRecord(
                id=str(uuid.uuid4())[:8],
                from_persona=from_id,
                to_persona=to_id,
                snapshot_before=before_snapshot_copy,
                snapshot_after=after,
                timestamp=time_mod.time(),
                success=True,
                strategy=strategy,
            )
            self._migration_log.append(record)
            if len(self._migration_log) > self.MAX_LOG_SIZE:
                self._migration_log = self._migration_log[-self.MAX_LOG_SIZE:]

            logger.info("Migration: %s → %s (%s) success", from_id, to_id, strategy)
            return MigrationResult(success=True, record=record)

        except Exception as e:
            logger.exception("Migration failed: %s", e)
            return MigrationResult(success=False, error="migration_failed")

    def _apply_soft(self, snapshot: StateSnapshot) -> None:
        if self._emotion is None:
            return
        state = self._emotion._state if hasattr(self._emotion, "_state") else None
        if state is None:
            return

        new_affinity = int(snapshot.affinity_level * self.SOFT_AFFINITY_FACTOR)
        state.affinity = max(0, min(8, new_affinity))
        state.energy = snapshot.energy
        self._emotion._total_chats = snapshot.total_chats

    def _apply_inherit(self, snapshot: StateSnapshot) -> None:
        if self._emotion is None:
            return
        state = self._emotion._state if hasattr(self._emotion, "_state") else None
        if state is None:
            return

        state.affinity = snapshot.affinity_level
        state.affection_points = snapshot.affection_points
        state.energy = snapshot.energy
        self._emotion._total_chats = snapshot.total_chats

    def rollback(self, migration_id: str) -> bool:
        if self._emotion is None:
            return False
        for record in reversed(self._migration_log):
            if record.id == migration_id and record.success:
                before = record.snapshot_before
                state = self._emotion._state if hasattr(self._emotion, "_state") else None
                if state:
                    state.affinity = before.affinity_level
                    state.affection_points = before.affection_points
                    state.energy = before.energy
                    logger.info("Rollback to migration %s", migration_id)
                    return True
        return False

    def get_migration_history(self, limit: int = 10) -> list[dict[str, Any]]:
        return [
            {
                "id": r.id,
                "from": r.from_persona,
                "to": r.to_persona,
                "strategy": r.strategy,
                "success": r.success,
                "timestamp": r.timestamp,
            }
            for r in self._migration_log[-limit:]
        ]

    def health_check(self) -> dict[str, Any]:
        return {
            "emotion_set": self._emotion is not None,
            "memory_set": self._memory is not None,
            "snapshots_count": len(self._snapshots),
            "migration_count": len(self._migration_log),
        }
