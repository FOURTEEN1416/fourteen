"""
角色卡系统 - SillyTavern角色卡格式兼容模块

支持:
  - V1/V2/V3 角色卡格式解析
  - PNG嵌入角色卡解析（无需pypng，使用struct解析）
  - JSON角色卡加载
  - 角色卡 → PersonaEngine配置转换
  - 角色验证与提示词构建

使用方式:
    from character_card import CharacterCardParser, PromptBuilder

    # 从JSON加载
    card = CharacterCardParser.parse_json(json_str)

    # 从PNG加载
    card = CharacterCardParser.parse_png(png_bytes)

    # 转换为PersonaEngine配置
    config = card.to_persona_config()

    # 构建系统提示词
    prompt = PromptBuilder().build_system_prompt(card)
"""

from .models import (
    CardVersion,
    CharacterCard,
    CharacterData,
    CharacterExtensions,
    EmotionStyleMap,
    WorldInfoBook,
    WorldInfoEntry,
)
from .parser import CharacterCardParser
from .prompt_builder import PromptBuilder
from .validator import CharacterValidator

__all__ = [
    "CardVersion", "CharacterCard", "CharacterData",
    "CharacterExtensions", "WorldInfoBook", "WorldInfoEntry",
    "EmotionStyleMap",
    "CharacterCardParser",
    "CharacterValidator",
    "PromptBuilder",
]
