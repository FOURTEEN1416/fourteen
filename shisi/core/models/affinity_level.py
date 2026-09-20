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
        """该档最小 affection_points（权威刻度 0–500 的分界表）。

        换算一律走 `shisi.affinity.scale`，禁止在业务代码内再写一份阈值。
        """
        thresholds = [0, 10, 25, 50, 80, 120, 200, 350, 500]
        return thresholds[self.value]

    @classmethod
    def get_name(cls, level: int) -> str:
        try:
            return cls(max(0, min(8, int(level)))).display_name
        except ValueError:
            return cls.STRANGER.display_name
