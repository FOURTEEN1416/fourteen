"""
PersonaExtractor — AI女友小暖的人格克隆模块

融合 Agethos (数据结构) + PADO (检测prompt) + 原系统(运行时) 三方方案。

模块架构:
  models.py           OCEAN+PAD+风格向量数据模型 (Agethos继承)
  pado_detector.py    PADO风格多智能体OCEAN检测 (无需训练)
  style_vectorizer.py 5维风格向量提取 (增强ToneMimic)
  persona_bank.py     用户人格画像SQLite持久化存储
  emotion_coupler.py  PAD 3轴情感耦合器 (连接EmotionEngine)
  fusion.py           统一融合适配器 (对外主入口)

用法:
    from persona_extractor import PersonaExtractor
    pe = PersonaExtractor(llm_gateway=llm, db_path="./data/sqlite.db")
    await pe.initialize()
    enhancement = await pe.process_message("你好呀！")
    # enhancement → 可注入 system_prompt 的人格增强段
"""

from __future__ import annotations

from .emotion_coupler import EmotionCoupler
from .fusion import PersonaExtractor
from .models import (
    PAD_EMOTION_MAP,
    OceanTraits,
    PadState,
    StyleVector,
    UserPersona,
    UserPersonaSnapshot,
)
from .pado_detector import PADODetector
from .persona_bank import UserPersonaBank
from .style_vectorizer import StyleVectorizer

__all__ = [
    # 主入口
    "PersonaExtractor",
    # 子组件
    "PADODetector",
    "UserPersonaBank",
    "StyleVectorizer",
    "EmotionCoupler",
    # 数据模型
    "OceanTraits",
    "PadState",
    "StyleVector",
    "UserPersona",
    "UserPersonaSnapshot",
    "PAD_EMOTION_MAP",
]

__version__ = "1.0.0"
