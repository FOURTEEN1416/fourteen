"""知识宝库（Knowledge Vault）—— 角色知识自动收集与注入系统。

负责：
1. 从 CharaCardV2 提取 persona 特征（_persona_adapter）
2. 定时/事件驱动知识收集（collect_loop）
3. 将收集的知识注入 CharacterKnowledgeService
"""

from ._persona_adapter import PersonaAdapter, PersonaFeatures
from .collect_loop import CollectLoop, VaultCollector

__all__ = [
    "PersonaAdapter",
    "PersonaFeatures",
    "CollectLoop",
    "VaultCollector",
]
