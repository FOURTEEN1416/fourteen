"""增强版主动消息引擎 — 接入剧情线和场景叙事器。

保留原有 WeChatProactiveMessenger 接口兼容性，新增剧情线感知。
"""

from __future__ import annotations

import logging
from typing import Any

from shisi.ase.scene_narrator import SceneNarrator
from shisi.ase.trigger_engine import TriggerEngine
from shisi.storyline.engine import get_storyline_engine

from ..affinity.enhancer import AffinityEnhancer
from ..emotion_stage.stage_engine import EmotionStageEngine

logger = logging.getLogger("shisi.wechat.proactive_messenger")


class WeChatProactiveMessenger:
    """增强版主动消息引擎。

    兼容原有接口，新增剧情线感知能力。
    """

    def __init__(
        self,
        affinity_enhancer: AffinityEnhancer | None = None,
        stage_engine: EmotionStageEngine | None = None,
    ):
        self._affinity = affinity_enhancer
        self._stage = stage_engine
        self._scene_narrator = SceneNarrator(get_storyline_engine())
        self._trigger_engine = TriggerEngine(get_storyline_engine())

    @property
    def trigger_engine(self) -> TriggerEngine:
        return self._trigger_engine

    # ── 原有接口（向后兼容） ──

    def enhance_proactive_message(self, message: str, character_id: str, emotion: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {"text": message, "summary": None}

        summary_parts = []
        if emotion:
            summary_parts.append(f"情感：{emotion}")

        if self._affinity:
            value = self._affinity.get_value(character_id)
            summary_parts.append(f"好感度：{value:.0f}")

        if self._stage:
            progress = self._stage.get_progress(character_id)
            summary_parts.append(f"阶段：{progress['current_stage']}")

        # 新增：剧情线时间
        sl_engine = get_storyline_engine()
        sl_state = sl_engine.get_state(character_id)
        if sl_state and not sl_state.is_ended:
            summary_parts.append(f"剧情时间：{sl_state.display_time}")

        if summary_parts:
            result["summary"] = " | ".join(summary_parts)

        return result

    def format_morning_greeting(self, character_name: str, character_id: str) -> str:
        """早安 — 优先使用剧情线上下文。"""
        sl_engine = get_storyline_engine()
        sl_state = sl_engine.get_state(character_id)
        sl_config = sl_engine.get_config(character_id)

        if sl_config and sl_config.enabled and sl_state:
            stage = sl_engine.get_current_stage(character_id)
            if stage:
                return self._scene_narrator.generate_proactive_message(
                    character_id, character_name, stage.name, sl_state.display_time
                )

        # 降级到原有逻辑
        base = f"早安~{character_name}醒来啦☀️"
        if self._affinity:
            value = self._affinity.get_value(character_id)
            if value >= 75:
                base += " 好想你呀❤️"
            elif value >= 50:
                base += " 今天也要开心哦😊"
        return base

    def format_night_greeting(self, character_name: str, character_id: str) -> str:
        """晚安 — 优先使用剧情线上下文。"""
        sl_engine = get_storyline_engine()
        sl_state = sl_engine.get_state(character_id)
        sl_config = sl_engine.get_config(character_id)

        if sl_config and sl_config.enabled and sl_state:
            stage = sl_engine.get_current_stage(character_id)
            if stage:
                return self._scene_narrator.generate_proactive_message(
                    character_id, character_name, stage.name, sl_state.display_time
                )

        base = f"晚安~{character_name}要睡了🌙"
        if self._affinity:
            value = self._affinity.get_value(character_id)
            if value >= 75:
                base += " 梦里见❤️"
            elif value >= 50:
                base += " 好梦😊"
        return base

    # ── 新增：场景叙事 ──

    def generate_scene(self, character_id: str, character_name: str = "") -> str:
        """生成当前场景的旁白。"""
        return self._scene_narrator.generate_scene_narrative(character_id, character_name)

    def format_stage_change(self, transition, character_name: str) -> str:
        """阶段变更消息。"""
        return self._scene_narrator.format_stage_transition_message(transition, character_name)
