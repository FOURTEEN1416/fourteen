"""
知识注入器 — 将知识注入到对话上下文
"""

from __future__ import annotations

import logging
from typing import Any

from my_character.persona_card import PersonaCardV3

from .knowledge_store import KnowledgeStore

logger = logging.getLogger("knowledge_injector")


class KnowledgeInjector:
    """
    知识注入器

    在对话时自动注入相关知识到系统提示词
    """

    def __init__(self, store: KnowledgeStore):
        self._store = store
        self._max_inject = 3  # 最多注入3条知识
        self._relevance_threshold = 0.5

    def inject(
        self,
        user_message: str,
        persona: PersonaCardV3,
        context: dict[str, Any] | None = None,
    ) -> str:
        """
        根据用户消息注入相关知识

        Args:
            user_message: 用户消息
            persona: 当前人设
            context: 额外上下文

        Returns:
            注入的知识文本（用于添加到系统提示词）
        """
        # 搜索相关知识
        results = self._store.search(
            persona_name=persona.name,
            query=user_message,
            limit=self._max_inject,
        )

        if not results:
            return ""

        # 构建注入文本
        lines = ["【我知道的一些事】"]
        for i, r in enumerate(results[:self._max_inject], 1):
            content = r.get("content", "")
            if len(content) > 100:
                content = content[:100] + "..."
            lines.append(f"{i}. {content}")

        return "\n".join(lines)

    def inject_recent(
        self,
        persona: PersonaCardV3,
        limit: int = 5,
    ) -> str:
        """
        注入最近的知识（用于主动消息等场景）

        Args:
            persona: 当前人设
            limit: 最大条数

        Returns:
            注入的知识文本
        """
        results = self._store.get_recent(
            persona_name=persona.name,
            limit=limit,
        )

        if not results:
            return ""

        lines = ["【最近了解到的事】"]
        for r in results:
            title = r.get("title", "")
            content = r.get("content", "")
            if title:
                lines.append(f"- {title}")
            else:
                lines.append(f"- {content[:50]}...")

        return "\n".join(lines)

    def should_inject(self, user_message: str) -> bool:
        """
        判断是否应该注入知识

        Args:
            user_message: 用户消息

        Returns:
            是否应该注入
        """
        # 检查消息是否涉及知识领域
        knowledge_keywords = [
            "最近", "新闻", "听说", "知道吗", "告诉我",
            "什么", "怎么样", "如何", "为什么",
        ]

        return any(kw in user_message for kw in knowledge_keywords)

    def build_context(
        self,
        user_message: str,
        persona: PersonaCardV3,
    ) -> dict[str, Any]:
        """
        构建完整的知识上下文

        Returns:
            包含知识注入信息的上下文字典
        """
        should_inject = self.should_inject(user_message)

        context = {
            "should_inject": should_inject,
            "knowledge_text": "",
            "recent_text": "",
        }

        if should_inject:
            context["knowledge_text"] = self.inject(user_message, persona)

        # 总是获取最近知识（用于主动消息参考）
        context["recent_text"] = self.inject_recent(persona, limit=3)

        return context
