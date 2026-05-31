"""StorylineEngine — 剧情线运行时引擎。

职责：
1. 管理每个角色的时间线进度
2. 阶段检测和自动演进
3. 行为规则注入 system prompt
4. 结局检测和触发
"""

from __future__ import annotations

import logging
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import EndingConfig, StageDefinition, StorylineConfig

logger = logging.getLogger("shisi.storyline.engine")

# 持久化回调类型：接收 (character_id, state_dict) → 无返回
PersistStateCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class StorylineState:
    """角色剧情线运行时状态"""
    character_id: str
    story_time_minutes: int = 0  # 剧情内总分钟数
    story_day: int = 1
    story_hour: int = 0
    story_minute: int = 0
    current_stage_index: int = 0
    is_ended: bool = False
    ended_at: float = 0.0  # 时间戳
    turn_count: int = 0  # 总对话轮次

    @property
    def display_time(self) -> str:
        """显示时间，如「第3天 14:30」"""
        return f"第{self.story_day}天 {self.story_hour:02d}:{self.story_minute:02d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "story_time_minutes": self.story_time_minutes,
            "story_day": self.story_day,
            "story_hour": self.story_hour,
            "story_minute": self.story_minute,
            "display_time": self.display_time,
            "current_stage_index": self.current_stage_index,
            "is_ended": self.is_ended,
            "turn_count": self.turn_count,
        }


@dataclass
class StageTransition:
    """阶段变更事件"""
    character_id: str
    old_index: int
    new_index: int
    old_stage_name: str
    new_stage_name: str
    story_time_minutes: int
    is_ending: bool = False


# 回调类型
StageChangeCallback = Callable[[StageTransition], Any]


