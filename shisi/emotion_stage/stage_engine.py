"""EmotionStageEngine — 好感度驱动的情感阶段评估引擎。"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .event_dispatcher import EventDispatcher, StageChangeEvent
from .stage_config import EmotionStageConfig, StageDefinition

logger = logging.getLogger("shisi.emotion_stage.stage_engine")


class EmotionStageEngine:
    def __init__(self, config: EmotionStageConfig | None = None):
        self._config = config or EmotionStageConfig.from_yaml()
        self._dispatcher = EventDispatcher()
        self._states: dict[str, dict[str, Any]] = {}

    @property
    def stages(self) -> list[StageDefinition]:
        return self._config.stages

    @property
    def dispatcher(self) -> EventDispatcher:
        return self._dispatcher

    def evaluate(self, character_id: str, affinity: float) -> StageDefinition:
        stage = self._find_stage(affinity)

        if character_id in self._states:
            old = self._states[character_id]
            old_index = old.get("stage_index", 0)
            new_index = self._config.stages.index(stage) if stage in self._config.stages else 0

            if new_index != old_index:
                if new_index < old_index and not self._config.allow_backward:
                    logger.warning("阶段回退被禁止: %s %s→%s", character_id, old["current_stage"], stage.name)
                    return self._config.stages[old_index]

                event = StageChangeEvent(
                    character_id=character_id,
                    old_stage=old["current_stage"],
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

        return stage

    def get_current_stage(self, character_id: str) -> Optional[StageDefinition]:
        state = self._states.get(character_id)
        if not state:
            return None
        idx = state.get("stage_index", 0)
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
