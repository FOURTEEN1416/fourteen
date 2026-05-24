"""核心领域模型导出"""

from .affinity_level import AffinityLevel
from .character_aggregate import CharacterAggregate
from .character_id import CharacterId
from .emotion_type import EmotionType
from .emotional_state import EmotionalState
from .persona_profile import PersonaProfile

__all__ = [
    "AffinityLevel",
    "CharacterAggregate",
    "CharacterId",
    "EmotionalState",
    "EmotionType",
    "PersonaProfile",
]