class StorylineEngine:
    """剧情线引擎 — 时间驱动、阶段演进。"""

    def __init__(self):
        self._configs: dict[str, StorylineConfig] = {}
        self._states: dict[str, StorylineState] = {}
        self._callbacks: list[StageChangeCallback] = []
        self._persist_hooks: list[PersistStateCallback] = []

    def set_persist_hook(self, hook: PersistStateCallback | None) -> None:
        """设置状态持久化回调（每次 tick 后调用）。"""
        if hook is None:
            self._persist_hooks.clear()
        else:
            self._persist_hooks.append(hook)

    def clear_persist_hooks(self) -> None:
        self._persist_hooks.clear()

    # ── 配置管理 ──

    def set_config(self, character_id: str, config: StorylineConfig) -> None:
        """设置/更新角色的剧情线配置。"""
        self._configs[character_id] = config
        # 如果还没状态，初始化一个
        if character_id not in self._states:
            self._states[character_id] = StorylineState(character_id=character_id)
        logger.info("剧情线配置已设置: %s (enabled=%s)", character_id, config.enabled)

    def get_config(self, character_id: str) -> StorylineConfig | None:
        return self._configs.get(character_id)

    def has_config(self, character_id: str) -> bool:
        return character_id in self._configs

    def remove_config(self, character_id: str) -> None:
        self._configs.pop(character_id, None)
        self._states.pop(character_id, None)

    # ── 状态管理 ──

    def get_state(self, character_id: str) -> StorylineState | None:
        return self._states.get(character_id)

    def get_or_init_state(self, character_id: str) -> StorylineState:
        if character_id not in self._states:
            self._states[character_id] = StorylineState(character_id=character_id)
        return self._states[character_id]

    def reset_state(self, character_id: str) -> None:
        """重置角色的剧情进度。"""
        config = self._configs.get(character_id)
        if config:
            self._states[character_id] = StorylineState(character_id=character_id)
            self._trigger_persist(character_id)
            logger.info("剧情线状态已重置: %s", character_id)

    def set_state_from_dict(self, character_id: str, data: dict) -> None:
        """从字典恢复状态（如从数据库加载）。"""
        self._states[character_id] = StorylineState(
            character_id=character_id,
            story_time_minutes=data.get("story_time_minutes", 0),
            story_day=data.get("story_day", 1),
            story_hour=data.get("story_hour", 0),
            story_minute=data.get("story_minute", 0),
            current_stage_index=data.get("current_stage_index", 0),
            is_ended=data.get("is_ended", False),
            ended_at=data.get("ended_at", 0.0),
            turn_count=data.get("turn_count", 0),
        )

    # ── 核心逻辑 ──

    def tick(self, character_id: str) -> StorylineState:
        """推进一次时间（每句话调用一次）。

        返回当前状态，触发阶段变更回调（如果发生）。
        """
        config = self._configs.get(character_id)
        if not config or not config.enabled:
            return self.get_or_init_state(character_id)

        state = self.get_or_init_state(character_id)

        # 如果已结束，不再推进
        if state.is_ended:
            return state

        old_index = state.current_stage_index

        # 推进时间
        state.story_time_minutes += config.time_per_turn
        state.turn_count += 1

        # 更新时间显示
        self._update_display_time(state, config)

        # 检测新阶段
        new_index = self._detect_stage(state.story_time_minutes, config)

        if new_index != old_index:
            # 阶段变更
            old_name = config.stages[old_index].name if old_index < len(config.stages) else "未知"
            new_name = config.stages[new_index].name if new_index < len(config.stages) else "未知"

            transition = StageTransition(
                character_id=character_id,
                old_index=old_index,
                new_index=new_index,
                old_stage_name=old_name,
                new_stage_name=new_name,
                story_time_minutes=state.story_time_minutes,
                is_ending=False,
            )
            state.current_stage_index = new_index

            # 检查是否到达结局
            if self._check_ending(state, config):
                transition.is_ending = True
                state.is_ended = True
                state.ended_at = time_module.time()

            self._notify_callbacks(transition)
            logger.info(
                "剧情线阶段变更: %s %s→%s (时间:%s)",
                character_id, transition.old_stage_name, transition.new_stage_name,
                state.display_time,
            )
        else:
            # 检查结局（在最后阶段超时）
            if self._check_ending(state, config):
                state.is_ended = True
                state.ended_at = time_module.time()
                transition = StageTransition(
                    character_id=character_id,
                    old_index=new_index,
                    new_index=new_index,
                    old_stage_name=config.stages[new_index].name if new_index < len(config.stages) else "未知",
                    new_stage_name="结局",
                    story_time_minutes=state.story_time_minutes,
                    is_ending=True,
                )
                self._notify_callbacks(transition)
                logger.info("剧情线结局触发: %s (时间:%s)", character_id, state.display_time)

        # 触发持久化
        self._trigger_persist(character_id)

        return state

    def get_current_stage(self, character_id: str) -> StageDefinition | None:
        """获取当前阶段定义。"""
        config = self._configs.get(character_id)
        state = self._states.get(character_id)
        if not config or not state or not config.stages:
            return None
        idx = state.current_stage_index
        if 0 <= idx < len(config.stages):
            return config.stages[idx]
        return None

    def get_behavior_rules(self, character_id: str) -> list[str]:
        """获取当前阶段的行为规则（用于 prompt 注入）。"""
        stage = self.get_current_stage(character_id)
        if not stage:
            return []
        return [r.rule for r in stage.behavior_rules if r.enforce]

    def get_style_rules(self, character_id: str) -> list[str]:
        """获取当前阶段的对话风格规则。"""
        stage = self.get_current_stage(character_id)
        if not stage:
            return []
        return [r.style for r in stage.style_rules if r.inject_prompt]

    def get_storyline_context(self, character_id: str) -> str:
        """获取剧情线上下文文本（注入 system prompt 用）。"""
        config = self._configs.get(character_id)
        state = self._states.get(character_id)
        if not config or not state or not config.enabled:
            return ""

        parts = [f"# 剧情时间\n当前时间：{state.display_time}"]

        stage = self.get_current_stage(character_id)
        if stage:
            if stage.dialogue_notes:
                parts.append(f"当前阶段：{stage.display_name or stage.name}")
                parts.append(f"对话基调：{stage.dialogue_notes}")

            style_rules = self.get_style_rules(character_id)
            if style_rules:
                parts.append("对话风格：" + "；".join(style_rules))

            behavior_rules = self.get_behavior_rules(character_id)
            if behavior_rules:
                parts.append("行为规则：" + "；".join(behavior_rules))

        if state.is_ended:
            config = self._configs.get(character_id)
            if config and config.ending.narrative:
                parts.append(f"\n# 结局\n{config.ending.narrative}")
            if config and config.ending.blank_after_end:
                parts.append("\n【注意】本剧情已结束，之后的所有回复必须为空。")

        return "\n".join(parts)

    def get_progress(self, character_id: str) -> dict[str, Any]:
        """获取剧情进度（用于前端展示）。"""
        config = self._configs.get(character_id)
        state = self._states.get(character_id)
        if not config:
            return {"enabled": False}

        if not state:
            return {
                "enabled": config.enabled,
                "stages": len(config.stages),
                "active": False,
            }

        stage = self.get_current_stage(character_id)
        progress_pct = 0.0
        if config.max_duration_minutes > 0:
            progress_pct = min(100.0, state.story_time_minutes / config.max_duration_minutes * 100)

        return {
            "enabled": config.enabled,
            "state": state.to_dict(),
            "current_stage": {
                "name": stage.name if stage else "",
                "display_name": stage.display_name if stage else "",
                "style_rules": self.get_style_rules(character_id),
                "behavior_rules": self.get_behavior_rules(character_id),
                "dialogue_notes": stage.dialogue_notes if stage else "",
                "transition_message": stage.transition_message if stage else "",
            } if stage else None,
            "progress_percent": progress_pct,
            "total_stages": len(config.stages),
        }

    # ── 事件订阅 ──

    def subscribe(self, callback: StageChangeCallback) -> None:
        self._callbacks.append(callback)

    def unsubscribe(self, callback: StageChangeCallback) -> None:
        self._callbacks.remove(callback)

    # ── 内部方法 ──

    def _update_display_time(self, state: StorylineState, config: StorylineConfig) -> None:
        """根据总分钟数更新天/时/分显示。"""
        total = state.story_time_minutes
        # 从初始时间偏移
        total += config.start_day * 1440 + config.start_hour * 60 + config.start_minute
        state.story_day = total // 1440
        remainder = total % 1440
        state.story_hour = remainder // 60
        state.story_minute = remainder % 60

    def _detect_stage(self, time_minutes: int, config: StorylineConfig) -> int:
        """根据时间检测所在阶段索引。"""
        for i, stage in enumerate(config.stages):
            if stage.timing.contains(time_minutes):
                return i
        # 超出范围：最后一个阶段
        if config.stages:
            if time_minutes >= config.stages[-1].timing.end_minutes:
                return len(config.stages) - 1
            return 0
        return 0

    def _check_ending(self, state: StorylineState, config: StorylineConfig) -> bool:
        """检查是否到达结局条件。"""
        if state.is_ended:
            return False
        if state.story_time_minutes >= config.max_duration_minutes:
            return True
        return False

    def _trigger_persist(self, character_id: str) -> None:
        """触发持久化回调。"""
        state = self._states.get(character_id)
        if state is None:
            return
        state_dict = state.to_dict()
        for hook in self._persist_hooks:
            try:
                hook(character_id, state_dict)
            except Exception as e:
                logger.debug("持久化回调异常: %s", e)

    def _notify_callbacks(self, transition: StageTransition) -> None:
        for cb in self._callbacks:
            try:
                cb(transition)
            except Exception as e:
                logger.warning("剧情线回调异常: %s", e)


# ── 单例 ──

_engine: StorylineEngine | None = None


def get_storyline_engine() -> StorylineEngine:
    global _engine
    if _engine is None:
        _engine = StorylineEngine()
    return _engine
