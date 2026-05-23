"""
系统提示词构建器 - 将角色卡转换为LLM提示词

复用SillyTavern的提示词构建逻辑，输出格式兼容十四的PersonaEngine
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from .models import CharacterCard

logger = logging.getLogger("character_card.prompt_builder")


class PromptBuilder:
    """
    系统提示词构建器

    将SillyTavern角色卡转换为LLM系统提示词，输出格式与十四PersonaEngine兼容

    构建顺序:
      1. 主提示词 (写回复的指令)
      2. 角色描述
      3. 角色性格
      4. 场景设定
      5. 系统指令
      6. 历史处理指令
      7. 角色书（常量条目）
    """

    DEFAULT_MAIN_PROMPT = "Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}."

    def __init__(self, char_name: str = "Character", user_name: str = "User"):
        self.char_name = char_name
        self.user_name = user_name

    def build_system_prompt(self, card: CharacterCard) -> str:
        """
        构建完整系统提示词

        Args:
            card: 角色卡对象

        Returns:
            格式化的系统提示词字符串
        """
        parts = []

        # 1. 主提示词 - 角色扮演指令
        main = self.DEFAULT_MAIN_PROMPT
        main = main.replace("{{char}}", card.data.name).replace("{{user}}", self.user_name)
        parts.append(main)

        # 2. 角色描述
        if card.data.description:
            parts.append(f"[Character Description]\n{card.data.description}")

        # 3. 角色性格
        if card.data.personality:
            parts.append(f"[Character Personality]\n{card.data.personality}")

        # 4. 场景设定
        if card.data.scenario:
            parts.append(f"[Scenario]\n{card.data.scenario}")

        # 5. 系统指令
        if card.data.system_prompt:
            parts.append(f"[System Instructions]\n{card.data.system_prompt}")

        # 6. 历史处理指令
        if card.data.post_history_instructions:
            parts.append(f"[Post History Instructions]\n{card.data.post_history_instructions}")

        # 7. 角色书（仅常量条目，非常量条目由WorldInfo系统动态注入）
        if card.data.character_book and card.data.character_book.entries:
            constant_entries = [e for e in card.data.character_book.entries if e.enabled and e.constant]
            if constant_entries:
                book_parts = []
                for e in constant_entries:
                    label = e.comment or (e.keys[0] if e.keys else "entry")
                    book_parts.append(f"{label}: {e.content}")
                parts.append("[Character Book]\n" + "\n\n".join(book_parts))

        return "\n\n".join(parts)

    def build_example_messages(self, card: CharacterCard) -> List[Dict[str, str]]:
        """
        构建示例对话（few-shot示例）

        Returns:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        examples = []
        if not card.data.mes_example:
            return examples

        lines = card.data.mes_example.split('\n')
        current_role = None
        current_content = []

        for line in lines:
            line = line.strip()
            if line == '<START>':
                continue

            user_match = re.match(r'\{\{user\}\}:\s*(.+)', line)
            char_match = re.match(r'\{\{char\}\}:\s*(.+)', line)

            if user_match:
                if current_role and current_content:
                    examples.append({"role": current_role, "content": '\n'.join(current_content)})
                current_role = "user"
                current_content = [user_match.group(1)]
            elif char_match:
                if current_role and current_content:
                    examples.append({"role": current_role, "content": '\n'.join(current_content)})
                current_role = "assistant"
                current_content = [char_match.group(1)]
            elif line:
                current_content.append(line)

        if current_role and current_content:
            examples.append({"role": current_role, "content": '\n'.join(current_content)})

        return examples

    def merge_with_persona_prompt(self,
                                   card: CharacterCard,
                                   existing_prompt: str,
                                   mode: str = "append") -> str:
        """
        将角色卡提示词合并到现有PersonaEngine提示词

        Args:
            card: 角色卡
            existing_prompt: PersonaEngine已有的提示词
            mode: 'append' - 追加到末尾, 'replace' - 完全替换, 'merge' - 智能合并

        Returns:
            合并后的提示词
        """
        if mode == "replace":
            return self.build_system_prompt(card)
        elif mode == "merge":
            card_prompt = self.build_system_prompt(card)
            return f"{existing_prompt}\n\n[Character Card]\n{card_prompt}"
        else:  # append
            card_prompt = self.build_system_prompt(card)
            return f"{existing_prompt}\n\n{card_prompt}"
