"""
主动消息V2模块 — 从死板的"闹钟定时"到活生生的"像真人找你"

核心改造：
- 时间漂移：时间段窗口 + 随机偏移
- 人设感知：不同人设不同作息
- 好感度动态：根据好感度调整频率和风格
- 勿扰模式：时间段屏蔽 + 智能检测
"""

from .affinity_adapter import AffinityAdapter
from .content_generator import ContentGenerator
from .dnd_guard import DndGuard
from .time_drifter import TimeDrifter

__all__ = ["TimeDrifter", "DndGuard", "AffinityAdapter", "ContentGenerator"]
