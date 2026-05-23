"""微信主动消息增强 — 早安/晚安附带情感摘要+好感度通知。"""

from __future__ import annotations

import logging
from typing import Any

from ..affinity.enhancer import AffinityEnhancer
from ..emotion_stage.stage_engine import EmotionStageEngine

logger = logging.getLogger("shisi.wechat.proactive_messenger")


class WeChatProactiveMessenger:
    def __init__(
        self,
        affinity_enhancer: AffinityEnhancer | None = None,
        stage_engine: EmotionStageEngine | None = None,
    ):
        self._affinity = affinity_enhancer
        self._stage = stage_engine

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

        if summary_parts:
            result["summary"] = " | ".join(summary_parts)

        return result

    def format_morning_greeting(self, character_name: str, character_id: str) -> str:
        base = f"早安~{character_name}醒来啦☀️"
        if self._affinity:
            value = self._affinity.get_value(character_id)
            if value >= 75:
                base += " 好想你呀❤️"
            elif value >= 50:
                base += " 今天也要开心哦😊"
        return base

    def format_night_greeting(self, character_name: str, character_id: str) -> str:
        base = f"晚安~{character_name}要睡了🌙"
        if self._affinity:
            value = self._affinity.get_value(character_id)
            if value >= 75:
                base += " 梦里见❤️"
            elif value >= 50:
                base += " 好梦😊"
        return base
