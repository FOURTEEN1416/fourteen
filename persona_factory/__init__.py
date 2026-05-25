"""
人设工厂模块 — 从"写死的十四"到"3分钟造出任何人"

支持三种创建方式：
1. YAML/JSON手写导入
2. 微信聊天克隆
3. LLM对话式创建
"""

from .creator import PersonaCreator
from .factory import PersonaFactory
from .manager import PersonaManager
from .switcher import PersonaSwitcher

__all__ = ["PersonaFactory", "PersonaManager", "PersonaCreator", "PersonaSwitcher"]
