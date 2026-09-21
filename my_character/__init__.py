from .character_config import ConfigLoader
from .emotion_engine import CompoundEmotionalState, Emotion, EmotionEngine
from .emotion_style_coupler import CoupledStyle, EmotionStyleCoupler
from .persona_engine import PersonaEngine, PersonaProfile
from .style_enhancer import EnhancedStyle, StyleEnhancer
from .tone_mimic import ToneMimic

EmotionEngineV2 = EmotionEngine
EmotionEngineOptimized = EmotionEngine
EmotionalState = CompoundEmotionalState
PersonaEngineV2 = PersonaEngine
PersonaEngineOptimized = PersonaEngine

__all__ = [
    "EmotionEngine", "Emotion", "CompoundEmotionalState",
    "EmotionEngineV2", "EmotionEngineOptimized", "EmotionalState",
    "ToneMimic",
    "PersonaEngine", "PersonaProfile",
    "PersonaEngineV2", "PersonaEngineOptimized",
    "ConfigLoader",
    "EmotionStyleCoupler", "CoupledStyle",
    "StyleEnhancer", "EnhancedStyle",
]
