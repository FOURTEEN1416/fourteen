"""
角色卡验证器

在加载角色卡时进行完整性检查，避免有缺陷的角色卡影响对话系统
"""

from __future__ import annotations

from typing import List, Tuple

from .models import CharacterCard


class CharacterValidator:
    """
    角色卡验证器 - 轻量级，所有检查不涉及外部调用

    验证项:
      1. 必填字段检查 (name, description)
      2. 数值范围检查 (talkativeness 0-1)
      3. 角色书条目完整性
      4. 示例消息格式检查
    """

    REQUIRED_FIELDS = ["name", "description"]
    MAX_NAME_LENGTH = 100
    MAX_DESC_LENGTH = 50000
    MAX_EXAMPLE_LENGTH = 100000

    @classmethod
    def validate(cls, card: CharacterCard) -> Tuple[bool, List[str]]:
        """
        验证角色卡完整性

        Returns:
            (is_valid, error_messages)
        """
        errors = []

        # 1. 必填字段检查
        errors.extend(cls._check_required_fields(card))
        # 2. 长度检查
        errors.extend(cls._check_lengths(card))
        # 3. 数值范围检查
        errors.extend(cls._check_ranges(card))
        # 4. 角色书检查
        errors.extend(cls._check_world_info(card))
        # 5. 示例消息检查
        errors.extend(cls._check_examples(card))

        return len(errors) == 0, errors

    @classmethod
    def is_valid(cls, card: CharacterCard) -> bool:
        """快速检查是否有效（不返回错误详情）"""
        valid, _ = cls.validate(card)
        return valid

    @classmethod
    def _check_required_fields(cls, card: CharacterCard) -> List[str]:
        errors = []
        for field in cls.REQUIRED_FIELDS:
            value = getattr(card.data, field, "")
            if not value or not str(value).strip():
                errors.append(f"缺少必填字段: {field}")
        return errors

    @classmethod
    def _check_lengths(cls, card: CharacterCard) -> List[str]:
        errors = []
        if len(card.data.name) > cls.MAX_NAME_LENGTH:
            errors.append(f"角色名过长 ({len(card.data.name)} > {cls.MAX_NAME_LENGTH})")
        if len(card.data.description) > cls.MAX_DESC_LENGTH:
            errors.append(f"角色描述过长 ({len(card.data.description)} > {cls.MAX_DESC_LENGTH} 字符)")
        if card.data.mes_example and len(card.data.mes_example) > cls.MAX_EXAMPLE_LENGTH:
            errors.append(f"示例消息过长 ({len(card.data.mes_example)} > {cls.MAX_EXAMPLE_LENGTH} 字符)")
        return errors

    @classmethod
    def _check_ranges(cls, card: CharacterCard) -> List[str]:
        errors = []
        t = card.data.extensions.talkativeness
        if not 0 <= t <= 1:
            errors.append(f"talkativeness 必须在 0-1 之间，当前为 {t}")
        return errors

    @classmethod
    def _check_world_info(cls, card: CharacterCard) -> List[str]:
        errors = []
        if card.data.character_book:
            for entry in card.data.character_book.entries:
                if not entry.keys:
                    errors.append(f"角色书条目 #{entry.id} 缺少触发关键词")
                if not entry.content:
                    errors.append(f"角色书条目 #{entry.id} 缺少内容")
        return errors

    @classmethod
    def _check_examples(cls, card: CharacterCard) -> List[str]:
        errors = []
        if card.data.mes_example:
            if "{{char}}" not in card.data.mes_example and "{{user}}" not in card.data.mes_example:
                errors.append("示例消息中未使用 {{char}}/{{user}} 占位符")
        return errors
