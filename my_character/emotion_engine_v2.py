from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("emotion_engine_v2")


class Emotion(enum.Enum):
    HAPPY = "开心"
    SAD = "伤心"
    ANGRY = "生气"
    LOVELY = "撒娇"
    JEALOUS = "吃醋"
    SULLEN = "傲娇"
    CARING = "温柔"
    PLAYFUL = "调皮"
    TIRED = "疲惫"
    NEUTRAL = "平常"


class AffinityLevel:
    LEVELS = ["陌生人", "认识", "朋友", "好朋友", "知己", "暧昧", "恋人", "热恋", "羁绊"]

    @staticmethod
    def get_name(level: int) -> str:
        if 0 <= level < len(AffinityLevel.LEVELS):
            return AffinityLevel.LEVELS[level]
        return f"未知({level})"

    @staticmethod
    def is_intimate(level: int) -> bool:
        return level >= 6


@dataclass
class CompoundEmotionalState:
    primary_emotion: Emotion = Emotion.NEUTRAL
    primary_intensity: float = 0.5
    secondary_emotions: List[Tuple[Emotion, float]] = field(default_factory=list)
    energy: float = 1.0
    affinity: int = 0
    emotion_vector: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        self.primary_intensity = max(0.0, min(1.0, self.primary_intensity))
        self.energy = max(0.0, min(1.0, self.energy))
        self.affinity = max(0, min(8, self.affinity))
        self.secondary_emotions = [
            (e, max(0.0, min(1.0, i)))
            for e, i in self.secondary_emotions if i >= 0.3
        ][:3]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": {"type": self.primary_emotion.value, "intensity": self.primary_intensity},
            "secondary": [{"type": e.value, "intensity": i} for e, i in self.secondary_emotions],
            "energy": self.energy,
            "affinity": self.affinity,
        }


EMOTION_TRANSITION_MATRIX = {
    (Emotion.NEUTRAL, Emotion.ANGRY): 0.6,
    (Emotion.NEUTRAL, Emotion.JEALOUS): 0.5,
    (Emotion.HAPPY, Emotion.ANGRY): 0.2,
    (Emotion.HAPPY, Emotion.SAD): 0.2,
    (Emotion.ANGRY, Emotion.HAPPY): 0.3,
    (Emotion.SAD, Emotion.HAPPY): 0.3,
    (Emotion.LOVELY, Emotion.JEALOUS): 0.6,
}


class ContinuityGuard:
    def __init__(self, blend_ratio: float = 0.4):
        self.blend_ratio = blend_ratio

    def check_transition(self, old: Emotion, new: Emotion) -> Tuple[bool, Optional[Emotion]]:
        if old == new:
            return True, None
        prob = EMOTION_TRANSITION_MATRIX.get((old, new), 0.5)
        if prob >= 0.4:
            return True, None
        return False, Emotion.NEUTRAL

    def blend_intensity(self, old_intensity: float, new_intensity: float) -> float:
        return old_intensity * self.blend_ratio + new_intensity * (1 - self.blend_ratio)


EMOTION_STYLE_MAP = {
    Emotion.JEALOUS: {"rhetorical_prob": 0.8, "hint_prob": 0.7, "caring_prob": 0.2, "teasing_prob": 0.1, "emoji_freq": 0.3},
    Emotion.SULLEN: {"rhetorical_prob": 0.7, "hint_prob": 0.6, "caring_prob": 0.4, "teasing_prob": 0.3, "emoji_freq": 0.5},
    Emotion.CARING: {"rhetorical_prob": 0.2, "hint_prob": 0.1, "caring_prob": 0.9, "teasing_prob": 0.1, "emoji_freq": 0.4},
    Emotion.HAPPY: {"rhetorical_prob": 0.3, "hint_prob": 0.2, "caring_prob": 0.5, "teasing_prob": 0.6, "emoji_freq": 0.8},
    Emotion.ANGRY: {"rhetorical_prob": 0.9, "hint_prob": 0.3, "caring_prob": 0.1, "teasing_prob": 0.0, "emoji_freq": 0.1},
    Emotion.LOVELY: {"rhetorical_prob": 0.4, "hint_prob": 0.5, "caring_prob": 0.7, "teasing_prob": 0.5, "emoji_freq": 0.9},
    Emotion.PLAYFUL: {"rhetorical_prob": 0.5, "hint_prob": 0.3, "caring_prob": 0.3, "teasing_prob": 0.8, "emoji_freq": 0.7},
    Emotion.SAD: {"rhetorical_prob": 0.3, "hint_prob": 0.4, "caring_prob": 0.6, "teasing_prob": 0.0, "emoji_freq": 0.2},
    Emotion.TIRED: {"rhetorical_prob": 0.2, "hint_prob": 0.1, "caring_prob": 0.5, "teasing_prob": 0.1, "emoji_freq": 0.2},
    Emotion.NEUTRAL: {"rhetorical_prob": 0.3, "hint_prob": 0.2, "caring_prob": 0.4, "teasing_prob": 0.3, "emoji_freq": 0.4},
}


