"""
人设创建器 — 支持LLM对话式创建人设
"""

from __future__ import annotations

import logging
from typing import Any

from my_character.persona_card_v3 import (
    Identity,
    PersonaCardV3,
    PersonalityTraits,
    SpeakingStyle,
)

logger = logging.getLogger("persona_creator")


# LLM对话式创建的引导提示词
CREATION_PROMPTS = {
    "intro": """你好！我来帮你创建一个人设。
首先，你想让这个人是谁？
（可以是真实人物、虚构角色、或者完全自定义）""",

    "archetype": """好的。{name}的核心性格特征是什么？我从几个维度帮你梳理：

1. [性格类型] 选择最符合的：
   - 傲娇：嘴硬心软，表面嫌弃实则关心
   - 温柔：体贴善解人意，说话轻声细语
   - 高冷：表面冷淡，内心细腻
   - 活泼：开朗有活力，喜欢分享
   - 知性：理性思考，喜欢深度交流

2. [温暖度] {name}是偏温暖亲切，还是高冷疏离？
   （0-10分，建议：{suggested_warmth}）

3. [说话风格] 说话偏正式还是随意？
   （0-10分，0=非常随意，10=很正式）

4. [幽默感] 爱开玩笑还是严肃认真？
   （0-10分，建议：{suggested_humor}）

5. [兴趣领域] {name}最关注的领域是什么？
   （如：音乐、游戏、阅读、运动等）

6. [口头禅] 有什么标志性的话？
   （如："哼"、"才不是"、"好的呀"等）

请逐一回答，或直接描述你心中的{name}。""",

    "confirm": """根据你的描述，我为人设卡生成了以下配置：

【身份】
- 名称：{name}
- 性格类型：{archetype}
- 背景：{backstory}

【性格维度】
- 温暖度：{warmth}
- 傲娇程度：{stubbornness}
- 活泼度：{playfulness}

【说话风格】
- 正式度：{formality}
- emoji使用：{emoji_freq}
- 口头禅：{catchphrases}

确认创建吗？（可以调整任何参数）""",
}


