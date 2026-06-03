"""
WeClone 适配器 — 将 WeChatMsg 数据提取桥接到 唯一的你 项目

集成 LC044/WeChatMsg 的数据导出功能，直接送入 clone_training 管线。

管线:
    WeChatMsg 导出 (SQLite→JSON)
        → weclone_adapter (格式适配+清洗)
        → clone_training.DataExtractor
        → clone_training.StyleAnalyzer
        → clone_training.DatasetBuilder
        → clone_training.LoRATrainer
        → 风格注入 ToneMimic / LoRA 模型
"""

from .adapter import WeCloneAdapter
from .style_profiler import StyleProfiler

__all__ = ["WeCloneAdapter", "StyleProfiler"]
