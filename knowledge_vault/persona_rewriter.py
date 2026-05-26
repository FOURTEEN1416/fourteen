"""
人设口吻改写器 — 将信息以人设的口吻转述
"""

from __future__ import annotations

import logging
from typing import Any

from my_character.persona_card import PersonaCardV3

logger = logging.getLogger("persona_rewriter")


class PersonaRewriter:
    """
    人设口吻改写器

    将采集的信息以人设的口吻重新表述
    """

    def __init__(self, llm_gateway: Any = None):
        self._llm = llm_gateway

    def rewrite(
        self,
        content: str,
        persona: PersonaCardV3,
        content_type: str = "news",
    ) -> str:
        """
        以人设口吻改写内容

        Args:
            content: 原始内容
            persona: 人设卡
            content_type: 内容类型 (news/fact/anecdote)

        Returns:
            改写后的内容
        """
        if self._llm is None:
            return self._simple_rewrite(content, persona)

        prompt = self._build_rewrite_prompt(content, persona, content_type)

        try:
            result = self._llm.chat_sync(query=prompt, temperature=0.7)
            return str(result)
        except Exception as e:  # noqa: BLE001

            logger.warning("LLM rewrite failed: %s", e)
            return self._simple_rewrite(content, persona)

    def _build_rewrite_prompt(
        self,
        content: str,
        persona: PersonaCardV3,
        content_type: str,
    ) -> str:
        """构建改写提示词"""
        type_prompts = {
            "news": "请以你的口吻把这个新闻分享给朋友",
            "fact": "请以你的口吻告诉朋友这个有趣的事实",
            "anecdote": "请以你的口吻分享这个小故事",
        }

        prompt = f"""你现在是{persona.name}，{persona.get_personality_summary()}。

说话风格要求：
- {persona.archetype}风格
- {"使用口头禅：" + "、".join(persona.speaking_style.catchphrases[:3]) if persona.speaking_style.catchphrases else "保持自然口语"}
- {"适当使用emoji" if persona.speaking_style.emoji_freq > 0.5 else "少用emoji"}

{type_prompts.get(content_type, "请以你的口吻转述以下内容")}：

{content[:500]}

要求：
1. 保持你的性格特点
2. 自然不做作
3. 30-100字
"""
        return prompt

    def _simple_rewrite(self, content: str, persona: PersonaCardV3) -> str:
        """简单改写（无LLM时）"""
        # 截取前100字
        text = content[:100]
        if len(content) > 100:
            text += "..."

        # 添加口头禅前缀
        if persona.speaking_style.catchphrases:
            import random
            prefix = random.choice(persona.speaking_style.catchphrases)
            return f"{prefix} {text}"

        return text

    def rewrite_batch(
        self,
        contents: list[str],
        persona: PersonaCardV3,
    ) -> list[str]:
        """批量改写"""
        return [self.rewrite(c, persona) for c in contents]
