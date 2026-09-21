from .character_config import ConfigLoader
from .emotion_engine import CompoundEmotionalState, Emotion, EmotionEngine
from .emotion_style_coupler import CoupledStyle, EmotionStyleCoupler
from .persona_engine import PersonaEngine, PersonaProfile
from .tone_mimic import ToneMimic

EmotionalState = CompoundEmotionalState

__all__ = [
    "EmotionEngine", "Emotion", "CompoundEmotionalState", "EmotionalState",
    "ToneMimic",
    "PersonaEngine", "PersonaProfile",
    "ConfigLoader",
    "EmotionStyleCoupler", "CoupledStyle",
]
