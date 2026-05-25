"""
人设管理器 — 管理人设的切换、记忆保留策略
"""

from __future__ import annotations

import logging
from typing import Any

from my_character.persona_card_v3 import PersonaCardV3
from persona_factory.creator import PersonaCreator

logger = logging.getLogger("persona_manager")


class PersonaManager:
    """
    人设管理器

    负责：
    - 人设切换
    - 记忆保留策略（清空工作记忆，保留长期记忆）
    - 人设状态持久化
    """

    def __init__(self, factory: PersonaCreator):
        self._factory = factory
        self._switch_history: list[dict[str, Any]] = []

    def switch_persona(self, target_name: str, keep_long_term_memory: bool = True) -> dict[str, Any]:
        """
        切换人设

        Args:
            target_name: 目标人设名称
            keep_long_term_memory: 是否保留长期记忆

        Returns:
            切换结果
        """
        current = self._factory.get_active()  # type: ignore[attr-defined]
        if current is None:
            return {"success": False, "message": "No active persona"}
        if current.name == target_name:
            return {"success": True, "message": "Already active", "persona": current.name}

        target = self._factory.get(target_name)  # type: ignore[attr-defined]
        if target is None:
            return {"success": False, "message": f"Persona '{target_name}' not found"}

        # 记录切换历史
        switch_record = {
            "from": current.name,
            "to": target_name,
            "keep_memory": keep_long_term_memory,
        }
        self._switch_history.append(switch_record)

        # 执行切换
        success = self._factory.set_active(target_name)  # type: ignore[attr-defined]

        if success:
            logger.info("Switched persona: %s -> %s", current.name, target_name)
            return {
                "success": True,
                "message": f"Switched from {current.name} to {target_name}",
                "persona": target_name,
                "memory_retained": keep_long_term_memory,
            }

        return {"success": False, "message": "Switch failed"}

    def get_switch_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取切换历史"""
        return self._switch_history[-limit:]

    def get_current_persona(self) -> PersonaCardV3 | None:
        """获取当前人设"""
        return self._factory.get_active()  # type: ignore[attr-defined,no-any-return]

    def get_persona_context(self) -> dict[str, Any]:
        """获取当前人设的上下文信息（用于模板渲染）"""
        persona = self.get_current_persona()
        if persona is None:
            return {}
        return {
            "persona.name": persona.name,
            "persona.archetype": persona.archetype,
            "persona.personality_summary": persona.get_personality_summary(),
            "persona.warmth": persona.personality.warmth,
            "persona.stubbornness": persona.personality.stubbornness,
            "persona.playfulness": persona.personality.playfulness,
            "persona.interests": persona.interests,
            "persona.catchphrases": persona.speaking_style.catchphrases,
        }
