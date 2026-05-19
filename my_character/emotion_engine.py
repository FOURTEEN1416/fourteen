"""
情感状态机引擎 — 参考 Shikigami-Protocol 情感×能量×好感度三层模型

管理 AI 女友"小暖"的实时情感状态：
- Emotion: 10种情感分类
- Energy: 0~1 能量系统（聊天消耗，恢复）
- Affinity: 0~8 好感度阶梯
- Intensity: 0~1 当前情绪强度
"""

from __future__ import annotations

import enum
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("emotion_engine")


class Emotion(enum.Enum):
    """10种情感状态"""
    HAPPY = "开心"          # 高能量+高愉悦
    SAD = "伤心"            # 低能量+低愉悦
    ANGRY = "生气"          # 高能量+低愉悦
    LOVELY = "撒娇"         # 中能量+高愉悦（亲密专属）
    JEALOUS = "吃醋"        # 中能量+中愉悦
    SULLEN = "傲娇"         # 高能量+表面低愉悦
    CARING = "温柔"         # 低能量+高愉悦
    PLAYFUL = "调皮"        # 高能量+中愉悦
    TIRED = "疲惫"          # 低能量+中性
    NEUTRAL = "平常"        # 中性


class AffinityLevel:
    """好感度阶梯（0~8级）"""
    LEVELS = [
        "陌生人",    # 0
        "认识",      # 1
        "朋友",      # 2
        "好朋友",    # 3
        "知己",      # 4
        "暧昧",      # 5
        "恋人",      # 6
        "热恋",      # 7
        "羁绊",      # 8
    ]

    @staticmethod
    def get_name(level: int) -> str:
        """获取好感度等级名称"""
        if 0 <= level < len(AffinityLevel.LEVELS):
            return AffinityLevel.LEVELS[level]
        return f"未知({level})"

    @staticmethod
    def is_intimate(level: int) -> bool:
        """是否达到亲密关系（>=恋人）"""
        return level >= 6

    @staticmethod
    def is_friend(level: int) -> bool:
        """是否达到朋友关系（>=朋友）"""
        return level >= 2


@dataclass
class EmotionalState:
    """完整的情感状态"""
    emotion: Emotion = Emotion.NEUTRAL
    energy: float = 1.0         # 0.0 ~ 1.0
    affinity: int = 0           # 0~8 好感度等级
    intensity: float = 0.5      # 0.0 ~ 1.0 当前情绪强度
    affection_points: float = 0.0  # 好感度累积点数（用于升级判定）

    def to_prompt_segment(self) -> str:
        """将当前情感状态转换为影响回复的 prompt 段"""
        level_name = AffinityLevel.get_name(self.affinity)
        return (
            f"[当前情感: {self.emotion.value}(强度{self.intensity:.1f}) | "
            f"能量: {self.energy:.1f} | "
            f"关系: {level_name}]"
        )

    def is_low_energy(self) -> bool:
        """是否低能量状态"""
        return self.energy < 0.2

    def is_high_affinity(self) -> bool:
        """是否高好感度"""
        return self.affinity >= 6


