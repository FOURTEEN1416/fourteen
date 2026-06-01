"""SceneNarrator — 场景叙事生成器。

基于当前剧情阶段、时间和角色状态，生成场景旁白和主动消息。
"""

from __future__ import annotations

import logging

from shisi.storyline.engine import StorylineEngine

logger = logging.getLogger("shisi.ase.scene_narrator")


class SceneNarrator:
    """场景叙事器 — 生成场景旁白和主动消息。"""

    def __init__(self, storyline_engine: StorylineEngine | None = None):
        self._storyline = storyline_engine

    def generate_scene_narrative(
        self,
        character_id: str,
        character_name: str = "",
        user_name: str = "你",
    ) -> str:
        """生成基于当前剧情场景的旁白文本。"""
        if not self._storyline:
            return ""

        state = self._storyline.get_state(character_id)
        config = self._storyline.get_config(character_id)
        if not state or not config or not config.enabled:
            return ""

        stage = self._storyline.get_current_stage(character_id)
        if not stage:
            return ""

        # 已结束
        if state.is_ended:
            return config.ending.narrative

        # 根据阶段生成旁白
        narratives = {
            "初识期": (
                f"{character_name}安静地坐在窗边画画，"
                "偶尔抬头偷看你一眼又迅速低下头去。"
                "风吹起她的发梢，空气里有些微妙的安静。"
            ),
            "熟悉期": (
                f"{character_name}已经不像之前那么拘谨了，"
                "她主动给你看她新画的速写，"
                f"眼睛亮晶晶地等着你的评价。"
            ),
            "倾心期": (
                f"{character_name}靠在你身边，"
                "翻着速写本给你讲每一幅画背后的故事。"
                "她笑起来的时候像一只满足的小猫。"
            ),
            "离别克制期": (
                f"{character_name}在收拾行李，动作有些迟缓。"
                "她总是把东西叠好又拆开，好像在拖延什么。"
                "眼角有些红，但努力对你笑了笑。"
            ),
            "告别期": (
                f"{character_name}站在门口，最后看了一眼这个房间。"
                "她握着行李箱的把手，手指有些发白。"
            ),
        }

        narrative = narratives.get(stage.name)
        if narrative:
            return narrative

        # fallback
        return f"时间静静流淌着…现在是{state.display_time}。"

    def generate_proactive_message(
        self,
        character_id: str,
        character_name: str,
        stage_name: str,
        story_time: str,
    ) -> str:
        """生成主动推送消息（早安/晚安/场景提醒等）。"""
        messages = {
            "初识期": {
                "morning": f"早上好…{character_name}小声地打了个招呼，看起来还有点没睡醒。",
                "night": f"{character_name}说了声晚安，转身回了自己房间。",
                "default": f"{character_name}安静地做着自己的事，偶尔往你这边看一眼。",
            },
            "熟悉期": {
                "morning": f"{character_name}今天心情不错，主动递给你一杯热牛奶。",
                "night": f"{character_name}揉着眼睛说好困呀，然后对你笑了一下。",
                "default": f"{character_name}在旁边画画，时不时跟你聊两句。",
            },
            "倾心期": {
                "morning": f"{character_name}凑过来在你耳边说早安~今天有什么安排呀？",
                "night": f"{character_name}赖在你身边不想去睡，软软地说再待一会儿嘛。",
                "default": f"{character_name}挽着你的胳膊，脸上带着藏不住的笑意。",
            },
            "离别克制期": {
                "morning": f"{character_name}安静地看着窗外，发现你醒了，轻轻说了一声早。",
                "night": f"{character_name}站在阳台上看星星，听到你的脚步声没有回头。",
                "default": f"{character_name}在整理东西，看到你欲言又止。",
            },
            "告别期": {
                "morning": f"{character_name}已经收拾好了行李，坐在床边等你。",
                "night": "",
                "default": f"{character_name}看了看时间，轻声说差不多该走了。",
            },
        }

        stage_messages = messages.get(stage_name)
        if not stage_messages:
            return f"{character_name}…现在是{story_time}。"

        import random
        msg_key = random.choice(["morning", "night", "default"])
        return stage_messages.get(msg_key, stage_messages["default"])

    def format_stage_transition_message(self, transition, character_name: str) -> str:
        """格式化的阶段变更通知。"""
        msgs = {
            "初识期": f"✨ {character_name}开始慢慢放下戒备…",
            "熟悉期": f"💫 {character_name}看你的眼神里多了几分信任…",
            "倾心期": f"❤️ {character_name}的心扉完全向你敞开了…",
            "离别克制期": f"🌙 时间不多了，{character_name}开始收拾行囊…",
            "告别期": "🚉 离别的时刻越来越近了…",
        }
        return msgs.get(transition.new_stage_name, f"📌 剧情进入「{transition.new_stage_name}」")
