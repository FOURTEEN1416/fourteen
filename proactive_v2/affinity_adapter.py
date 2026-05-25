"""
好感度适配器 — 根据好感度动态调整消息频率和风格
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("affinity_adapter")


class AffinityLevel(Enum):
    """好感度等级"""
    STRANGER = 0      # 陌生人 (0-2)
    ACQUAINTANCE = 1  # 认识 (3-4)
    FRIEND = 2        # 朋友 (5-6)
    CLOSE = 3         # 亲密 (7-8)


@dataclass
class AffinityConfig:
    """好感度配置"""
    level: AffinityLevel
    messages_per_day: tuple[int, int]  # (最小, 最大)
    style_traits: dict[str, float]
    description: str


class AffinityAdapter:
    """
    好感度适配器

    根据好感度动态调整：
    - 消息频率
    - 语气风格
    - 亲密度表达
    """

    # 好感度配置表
    CONFIGS = {
        AffinityLevel.STRANGER: AffinityConfig(
            level=AffinityLevel.STRANGER,
            messages_per_day=(1, 2),
            style_traits={"formality": 0.8, "intimacy": 0.1, "emoji": 0.2},
            description="礼貌边界感，不会太随意",
        ),
        AffinityLevel.ACQUAINTANCE: AffinityConfig(
            level=AffinityLevel.ACQUAINTANCE,
            messages_per_day=(2, 4),
            style_traits={"formality": 0.5, "intimacy": 0.3, "emoji": 0.4},
            description="随意分享感，开始聊私事",
        ),
        AffinityLevel.FRIEND: AffinityConfig(
            level=AffinityLevel.FRIEND,
            messages_per_day=(4, 8),
            style_traits={"formality": 0.2, "intimacy": 0.6, "emoji": 0.6},
            description="可以开玩笑，会撒娇",
        ),
        AffinityLevel.CLOSE: AffinityConfig(
            level=AffinityLevel.CLOSE,
            messages_per_day=(8, 16),
            style_traits={"formality": 0.0, "intimacy": 0.9, "emoji": 0.8},
            description="撒娇、吐槽、深夜emo都可以",
        ),
    }

    def __init__(self, affinity_max: int = 8):
        self._affinity_max = affinity_max

    def get_level(self, affinity: int) -> AffinityLevel:
        """
        获取好感度等级

        Args:
            affinity: 好感度值 (0-8)

        Returns:
            好感度等级
        """
        if affinity <= 2:
            return AffinityLevel.STRANGER
        elif affinity <= 4:
            return AffinityLevel.ACQUAINTANCE
        elif affinity <= 6:
            return AffinityLevel.FRIEND
        else:
            return AffinityLevel.CLOSE

    def get_config(self, affinity: int) -> AffinityConfig:
        """获取好感度配置"""
        level = self.get_level(affinity)
        return self.CONFIGS[level]

    def get_messages_per_day(self, affinity: int) -> int:
        """
        获取每日消息数

        Args:
            affinity: 好感度值

        Returns:
            每日消息数
        """
        import random
        config = self.get_config(affinity)
        return random.randint(*config.messages_per_day)

    def get_style_traits(self, affinity: int) -> dict[str, float]:
        """
        获取风格特征

        Args:
            affinity: 好感度值

        Returns:
            风格特征字典
        """
        return self.get_config(affinity).style_traits

    def should_send_message(
        self,
        affinity: int,
        messages_sent_today: int,
    ) -> bool:
        """
        判断是否应该发送消息

        Args:
            affinity: 好感度值
            messages_sent_today: 今日已发送消息数

        Returns:
            是否应该发送
        """
        config = self.get_config(affinity)
        return messages_sent_today < config.messages_per_day[1]

    def get_message_interval(self, affinity: int) -> int:
        """
        获取消息间隔（分钟）

        Args:
            affinity: 好感度值

        Returns:
            最小间隔分钟数
        """
        # 好感度越高，间隔越短
        base_interval = 120  # 基础间隔2小时
        reduction = affinity * 10  # 每级好感度减少10分钟
        return max(30, base_interval - reduction)

    def adapt_content(
        self,
        content: str,
        affinity: int,
    ) -> str:
        """
        根据好感度调整内容

        Args:
            content: 原始内容
            affinity: 好感度值

        Returns:
            调整后的内容
        """
        traits = self.get_style_traits(affinity)

        # 根据亲密度调整
        if traits["intimacy"] > 0.7:
            # 高亲密度：可以添加亲密表达
            pass
        elif traits["intimacy"] < 0.3:
            # 低亲密度：保持礼貌
            pass

        return content

    def get_description(self, affinity: int) -> str:
        """获取好感度描述"""
        return self.get_config(affinity).description
