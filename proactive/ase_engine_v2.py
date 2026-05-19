from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ase_engine_v2")

PROACTIVE_MESSAGES = {
    "morning": ["早安呀～今天又比我先醒", "起床了吗？别忘了吃早餐"],
    "night": ["还不睡？要我陪你聊会儿", "很晚了呢，该休息了"],
    "meal": ["吃饭了没？别又不按时吃饭", "该吃饭了吧？别饿着"],
    "miss": ["在忙什么呢？好久了…", "你今天还没找我，有点想你了"],
}


class FrequencyAdapter:
    def __init__(self, normal_daily: int = 8, low_daily: int = 3, min_weekly: int = 1):
        self.normal_daily = normal_daily
        self.low_daily = low_daily
        self.min_weekly = min_weekly
        self._unanswered_count = 0
        self._current_level = "normal"

    def on_reply_received(self):
        self._unanswered_count = max(0, self._unanswered_count - 1)
        if self._current_level == "low" and self._unanswered_count == 0:
            self._current_level = "normal"

    def on_no_reply(self):
        self._unanswered_count += 1
        if self._unanswered_count >= 5 and self._current_level != "minimal":
            self._current_level = "minimal"
        elif self._unanswered_count >= 3 and self._current_level == "normal":
            self._current_level = "low"

    def get_max_daily(self) -> int:
        if self._current_level == "minimal":
            return self.min_weekly
        if self._current_level == "low":
            return self.low_daily
        return self.normal_daily

    @property
    def level(self) -> str:
        return self._current_level


class ASEEngineV2:
    def __init__(self, llm_gateway=None, max_daily_messages: int = 8,
                 min_interval_minutes: int = 30, cooldown_after_reply: int = 15,
                 urgency_threshold: float = 2.0):
        self._llm = llm_gateway
        self.max_daily_messages = max_daily_messages
        self.min_interval_minutes = min_interval_minutes
        self.cooldown_after_reply = cooldown_after_reply
        self.urgency_threshold = urgency_threshold
        self._urgency = 0.0
        self._last_chat_time: Optional[float] = None
        self._last_proactive_time: Optional[float] = None
        self._daily_count = 0
        self._freq_adapter = FrequencyAdapter()

    @property
    def urgency(self) -> float:
        return self._urgency

    def on_chat(self, user_msg: str = "", reply: str = ""):
        now = time.time()
        self._last_chat_time = now
        self._last_proactive_time = now
        self._urgency = 0.0
        self._freq_adapter.on_reply_received()

    def tick(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        self._update_urgency(now)
        if self._urgency < self.urgency_threshold:
            return None
        if self._daily_count >= self._freq_adapter.get_max_daily():
            return None
        if self._last_proactive_time and (now - self._last_proactive_time) < self.min_interval_minutes * 60:
            return None
        trigger_type, content = self._generate_proactive_message(now)
        if content:
            self._daily_count += 1
            self._last_proactive_time = now
            return {"type": trigger_type, "content": content, "urgency": self._urgency}
        return None

    def _update_urgency(self, now: float):
        if self._last_chat_time is None:
            self._urgency = 3.0
            return
        hours = (now - self._last_chat_time) / 3600
        if hours < 2:
            self._urgency += 0.1
        elif hours < 8:
            self._urgency += 0.3
        elif hours < 24:
            self._urgency += 0.5
        else:
            self._urgency += 1.0
        self._urgency = min(self._urgency, 10.0)

    def _generate_proactive_message(self, now: float) -> tuple:
        import datetime
        hour = datetime.datetime.fromtimestamp(now).hour
        if 7 <= hour <= 9:
            return "morning", self._pick_message("morning")
        if 22 <= hour or hour <= 1:
            return "night", self._pick_message("night")
        if 11 <= hour <= 13 or 17 <= hour <= 19:
            return "meal", self._pick_message("meal")
        if self._urgency >= 3.0:
            return "miss", self._pick_message("miss")
        return "miss", self._pick_message("miss")

    def _pick_message(self, trigger_type: str) -> str:
        import random
        messages = PROACTIVE_MESSAGES.get(trigger_type, PROACTIVE_MESSAGES["miss"])
        if self._llm and self._urgency >= 2.5:
            try:
                llm_msg = self._llm.chat(
                    query="生成一条简短自然的主动关心消息，15字以内，符合傲娇人设",
                    max_tokens=32, temperature=0.9,
                )
                if llm_msg and len(llm_msg) <= 30:
                    return llm_msg
            except Exception:
                pass
        return random.choice(messages)

    def daily_reset(self):
        self._daily_count = 0
        self._urgency = 0.0

    def health_check(self) -> dict:
        return {
            "urgency": self._urgency,
            "daily_count": self._daily_count,
            "freq_level": self._freq_adapter.level,
        }
