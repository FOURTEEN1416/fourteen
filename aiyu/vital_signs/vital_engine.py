"""VitalSignsEngine — 情感驱动生理指标模拟引擎。"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from ..config import get_config
from .emotion_mapping import EMOTION_VITAL_MAP

logger = logging.getLogger("aiyu.vital_signs.vital_engine")


@dataclass
class VitalSignsState:
    heart_rate: float
    temperature: float
    breath_rate: float
    last_emotion: str


class VitalSignsEngine:
    def __init__(self):
        vs = get_config("vital_signs") or {}
        hr = vs.get("heart_rate", {})
        self._hr_default = hr.get("default", 72.0)
        self._hr_min = hr.get("min", 60.0)
        self._hr_max = hr.get("max", 120.0)

        temp = vs.get("temperature", {})
        self._temp_default = temp.get("default", 36.5)
        self._temp_min = temp.get("min", 36.0)
        self._temp_max = temp.get("max", 37.5)

        br = vs.get("breath_rate", {})
        self._br_default = br.get("default", 16.0)
        self._br_min = br.get("min", 12.0)
        self._br_max = br.get("max", 25.0)

        self._smoothing = vs.get("smoothing_factor", 0.3)
        self._noise_hr = hr.get("noise_amplitude", 2)
        self._noise_temp = temp.get("noise_amplitude", 0.1)
        self._noise_br = br.get("noise_amplitude", 1)

        self._states: dict[str, VitalSignsState] = {}

    def update_on_emotion(self, character_id: str, emotion: str) -> VitalSignsState:
        mapping = EMOTION_VITAL_MAP.get(emotion, EMOTION_VITAL_MAP.get("平静", {}))
        target_hr = self._hr_default + mapping.get("heart_rate_delta", 0)
        target_temp = self._temp_default + mapping.get("temperature_delta", 0)
        target_br = self._br_default + mapping.get("breath_rate_delta", 0)

        current = self._states.get(character_id)
        if current:
            s = self._smoothing
            new_hr = current.heart_rate * (1 - s) + target_hr * s
            new_temp = current.temperature * (1 - s) + target_temp * s
            new_br = current.breath_rate * (1 - s) + target_br * s
        else:
            new_hr = target_hr
            new_temp = target_temp
            new_br = target_br

        new_hr = self._clamp(new_hr, self._hr_min, self._hr_max)
        new_temp = self._clamp(new_temp, self._temp_min, self._temp_max)
        new_br = self._clamp(new_br, self._br_min, self._br_max)

        state = VitalSignsState(
            heart_rate=round(new_hr, 1),
            temperature=round(new_temp, 1),
            breath_rate=round(new_br, 1),
            last_emotion=emotion,
        )
        self._states[character_id] = state
        return state

    def tick(self, character_id: str) -> VitalSignsState:
        current = self._states.get(character_id)
        if not current:
            return self._default_state(character_id)

        noise_hr = random.gauss(0, self._noise_hr)
        noise_temp = random.gauss(0, self._noise_temp)
        noise_br = random.gauss(0, self._noise_br)

        decay = 0.05
        new_hr = current.heart_rate * (1 - decay) + self._hr_default * decay + noise_hr
        new_temp = current.temperature * (1 - decay) + self._temp_default * decay + noise_temp
        new_br = current.breath_rate * (1 - decay) + self._br_default * decay + noise_br

        state = VitalSignsState(
            heart_rate=round(self._clamp(new_hr, self._hr_min, self._hr_max), 1),
            temperature=round(self._clamp(new_temp, self._temp_min, self._temp_max), 1),
            breath_rate=round(self._clamp(new_br, self._br_min, self._br_max), 1),
            last_emotion=current.last_emotion,
        )
        self._states[character_id] = state
        return state

    def get_current(self, character_id: str) -> VitalSignsState:
        return self._states.get(character_id, self._default_state(character_id))

    def format_wechat_message(self, character_id: str) -> str:
        state = self.get_current(character_id)
        return f"❤️ 心率：{state.heart_rate}bpm | 🌡️ 体温：{state.temperature}℃ | 💨 呼吸：{state.breath_rate}次/分"

    def _default_state(self, character_id: str) -> VitalSignsState:
        return VitalSignsState(self._hr_default, self._temp_default, self._br_default, "平静")

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))