class PersonaCreator:
    """
    人设创建器

    支持LLM对话式创建人设卡
    """

    def __init__(self, llm_gateway: Any = None):
        self._llm = llm_gateway
        self._creation_sessions: dict[str, dict] = {}

    def start_creation(self, session_id: str) -> str:
        """开始创建会话"""
        self._creation_sessions[session_id] = {
            "stage": "intro",
            "data": {},
        }
        return CREATION_PROMPTS["intro"]

    def process_response(self, session_id: str, user_response: str) -> tuple[str, PersonaCardV3 | None]:
        """
        处理用户响应

        Args:
            session_id: 会话ID
            user_response: 用户回复

        Returns:
            (下一步提示, 完成的人设卡或None)
        """
        if session_id not in self._creation_sessions:
            return "会话不存在，请重新开始", None

        session = self._creation_sessions[session_id]
        stage = session["stage"]
        data = session["data"]

        if stage == "intro":
            # 解析名字
            name = self._extract_name(user_response)
            data["name"] = name
            session["stage"] = "details"

            # 生成下一步提示
            prompt = CREATION_PROMPTS["archetype"].format(
                name=name,
                suggested_warmth=7,
                suggested_humor=5,
            )
            return prompt, None

        elif stage == "details":
            # 解析详细配置
            self._parse_details(user_response, data)
            session["stage"] = "confirm"

            # 生成确认提示
            prompt = CREATION_PROMPTS["confirm"].format(
                name=data.get("name", "AI伴侣"),
                archetype=data.get("archetype", "温柔"),
                backstory=data.get("backstory", "暂无"),
                warmth=data.get("warmth", 0.7),
                stubbornness=data.get("stubbornness", 0.3),
                playfulness=data.get("playfulness", 0.5),
                formality=data.get("formality", 0.3),
                emoji_freq=data.get("emoji_freq", 0.5),
                catchphrases=", ".join(data.get("catchphrases", [])),
            )
            return prompt, None

        elif stage == "confirm":
            # 创建人设卡
            if any(kw in user_response.lower() for kw in ["是", "确认", "好的", "ok", "yes"]):
                persona = self._create_persona(data)
                del self._creation_sessions[session_id]
                return f"✅ 人设卡「{persona.name}」创建成功！", persona
            else:
                # 允许修改
                self._parse_modifications(user_response, data)
                return "已记录修改，请再次确认（输入'确认'完成创建）", None

        return "未知状态", None

    def _extract_name(self, text: str) -> str:
        """从文本中提取名字"""
        # 简单实现：取第一个非标点词
        import re
        words = re.findall(r"[\w]+", text)
        if words:
            return str(words[0])
        return "AI伴侣"

    def _parse_details(self, text: str, data: dict) -> None:
        """解析详细配置"""
        text.lower()

        # 解析性格类型
        archetypes = ["傲娇", "温柔", "高冷", "活泼", "知性"]
        for arch in archetypes:
            if arch in text:
                data["archetype"] = arch
                break

        # 解析数值
        import re
        numbers = re.findall(r"(\d+(?:\.\d+)?)", text)
        if len(numbers) >= 1:
            data["warmth"] = float(numbers[0]) / 10 if float(numbers[0]) > 1 else float(numbers[0])
        if len(numbers) >= 2:
            data["formality"] = float(numbers[1]) / 10 if float(numbers[1]) > 1 else float(numbers[1])
        if len(numbers) >= 3:
            data["playfulness"] = float(numbers[2]) / 10 if float(numbers[2]) > 1 else float(numbers[2])

        # 解析口头禅
        catchphrase_match = re.findall(r"[「\"]([^」\"]+)[」\"]", text)
        if catchphrase_match:
            data["catchphrases"] = catchphrase_match

        # 根据性格类型设置默认值
        archetype = data.get("archetype", "温柔")
        if archetype == "傲娇":
            data.setdefault("stubbornness", 0.6)
            data.setdefault("warmth", 0.8)
            data.setdefault("catchphrases", ["哼", "才不是"])
        elif archetype == "温柔":
            data.setdefault("stubbornness", 0.2)
            data.setdefault("warmth", 0.8)
        elif archetype == "高冷":
            data.setdefault("stubbornness", 0.3)
            data.setdefault("warmth", 0.4)
        elif archetype == "活泼":
            data.setdefault("playfulness", 0.8)
            data.setdefault("warmth", 0.7)

    def _parse_modifications(self, text: str, data: dict) -> None:
        """解析修改请求"""
        self._parse_details(text, data)

    def _create_persona(self, data: dict) -> PersonaCardV3:
        """创建人设卡"""
        identity = Identity(
            name=data.get("name", "AI伴侣"),
            archetype=data.get("archetype", "温柔"),
            backstory=data.get("backstory", ""),
        )

        personality = PersonalityTraits(
            warmth=float(data.get("warmth", 0.7)),
            playfulness=float(data.get("playfulness", 0.5)),
            stubbornness=float(data.get("stubbornness", 0.3)),
        )

        speaking_style = SpeakingStyle(
            formality=float(data.get("formality", 0.3)),
            emoji_freq=float(data.get("emoji_freq", 0.5)),
            catchphrases=data.get("catchphrases", []),
        )

        return PersonaCardV3(
            identity=identity,
            personality=personality,
            speaking_style=speaking_style,
        )

    def create_from_description(self, description: str) -> PersonaCardV3:
        """
        从自然语言描述创建人设卡（简化接口）

        Args:
            description: 自然语言描述，如"创建一个周杰伦，傲娇型，喜欢音乐和篮球"

        Returns:
            创建的人设卡
        """
        data = {}

        # 提取名字
        import re
        name_match = re.search(r"创建一个([^，,]+)", description)
        if name_match:
            data["name"] = name_match.group(1).strip()

        # 提取性格类型
        for arch in ["傲娇", "温柔", "高冷", "活泼", "知性"]:
            if arch in description:
                data["archetype"] = arch
                break

        # 提取兴趣
        interests_match = re.search(r"喜欢([^，,]+)", description)
        if interests_match:
            data["interests"] = [i.strip() for i in interests_match.group(1).split("和")]

        return self._create_persona(data)
