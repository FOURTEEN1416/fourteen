"""情绪类型枚举"""

from enum import IntEnum, auto


class EmotionType(IntEnum):
    NEUTRAL = auto()
    HAPPY = auto()
    SAD = auto()
    ANGRY = auto()
    LOVELY = auto()
    JEALOUS = auto()
    SULLEN = auto()
    CARING = auto()
    PLAYFUL = auto()
    TIRED = auto()
