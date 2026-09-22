"""EmotionStageEngine — 好感度驱动的情感阶段评估引擎。

状态键与 `AffinityMapper._track_key` / `affinity_key` 同构：
有 user 时 ``user::character``，无 user 时裸 ``character``。
旧实现异常回退裸 character_id 会使跨用户阶段互相覆盖（已由 mapper 收口）。

2026-09-22 二次排查：`emotion_stage_state` 表建了却**从未读写**（全仓只有
CREATE），阶段状态纯内存 —— 重启即回「陌生」，与好感度已落盘的事实自相
矛盾。现 evaluate 后 UPSERT、构造时回放。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from .event_dispatcher import EventDispatcher, StageChangeEvent
from .stage_config import EmotionStageConfig, StageDefinition

logger = logging.getLogger("shisi.emotion_stage.stage_engine")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


class EmotionStageEngine:
    def __init__(
        self,
        config: EmotionStageConfig | None = None,
        db_path: Path | str | None = None,
    ):
        self._config = config or EmotionStageConfig.from_yaml()
        self._dispatcher = EventDispatcher()
        self._states: dict[str, dict[str, Any]] = {}
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        self._restore_from_db()

    @property
    def stages(self) -> list[StageDefinition]:
        return self._config.stages

    @property
    def dispatcher(self) -> EventDispatcher:
        return self._dispatcher

    def _restore_from_db(self) -> None:
        """从 emotion_stage_state 回放阶段（重启不丢）。失败只告警。"""
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                rows = conn.execute(
                    "SELECT character_id, current_stage, stage_index, affinity_value "
                    "FROM emotion_stage_state"
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.debug("阶段状态回放跳过: %s", e)
            return
        for key, stage_name, stage_index, affinity in rows or []:
            k = str(key or "")
            if not k:
                continue
            self._states[k] = {
                "current_stage": str(stage_name or "陌生"),
                "stage_index": int(stage_index or 0),
                "affinity_value": float(affinity or 0.0),
            }
        if self._states:
            logger.info("情感阶段回放恢复 %d 个键", len(self._states))

    def _persist(self, key: str, state: dict[str, Any]) -> None:
        """UPSERT 阶段状态（表主键 character_id 列存隔离键）。"""
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                conn.execute(
                    "INSERT INTO emotion_stage_state "
                    "(character_id, current_stage, stage_index, affinity_value, updated_at) "
                    "VALUES (?,?,?,?,datetime('now')) "
                    "ON CONFLICT(character_id) DO UPDATE SET "
                    "current_stage=excluded.current_stage, "
                    "stage_index=excluded.stage_index, "
                    "affinity_value=excluded.affinity_value, "
                    "updated_at=excluded.updated_at",
                    (
                        key,
                        str(state.get("current_stage") or "陌生"),
                        int(state.get("stage_index") or 0),
                        float(state.get("affinity_value") or 0.0),
                    ),
                )
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("阶段状态落盘失败 key=%s: %s", key, e)

    def resolve_stage(self, affinity: float) -> StageDefinition:
        """纯映射：好感度 → 阶段（无状态变更、不落盘、不发事件）。

        控制面查询（如 API 端点「这个好感度对应哪个阶段」）用它；
        `evaluate` 会按调用方给的键写 `_states` 与 `emotion_stage_state`，
        API 层没有用户维度，写入只会以裸角色键污染 track 键空间的回放。
        """
        return self._find_stage(affinity)

    def evaluate(self, character_id: str, affinity: float) -> StageDefinition:
        stage = self._find_stage(affinity)

        if character_id in self._states:
            old = self._states[character_id]
            old_index: int = old.get("stage_index", 0)
            new_index = self._config.stages.index(stage) if stage in self._config.stages else 0

            if new_index != old_index:
                if new_index < old_index and not self._config.allow_backward:
                    logger.warning("阶段回退被禁止: %s %s→%s", character_id, old["current_stage"], stage.name)
                    return self._config.stages[old_index]

                event = StageChangeEvent(
                    character_id=character_id,
                    old_stage=str(old["current_stage"]),
                    new_stage=stage.name,
                    old_index=old_index,
                    new_index=new_index,
                    affinity=affinity,
                )
                self._dispatcher.dispatch_sync(event)
                logger.info("情感阶段变更: %s %s→%s (好感度%.1f)", character_id, event.old_stage, event.new_stage, affinity)

        self._states[character_id] = {
            "current_stage": stage.name,
            "stage_index": self._config.stages.index(stage) if stage in self._config.stages else 0,
            "affinity_value": affinity,
        }
        self._persist(character_id, self._states[character_id])

        return stage

    def get_current_stage(self, character_id: str) -> StageDefinition | None:
        state = self._states.get(character_id)
        if not state:
            return None
        idx: int = state.get("stage_index", 0)
        if 0 <= idx < len(self._config.stages):
            return self._config.stages[idx]
        return None

    def get_progress(self, character_id: str) -> dict[str, Any]:
        state = self._states.get(character_id, {})
        stage = self.get_current_stage(character_id)
        return {
            "character_id": character_id,
            "current_stage": stage.name if stage else "陌生",
            "stage_index": state.get("stage_index", 0),
            "affinity": state.get("affinity_value", 0.0),
            "features": stage.features if stage else [],
            "total_stages": len(self._config.stages),
        }

    def subscribe(self, listener: Callable[[StageChangeEvent], Any]) -> None:
        self._dispatcher.subscribe(listener)

    def _find_stage(self, affinity: float) -> StageDefinition:
        for stage in self._config.stages:
            if stage.affinity_min <= affinity < stage.affinity_max:
                return stage
        if self._config.stages:
            last = self._config.stages[-1]
            if affinity >= last.affinity_max:
                return last
            return self._config.stages[0]
        return StageDefinition("陌生", 0, 100)