class EmotionEngine:
    """情感状态机引擎"""

    # 各情感的愉悦度（1.0=高愉悦, -1.0=低愉悦）
    PLEASURE_MAP = {
        Emotion.HAPPY: 1.0,
        Emotion.SAD: -0.8,
        Emotion.ANGRY: -0.6,
        Emotion.LOVELY: 0.9,
        Emotion.JEALOUS: 0.1,
        Emotion.SULLEN: -0.2,
        Emotion.CARING: 0.7,
        Emotion.PLAYFUL: 0.6,
        Emotion.TIRED: -0.3,
        Emotion.NEUTRAL: 0.0,
    }

    def __init__(self, config: Optional[dict] = None):
        self.state = EmotionalState()
        self.config = config or {}
        self._total_chats = 0
        logger.info("EmotionEngine initialized")

    def process_message(self, user_message: str, context: Optional[dict] = None) -> EmotionalState:
        if context is None:
            context = {}

        detected_emotion = self._classify_emotion(user_message, context)
        delta_energy = self._calc_energy_delta(user_message, detected_emotion)
        delta_affection = self._calc_affection_delta(user_message)

        self.state.intensity *= 0.95

        self.state.emotion = detected_emotion
        self.state.energy = max(0.0, min(1.0, self.state.energy + delta_energy))
        self.state.affection_points += delta_affection
        self._total_chats += 1

        self._check_affinity_upgrade()
        self._check_affinity_downgrade()

        self.state.intensity = min(1.0, self.state.intensity + 0.15)

        return self.state

    def analyze(self, user_message: str, context: str = "") -> EmotionalState:
        return self.process_message(user_message, context if isinstance(context, dict) else {"recent": context})

    def _classify_emotion(self, message: str, context: dict) -> Emotion:
        """
        基于关键词规则的情感分类
        实际使用时可以用 LLM 替代
        """
        msg = message.lower()

        # 亲密/撒娇关键词
        if any(kw in msg for kw in ["想你了", "抱抱", "亲", "想你", "爱你", "么么"]):
            if self.state.affinity >= 5:
                return Emotion.LOVELY
            return Emotion.HAPPY

        # 负面关键词
        if any(kw in msg for kw in ["生气", "不理你", "哼", "讨厌"]):
            return Emotion.ANGRY

        # 伤心关键词
        if any(kw in msg for kw in ["难过", "伤心", "哭了", "不开心", "委屈"]):
            return Emotion.SAD

        # 吃醋关键词
        if any(kw in msg for kw in ["谁啊", "她是谁", "那个人", "女生"]):
            return Emotion.JEALOUS

        # 关心关键词
        if any(kw in msg for kw in ["吃饭", "睡觉", "休息", "累了", "注意"]):
            return Emotion.CARING

        # 调情/玩笑
        if any(kw in msg for kw in ["夸", "好看", "漂亮", "可爱", "乖"]):
            if random.random() < 0.4:
                return Emotion.SULLEN  # 傲娇
            return Emotion.HAPPY

        # 疲惫
        if any(kw in msg for kw in ["好累", "加班", "忙", "熬夜"]):
            return Emotion.CARING

        # 默认：基于当前能量和好感度
        if self.state.energy > 0.7 and self.state.affinity >= 4:
            return Emotion.PLAYFUL
        if self.state.energy < 0.3:
            return Emotion.TIRED

        return Emotion.NEUTRAL

    def _calc_energy_delta(self, message: str, emotion: Emotion) -> float:
        """计算能量变化"""
        # 每次回复消耗基础能量
        delta = -self.config.get("energy_drain_per_message", 0.02)

        # 积极情感回复部分恢复能量
        pleasure = self.PLEASURE_MAP.get(emotion, 0.0)
        if pleasure > 0.5:
            delta += 0.03  # 开心的回复让你"有能量"
        elif pleasure < -0.5:
            delta -= 0.02  # 负面对话消耗更多

        # 长时间不聊自动恢复（由 scheduler 处理）
        return delta

    def _calc_affection_delta(self, message: str) -> float:
        """计算好感度点数变化"""
        msg = message.lower()

        # 积极互动增加好感
        positive_signals = [
            "想", "喜欢", "爱", "好", "乖", "棒",
            "对不起", "错了", "哄", "宝贝", "亲爱的",
        ]
        negative_signals = [
            "烦", "滚", "闭嘴", "懒得", "无语",
        ]

        pos_count = sum(1 for kw in positive_signals if kw in msg)
        neg_count = sum(1 for kw in negative_signals if kw in msg)

        delta = 0.0
        if pos_count > 0:
            delta += pos_count * self.config.get("per_positive_reply", 1.0)
        if neg_count > 0:
            delta += neg_count * self.config.get("per_negative_reply", -0.5)

        # 对话本身增加微量好感
        delta += 0.2

        return delta

    def _check_affinity_upgrade(self) -> None:
        """检查好感度是否达到升级阈值"""
        # 每个等级所需好感度（指数增长）
        thresholds = [0, 10, 25, 50, 80, 120, 200, 350, 500]
        current_level = self.state.affinity

        while current_level < len(thresholds) - 1:
            next_threshold = thresholds[current_level + 1]
            if self.state.affection_points >= next_threshold:
                current_level += 1
                logger.info(
                    "好感度升级! %s → %s",
                    AffinityLevel.get_name(current_level - 1),
                    AffinityLevel.get_name(current_level),
                )
            else:
                break

        self.state.affinity = current_level

    def _check_affinity_downgrade(self) -> None:
        thresholds = [0, 10, 25, 50, 80, 120, 200, 350, 500]
        current_level = self.state.affinity
        while current_level > 0:
            current_threshold = thresholds[current_level]
            if self.state.affection_points < current_threshold:
                current_level -= 1
                logger.info(
                    "好感度降级! %s → %s",
                    AffinityLevel.get_name(current_level + 1),
                    AffinityLevel.get_name(current_level),
                )
            else:
                break
        self.state.affinity = current_level

    def apply_time_decay(self, hours_passed: float) -> None:
        """
        应用时间衰减（由 scheduler 定期调用）

        Args:
            hours_passed: 经过的小时数
        """
        # 能量自然恢复
        recovery = self.config.get("energy_recovery_per_hour", 0.05) * hours_passed
        self.state.energy = min(1.0, self.state.energy + recovery)

        # 强度衰减
        decay = self.config.get("intensity_per_minute", 0.001) * hours_passed * 60
        self.state.intensity = max(0.1, self.state.intensity - decay)

        # 好感度长期衰减
        days_passed = hours_passed / 24
        if days_passed >= 1:
            decay_affection = self.config.get("per_day_decay", 0.1) * days_passed
            self.state.affection_points = max(0, self.state.affection_points - decay_affection)
            self._check_affinity_upgrade()
            self._check_affinity_downgrade()

    def get_style_modifiers(self) -> dict:
        """
        获取当前状态对回复风格的影响参数

        Returns:
            dict: {warmth_mod, energy_mod, intimacy_mod, playfulness_mod}
        """
        pleasure = self.PLEASURE_MAP.get(self.state.emotion, 0.0)
        return {
            "warmth_mod": max(-0.3, min(0.3, pleasure * 0.3)),
            "energy_mod": self.state.energy - 0.5,  # -0.5~0.5
            "intimacy_mod": self.state.affinity / 8.0 - 0.5,  # -0.5~0.5
            "playfulness_mod": max(-0.3, min(0.3, pleasure * 0.2 + (self.state.energy - 0.5) * 0.3)),
            "needs_comfort": self.state.emotion in (Emotion.SAD, Emotion.TIRED),
            "is_flirty": self.state.emotion in (Emotion.LOVELY, Emotion.PLAYFUL) and self.state.affinity >= 5,
        }

    def get_affinity_level_name(self) -> str:
        """获取当前好感度等级名称"""
        return AffinityLevel.get_name(self.state.affinity)

    def reset(self) -> None:
        """重置情感状态"""
        self.state = EmotionalState()
        self._total_chats = 0
        logger.info("EmotionEngine reset")

    @property
    def total_chats(self) -> int:
        return self._total_chats

    def __repr__(self) -> str:
        return (
            f"EmotionEngine({self.state.emotion.value}, "
            f"energy={self.state.energy:.2f}, "
            f"affinity={self.get_affinity_level_name()}({self.state.affinity}), "
            f"intensity={self.state.intensity:.2f})"
        )
