"""
内容动态生成器 — LLM动态生成主动消息内容
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

from my_character.persona_card_v3 import PersonaCardV3

logger = logging.getLogger("content_generator")


class ContentGenerator:
    """
    内容动态生成器

    使用LLM动态生成主动消息，替代死模板
    """

    # 时间段问候模板（作为fallback）
    TIME_GREETINGS = {
        "morning": [
            "早安～",
            "早啊",
            "起床了没",
        ],
        "lunch": [
            "吃饭了吗",
            "午饭时间到",
        ],
        "dinner": [
            "晚饭吃了没",
            "该吃饭啦",
        ],
        "night": [
            "还不睡？",
            "晚安～",
        ],
    }

    def __init__(self, llm_gateway: Any = None):
        self._llm = llm_gateway

    def generate(
        self,
        persona: PersonaCardV3,
        message_type: str,
        context: dict[str, Any],
    ) -> str:
        """
        生成主动消息

        Args:
            persona: 人设卡
            message_type: 消息类型 (morning/lunch/dinner/night/miss_you)
            context: 上下文信息

        Returns:
            生成的消息
        """
        if self._llm:
            return self._generate_with_llm(persona, message_type, context)
        else:
            return self._generate_with_template(persona, message_type, context)

    def _generate_with_llm(
        self,
        persona: PersonaCardV3,
        message_type: str,
        context: dict[str, Any],
    ) -> str:
        """使用LLM生成"""
        prompt = self._build_prompt(persona, message_type, context)

        try:
            result = self._llm.chat_sync(query=prompt, temperature=0.8)
            return str(result)[:100]  # 限制长度
        except Exception as e:  # noqa: BLE001

            logger.warning("LLM generation failed: %s", e)
            return self._generate_with_template(persona, message_type, context)

    def _build_prompt(
        self,
        persona: PersonaCardV3,
        message_type: str,
        context: dict[str, Any],
    ) -> str:
        """构建生成提示词"""
        affinity = context.get("affinity", 5)
        current_time = context.get("current_time", datetime.now().strftime("%H:%M"))  # noqa: DTZ005
        hours_since = context.get("hours_since_last_chat", 0)

        # 消息类型描述
        type_desc = {
            "morning": "早安问候",
            "lunch": "午饭提醒",
            "dinner": "晚饭提醒",
            "night": "晚安问候",
            "miss_you": "表达想念",
        }.get(message_type, "主动消息")

        prompt = f"""你现在是{persona.name}，{persona.get_personality_summary()}。

当前情况：
- 时间：{current_time}
- 好感度：{affinity}/8
- 距上次聊天：{hours_since:.1f}小时
- 消息类型：{type_desc}

性格特点：
- 类型：{persona.archetype}
- 温暖度：{persona.personality.warmth:.1f}
- 傲娇程度：{persona.personality.stubbornness:.1f}

说话风格：
"""

        if persona.speaking_style.catchphrases:
            prompt += f"- 口头禅：{', '.join(persona.speaking_style.catchphrases[:3])}\n"

        if persona.personality.stubbornness > 0.5:
            prompt += "- 表面嫌弃，实则关心\n"

        if affinity >= 6:
            prompt += "- 可以直接表达想念和关心\n"
        elif affinity <= 2:
            prompt += "- 保持礼貌，不要过于亲密\n"

        prompt += f"""
请生成一条{type_desc}消息（15-30字）：
- 保持{persona.archetype}风格
- 自然不做作
- 符合当前好感度关系
"""

        return prompt

    def _generate_with_template(
        self,
        persona: PersonaCardV3,
        message_type: str,
        context: dict[str, Any],
    ) -> str:
        """使用模板生成（fallback）"""
        affinity = context.get("affinity", 5)

        # 获取基础问候
        greetings = self.TIME_GREETINGS.get(message_type, ["在吗"])
        base = random.choice(greetings)

        # 根据人设调整
        if persona.speaking_style.catchphrases:
            catchphrase = random.choice(persona.speaking_style.catchphrases)
            if persona.personality.stubbornness > 0.5:
                base = f"{catchphrase} {base}"

        # 根据好感度调整
        if affinity >= 6:
            # 高好感度：添加关心
            cares = ["想你了", "在干嘛", "今天怎么样"]
            base = f"{base}，{random.choice(cares)}"
        elif affinity <= 2:
            # 低好感度：保持简洁
            pass

        return base

    def generate_miss_you(
        self,
        persona: PersonaCardV3,
        hours_since: float,
        affinity: int,
    ) -> str:
        """
        生成想念消息

        Args:
            persona: 人设卡
            hours_since: 距上次聊天的小时数
            affinity: 好感度

        Returns:
            想念消息
        """
        context = {
            "affinity": affinity,
            "hours_since_last_chat": hours_since,
            "current_time": datetime.now().strftime("%H:%M"),  # noqa: DTZ005
        }
        return self.generate(persona, "miss_you", context)
