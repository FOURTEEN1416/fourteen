"""好感度等级枚举"""

from enum import IntEnum


class AffinityLevel(IntEnum):
    STRANGER = 0
    ACQUAINTANCE = 1
    FRIEND = 2
    GOOD_FRIEND = 3
    CONFIDANT = 4
    AMBIGUOUS = 5
    LOVER = 6
    PASSIONATE = 7
    BOND = 8

    @property
    def display_name(self) -> str:
        names = ["陌生人", "认识", "朋友", "好朋友", "知己", "暧昧", "恋人", "热恋", "羁绊"]
        return names[self.value]

    @property
    def threshold(self) -> int:
        thresholds = [0, 10, 25, 50, 80, 120, 200, 350, 500]
        return thresholds[self.value]