class LLMEmotionClassifier:
    def __init__(self, llm_gateway=None, timeout_ms: int = 500):
        self._llm = llm_gateway
        self.timeout_ms = timeout_ms

    def classify(self, message: str, recent_context: str = "") -> Optional[Dict]:
        if not self._llm:
            return None
        prompt = (
            f"分析以下消息的情感，考虑近几轮对话上下文：\n\n"
            f"近3轮对话：\n{recent_context}\n\n"
            f"当前消息：{message}\n\n"
            f'回复JSON格式：{{"primary": {{"type": "情感类型", "intensity": 0.0-1.0}}, '
            f'"secondary": [{{"type": "情感类型", "intensity": 0.0-1.0}}]}}\n'
            f"情感类型限定：开心/伤心/生气/撒娇/吃醋/傲娇/温柔/调皮/疲惫/平常"
        )
        try:
            response = self._llm.chat(query=prompt, max_tokens=128, temperature=0.1)
            result = json.loads(response)
            return result
        except Exception as e:
            logger.debug("LLM emotion classify failed: %s", e)
            return None


KEYWORD_EMOTION_MAP = {
    "开心": Emotion.HAPPY, "高兴": Emotion.HAPPY, "哈哈": Emotion.HAPPY,
    "伤心": Emotion.SAD, "难过": Emotion.SAD, "哭": Emotion.SAD,
    "生气": Emotion.ANGRY, "讨厌": Emotion.ANGRY, "烦": Emotion.ANGRY,
    "撒娇": Emotion.LOVELY, "抱": Emotion.LOVELY, "亲": Emotion.LOVELY,
    "吃醋": Emotion.JEALOUS, "她是谁": Emotion.JEALOUS, "哦？": Emotion.JEALOUS,
    "哼": Emotion.SULLEN, "才不": Emotion.SULLEN, "不理": Emotion.SULLEN,
    "关心": Emotion.CARING, "注意": Emotion.CARING, "休息": Emotion.CARING,
    "调皮": Emotion.PLAYFUL, "逗": Emotion.PLAYFUL, "猜": Emotion.PLAYFUL,
    "累": Emotion.TIRED, "困": Emotion.TIRED, "疲惫": Emotion.TIRED,
}


class EmotionEngineV2:
    def __init__(self, llm_gateway=None, use_llm: bool = True,
                 blend_ratio: float = 0.4, classifier_timeout_ms: int = 500):
        self._llm = llm_gateway
        self._classifier = LLMEmotionClassifier(llm_gateway, classifier_timeout_ms) if use_llm else None
        self._guard = ContinuityGuard(blend_ratio)
        self._state = CompoundEmotionalState()
        self._style_map = EMOTION_STYLE_MAP

    @property
    def state(self) -> CompoundEmotionalState:
        return self._state

    def analyze(self, message: str, recent_context: str = "") -> CompoundEmotionalState:
        if self._classifier:
            start = time.perf_counter()
            result = self._classifier.classify(message, recent_context)
            if result and time.perf_counter() - start < self._classifier.timeout_ms / 1000:
                new_state = self._parse_llm_result(result)
            else:
                new_state = self._rule_classify(message)
        else:
            new_state = self._rule_classify(message)
        allowed, intermediate = self._guard.check_transition(
            self._state.primary_emotion, new_state.primary_emotion
        )
        if not allowed and intermediate:
            new_state.primary_emotion = intermediate
        new_state.primary_intensity = self._guard.blend_intensity(
            self._state.primary_intensity, new_state.primary_intensity
        )
        self._state = new_state
        return self._state

    def get_style_modifiers(self) -> Dict[str, float]:
        return self._style_map.get(self._state.primary_emotion,
                                    self._style_map[Emotion.NEUTRAL])

    def _rule_classify(self, message: str) -> CompoundEmotionalState:
        for keyword, emotion in KEYWORD_EMOTION_MAP.items():
            if keyword in message:
                return CompoundEmotionalState(
                    primary_emotion=emotion,
                    primary_intensity=0.6,
                    energy=self._state.energy,
                    affinity=self._state.affinity,
                )
        return CompoundEmotionalState(
            primary_emotion=Emotion.NEUTRAL,
            primary_intensity=0.3,
            energy=self._state.energy,
            affinity=self._state.affinity,
        )

    def _parse_llm_result(self, result: Dict) -> CompoundEmotionalState:
        primary = result.get("primary", {})
        emotion_name = primary.get("type", "平常")
        primary_emotion = Emotion.NEUTRAL
        for e in Emotion:
            if e.value == emotion_name:
                primary_emotion = e
                break
        primary_intensity = float(primary.get("intensity", 0.5))
        secondary = []
        for s in result.get("secondary", []):
            s_type = s.get("type", "")
            s_intensity = float(s.get("intensity", 0.0))
            for e in Emotion:
                if e.value == s_type:
                    secondary.append((e, s_intensity))
                    break
        return CompoundEmotionalState(
            primary_emotion=primary_emotion,
            primary_intensity=primary_intensity,
            secondary_emotions=secondary,
            energy=self._state.energy,
            affinity=self._state.affinity,
        )

    def update_energy(self, drain: float = 0.02, recovery: float = 0.05):
        self._state.energy = max(0.0, min(1.0, self._state.energy - drain + recovery))

    def update_affinity(self, delta: int):
        self._state.affinity = max(0, min(8, self._state.affinity + delta))

    def health_check(self) -> dict:
        return {
            "current_emotion": self._state.primary_emotion.value,
            "intensity": self._state.primary_intensity,
            "energy": self._state.energy,
            "affinity": self._state.affinity,
        }
