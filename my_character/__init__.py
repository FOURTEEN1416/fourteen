# AI女友"小暖" — 性格系统
from .emotion_engine import EmotionEngine, Emotion, EmotionalState
from .emotion_engine_v2 import EmotionEngineV2, CompoundEmotionalState
from .tone_mimic import ToneMimic
from .persona import PersonaEngine
from .persona_engine_v2 import PersonaEngineV2
from .character_config import ConfigLoader

__all__ = [
    "EmotionEngine", "Emotion", "EmotionalState",
    "EmotionEngineV2", "CompoundEmotionalState",
    "ToneMimic",
    "PersonaEngine",
    "PersonaEngineV2",
    "ConfigLoader",
]
