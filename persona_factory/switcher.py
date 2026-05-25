"""
人设切换器 — 处理人设切换的API和事件
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from my_character.persona_card_v3 import PersonaCardV3
from persona_factory.creator import PersonaCreator

logger = logging.getLogger("persona_switcher")


class PersonaSwitcher:
    """
    人设切换器

    提供：
    - 切换API
    - 切换事件回调
    - 切换验证
    """

    def __init__(
        self,
        factory: PersonaCreator,
        on_switch: Callable[[str, str], None] | None = None,
    ):
        self._factory = factory
        self._on_switch = on_switch
        self._validators: list[Callable[[PersonaCardV3], bool]] = []

    def add_validator(self, validator: Callable[[PersonaCardV3], bool]) -> None:
        """添加切换验证器"""
        self._validators.append(validator)

    def can_switch_to(self, target_name: str) -> tuple[bool, str]:
        """
        检查是否可以切换到目标人设

        Returns:
            (是否可以切换, 原因)
        """
        target = self._factory.get(target_name)  # type: ignore[attr-defined]
        if target is None:
            return False, f"人设 '{target_name}' 不存在"

        # 运行验证器
        for validator in self._validators:
            if not validator(target):
                return False, "验证失败"

        return True, "可以切换"

    def switch(self, target_name: str) -> dict[str, Any]:
        """
        切换到目标人设

        Args:
            target_name: 目标人设名称

        Returns:
            切换结果
        """
        can_switch, reason = self.can_switch_to(target_name)
        if not can_switch:
            return {"success": False, "message": reason}

        current = self._factory.get_active()  # type: ignore[attr-defined]
        current_name = current.name if current else None

        success = self._factory.set_active(target_name)  # type: ignore[attr-defined]

        if success:
            # 触发回调
            if self._on_switch and current_name:
                self._on_switch(current_name, target_name)

            logger.info("Persona switched: %s -> %s", current_name, target_name)
            return {
                "success": True,
                "from": current_name,
                "to": target_name,
                "message": f"已切换到 {target_name}",
            }

        return {"success": False, "message": "切换失败"}

    def get_available_targets(self) -> list[str]:
        """获取可切换的目标人设列表"""
        current = self._factory.get_active()  # type: ignore[attr-defined]
        current_name = current.name if current else None

        return [
            p["name"]
            for p in self._factory.list_personas()  # type: ignore[attr-defined]
            if p["name"] != current_name
        ]

    def get_switch_context(self) -> dict[str, Any]:
        """获取切换上下文（用于前端渲染）"""
        current = self._factory.get_active()  # type: ignore[attr-defined]
        return {
            "current_persona": {
                "name": current.name,
                "archetype": current.archetype,
                "summary": current.get_personality_summary(),
            },
            "available_targets": self.get_available_targets(),
            "all_personas": self._factory.list_personas(),  # type: ignore[attr-defined]
        }
