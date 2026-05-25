"""
人设工厂核心 — 创建、加载、管理人设卡
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from my_character.persona_card_v3 import PersonaCardV3

logger = logging.getLogger("persona_factory")


class PersonaFactory:
    """
    人设工厂

    支持三种创建方式：
    1. from_yaml() - YAML/JSON文件导入
    2. from_wechat_clone() - 微信聊天记录克隆
    3. from_llm_dialog() - LLM对话式创建
    """

    def __init__(self, persona_dir: str = "persona_cards"):
        self.persona_dir = Path(persona_dir)
        self.persona_dir.mkdir(parents=True, exist_ok=True)
        self._active_persona: PersonaCardV3 | None = None
        self._personas: dict[str, PersonaCardV3] = {}
        self._load_all_personas()

    def _load_all_personas(self) -> None:
        """加载所有人设卡"""
        for yaml_file in self.persona_dir.glob("*.yaml"):
            try:
                persona = PersonaCardV3.from_yaml(yaml_file)
                self._personas[persona.name] = persona
                logger.info("Loaded persona: %s from %s", persona.name, yaml_file)
            except Exception as e:  # noqa: BLE001

                logger.warning("Failed to load %s: %s", yaml_file, e)

        # 如果没有人设卡，创建默认人设
        if not self._personas:
            default = PersonaCardV3()
            self._personas[default.name] = default
            self._active_persona = default
            logger.info("Created default persona: %s", default.name)

    def get_active(self) -> PersonaCardV3:
        """获取当前活跃人设"""
        if self._active_persona is None:
            self._active_persona = list(self._personas.values())[0] if self._personas else PersonaCardV3()
        return self._active_persona

    def set_active(self, name: str) -> bool:
        """设置活跃人设"""
        if name in self._personas:
            self._active_persona = self._personas[name]
            logger.info("Switched to persona: %s", name)
            return True
        logger.warning("Persona not found: %s", name)
        return False

    def list_personas(self) -> list[dict[str, Any]]:
        """列出所有人设"""
        return [
            {
                "name": p.name,
                "archetype": p.archetype,
                "is_active": p == self._active_persona,
            }
            for p in self._personas.values()
        ]

    @classmethod
    def from_yaml(cls, path: str | Path) -> PersonaCardV3:
        """从YAML文件创建人设卡"""
        return PersonaCardV3.from_yaml(path)

    @classmethod
    def from_dict(cls, data: dict) -> PersonaCardV3:
        """从字典创建人设卡"""
        return PersonaCardV3.from_dict(data)

    def create_and_save(self, persona: PersonaCardV3, filename: str | None = None) -> bool:
        """创建并保存人设卡"""
        filename = filename or f"{persona.name}.yaml"
        path = self.persona_dir / filename
        if persona.to_yaml(path):
            self._personas[persona.name] = persona
            logger.info("Created and saved persona: %s", persona.name)
            return True
        return False

    def delete(self, name: str) -> bool:
        """删除人设卡"""
        if name not in self._personas:
            return False

        self._personas[name]
        # 尝试删除文件
        for yaml_file in self.persona_dir.glob("*.yaml"):
            try:
                p = PersonaCardV3.from_yaml(yaml_file)
                if p.name == name:
                    yaml_file.unlink()
                    break  # noqa: BLE001

            except Exception as e:  # noqa: BLE001

                logger.debug("Error: %s", e)


        del self._personas[name]
        if self._active_persona and self._active_persona.name == name:
            self._active_persona = list(self._personas.values())[0] if self._personas else None

        logger.info("Deleted persona: %s", name)
        return True

    def get(self, name: str) -> PersonaCardV3 | None:
        """获取指定人设"""
        return self._personas.get(name)
