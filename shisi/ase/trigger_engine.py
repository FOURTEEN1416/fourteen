"""TriggerEngine — 多类型触发器。

支持：
- time_trigger: 在指定剧情时间点触发
- stage_trigger: 进入/离开某个阶段时触发
- affinity_trigger: 好感度达到阈值时触发
- event_trigger: 用户发送特定内容时触发
- idle_trigger: 用户长时间不回复时触发
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from shisi.storyline.engine import StageTransition, StorylineEngine

logger = logging.getLogger("shisi.ase.trigger_engine")


@dataclass
class TriggerResult:
    """触发结果"""
    triggered: bool = False
    trigger_type: str = ""
    trigger_name: str = ""
    message: str = ""
    priority: int = 0
    stage_name: str = ""


@dataclass
class TimeTrigger:
    """时间触发器 — 在指定剧情时间点触发"""
    name: str
    story_time_minutes: int  # 触发时间点（剧情分钟数）
    message: str  # 消息内容
    priority: int = 5
    repeatable: bool = False
    triggered: bool = False

    def check(self, current_time: int) -> bool:
        if self.repeatable:
            return current_time >= self.story_time_minutes
        if self.triggered:
            return False
        if current_time >= self.story_time_minutes:
            self.triggered = True
            return True
        return False


@dataclass
class StageTrigger:
    """阶段触发器 — 进入阶段时触发"""
    name: str
    stage_name: str  # 目标阶段名
    on_enter: bool = True  # True=进入时, False=离开时
    message: str = ""
    priority: int = 8
    triggered: bool = False

    def check(self, transition: StageTransition | None) -> bool:
        if self.triggered:
            return False
        if not transition:
            return False
        if self.on_enter and transition.new_stage_name == self.stage_name:
            self.triggered = True
            return True
        if not self.on_enter and transition.old_stage_name == self.stage_name:
            self.triggered = True
            return True
        return False


@dataclass
class AffinityTrigger:
    """好感度触发器 — 好感度达到阈值时触发"""
    name: str
    threshold: float
    direction: str = "above"  # "above" | "below"
    message: str = ""
    priority: int = 6
    triggered: bool = False

    def check(self, current_affinity: float) -> bool:
        if self.triggered:
            return False
        if self.direction == "above" and current_affinity >= self.threshold:
            self.triggered = True
            return True
        if self.direction == "below" and current_affinity < self.threshold:
            self.triggered = True
            return True
        return False


@dataclass
class EventTrigger:
    """事件触发器 — 用户发送特定内容时触发"""
    name: str
    keywords: list[str] = field(default_factory=list)
    message: str = ""
    priority: int = 7
    cooldown_turns: int = 5
    _last_triggered_turn: int = -100

    def check(self, user_message: str, current_turn: int) -> bool:
        if current_turn - self._last_triggered_turn < self.cooldown_turns:
            return False
        for kw in self.keywords:
            if kw in user_message:
                self._last_triggered_turn = current_turn
                return True
        return False


@dataclass
class IdleTrigger:
    """空闲触发器 — 用户长时间不回复时触发"""
    name: str
    idle_minutes: int = 30
    message: str = ""
    priority: int = 3
    _last_triggered: float = 0.0
    _cooldown_minutes: int = 120

    def check(self, idle_seconds: float) -> bool:
        import time as tm
        now = tm.time()
        if now - self._last_triggered < self._cooldown_minutes * 60:
            return False
        if idle_seconds >= self.idle_minutes * 60:
            self._last_triggered = now
            return True
        return False


class TriggerEngine:
    """触发器引擎 — 管理和检查所有触发器。"""

    def __init__(self, storyline_engine: StorylineEngine | None = None):
        self._time_triggers: dict[str, list[TimeTrigger]] = {}
        self._stage_triggers: dict[str, list[StageTrigger]] = {}
        self._affinity_triggers: dict[str, list[AffinityTrigger]] = {}
        self._event_triggers: dict[str, list[EventTrigger]] = {}
        self._idle_triggers: dict[str, list[IdleTrigger]] = {}
        self._storyline = storyline_engine

    # ── 注册触发器 ──

    def add_time_trigger(self, character_id: str, trigger: TimeTrigger) -> None:
        self._time_triggers.setdefault(character_id, []).append(trigger)

    def add_stage_trigger(self, character_id: str, trigger: StageTrigger) -> None:
        self._stage_triggers.setdefault(character_id, []).append(trigger)

    def add_affinity_trigger(self, character_id: str, trigger: AffinityTrigger) -> None:
        self._affinity_triggers.setdefault(character_id, []).append(trigger)

    def add_event_trigger(self, character_id: str, trigger: EventTrigger) -> None:
        self._event_triggers.setdefault(character_id, []).append(trigger)

    def add_idle_trigger(self, character_id: str, trigger: IdleTrigger) -> None:
        self._idle_triggers.setdefault(character_id, []).append(trigger)

    # ── 检查触发器 ──

    def check_time_triggers(self, character_id: str, current_time: int) -> list[TriggerResult]:
        results = []
        for trigger in self._time_triggers.get(character_id, []):
            if trigger.check(current_time):
                results.append(TriggerResult(
                    triggered=True, trigger_type="time", trigger_name=trigger.name,
                    message=trigger.message, priority=trigger.priority,
                ))
        return results

    def check_stage_triggers(self, character_id: str, transition: StageTransition | None) -> list[TriggerResult]:
        results = []
        for trigger in self._stage_triggers.get(character_id, []):
            if trigger.check(transition):
                results.append(TriggerResult(
                    triggered=True, trigger_type="stage", trigger_name=trigger.name,
                    message=trigger.message, priority=trigger.priority,
                    stage_name=trigger.stage_name,
                ))
        return results

    def check_affinity_triggers(self, character_id: str, affinity: float) -> list[TriggerResult]:
        results = []
        for trigger in self._affinity_triggers.get(character_id, []):
            if trigger.check(affinity):
                results.append(TriggerResult(
                    triggered=True, trigger_type="affinity", trigger_name=trigger.name,
                    message=trigger.message, priority=trigger.priority,
                ))
        return results

    def check_event_triggers(self, character_id: str, user_message: str, turn: int) -> list[TriggerResult]:
        results = []
        for trigger in self._event_triggers.get(character_id, []):
            if trigger.check(user_message, turn):
                results.append(TriggerResult(
                    triggered=True, trigger_type="event", trigger_name=trigger.name,
                    message=trigger.message, priority=trigger.priority,
                ))
        return results

    def check_idle_triggers(self, character_id: str, idle_seconds: float) -> list[TriggerResult]:
        results = []
        for trigger in self._idle_triggers.get(character_id, []):
            if trigger.check(idle_seconds):
                results.append(TriggerResult(
                    triggered=True, trigger_type="idle", trigger_name=trigger.name,
                    message=trigger.message, priority=trigger.priority,
                ))
        return results

    # ── 默认注册 ──

    def register_default_triggers(self, character_id: str) -> None:
        """注册针对剧情线的默认触发器。"""
        config = self._storyline.get_config(character_id) if self._storyline else None
        if not config or not config.enabled:
            return

        for i, stage in enumerate(config.stages):
            if stage.transition_message:
                self.add_stage_trigger(character_id, StageTrigger(
                    name=f"enter_{stage.name}",
                    stage_name=stage.name,
                    on_enter=True,
                    message=stage.transition_message,
                    priority=9,
                ))

        # 结局触发器
        if config.ending.final_dialogue:
            # 在最终时间点触发
            self.add_time_trigger(character_id, TimeTrigger(
                name="final_ending",
                story_time_minutes=config.max_duration_minutes - config.time_per_turn,
                message=config.ending.final_dialogue,
                priority=10,
            ))

    def get_all_triggers(self, character_id: str) -> list[TriggerResult]:
        """获取当前所有已触发的触发器。"""
        return []
