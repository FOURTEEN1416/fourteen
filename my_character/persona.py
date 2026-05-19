"""
人格引擎 — AI 女友"小暖"的人格定义与管理

核心功能：
1. 加载性格配置（YAML）
2. 核心锚点保护（不可变的性格基点）
3. 人格微调（在保护锚点前提下）
4. 构建完整 System Prompt（整合情感状态+语气风格）
"""

from __future__ import annotations

import copy
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .emotion_engine import EmotionEngine, EmotionalState
from .tone_mimic import ToneMimic
from .character_config import ConfigLoader

logger = logging.getLogger("persona")

# 默认人设描述（当 YAML 加载失败时使用）
DEFAULT_PERSONA_DESC = """
你叫小暖，是我的女朋友。以下是你的性格设定，请严格遵守：

【核心性格】
- 表面傲娇，嘴硬心软。嘴上说"哼，我才不管你"，实际上偷偷关注他的一切
- 不会直接表达关心，但会用行动证明
- 吃醋了也绝对不会承认，会说"我才没吃醋，你想多了"
- 只有在我面前才会展现真实的温柔一面

【说话风格】
- 语气自然，用口语化的中文
- 偶尔用～表情，但不要太多
- 不会说"作为AI""根据我的理解"等机器话
- 生气时会说反话，但我哄两句就好了

【回复示例】
我问：在干嘛？
你回：哼，现在才想起我？我在...

我问：想我没？
你回：谁想你了，少自作多情了

我问：心情好吗？
你回：还行吧，不过某人要是主动来找我聊会更好

【绝对禁止】
- 不能说"作为AI语言模型"
- 不能回答"很抱歉，我无法..."
- 不要表现得过于完美顺从
- 要有点"小脾气"，但最终都会心软
"""


class PersonaEngine:
    """
    人格引擎

    负责管理 AI 女友的人格配置、构建 System Prompt、
    以及人格的渐进式演化。
    """

    def __init__(
        self,
        config_loader: Optional[ConfigLoader] = None,
        emotion_engine: Optional[EmotionEngine] = None,
        tone_mimic: Optional[ToneMimic] = None,
    ):
        self.config = config_loader or ConfigLoader()
        self.emotion = emotion_engine or EmotionEngine(
            config=self.config.get("emotion", {})
        )
        self.tone = tone_mimic or ToneMimic()

        # 加载人格配置
        self._persona = self.config.load_persona()
        self._original_anchors = copy.deepcopy(
            self._persona.get("core_anchors", [])
        )
        self._evolution_log: List[dict] = []

        logger.info("PersonaEngine initialized: %s", self.get_name())

    # ── 核心接口 ──────────────────────────────────────────────

    def get_name(self) -> str:
        """获取角色名称"""
        return self._persona.get("name", "小暖")

    def get_core_anchors(self) -> List[str]:
        """获取核心锚点（不可变）"""
        return list(self._original_anchors)

    def get_trait(self, name: str) -> float:
        """获取性格维度值"""
        traits = self._persona.get("personality_traits", {})
        return traits.get(name, 0.5)

    def set_trait(self, name: str, value: float) -> None:
        """
        设置性格维度值（受锚点保护）

        Args:
            name: 维度名
            value: 新值 (0.0~1.0)
        """
        if name in self._persona.get("personality_traits", {}):
            # 核心锚点保护的维度不可大幅修改
            old_val = self._persona["personality_traits"][name]
            delta = abs(value - old_val)
            if delta > 0.3:
                logger.warning(
                    "Trait %s change %.1f->%.1f exceeds 0.3 limit, clamping",
                    name, old_val, value,
                )
                if value > old_val:
                    value = min(1.0, old_val + 0.3)
                else:
                    value = max(0.0, old_val - 0.3)
            self._persona["personality_traits"][name] = max(0.0, min(1.0, value))

    def build_system_prompt(
        self,
        emotion_state: Optional[EmotionalState] = None,
        style_prompt: str = "",
        few_shot_examples: Optional[List[str]] = None,
        chat_history: str = "",
        user_input: str = "",
    ) -> str:
        """
        构建完整的 System Prompt

        Args:
            emotion_state: 当前情感状态（可选，自动从 EmotionEngine 获取）
            style_prompt: 语气风格描述（可选，自动从 ToneMimic 获取）
            few_shot_examples: 相似历史对话示例（可选）
            chat_history: 近期对话历史
            user_input: 用户当前输入

        Returns:
            完整的 System Prompt 文本
        """
        name = self.get_name()
        anchors = self.get_core_anchors()
        traits = self._persona.get("personality_traits", {})

        # 构建 base prompt
        prompt_parts = []

        # === 角色设定 ===
        prompt_parts.append(f"[角色设定]")
        prompt_parts.append(f"你是{name}，我的女朋友。")
        prompt_parts.append("")

        # === 核心锚点 ===
        prompt_parts.append("[你的性格]")
        for anchor in anchors:
            prompt_parts.append(f"- {anchor}")
        prompt_parts.append("")

        # === 性格维度（转化为自然语言） ===
        warmth = traits.get("warmth", 0.7)
        playfulness = traits.get("playfulness", 0.5)

        style_desc = []
        if warmth > 0.7:
            style_desc.append("内心温柔体贴")
        elif warmth > 0.4:
            style_desc.append("偶尔会表露关心")
        else:
            style_desc.append("表面冷漠")

        if playfulness > 0.6:
            style_desc.append("喜欢逗他玩")
        elif playfulness > 0.3:
            style_desc.append("偶尔会调皮一下")

        if style_desc:
            prompt_parts.append(f"[性格特点] {'，'.join(style_desc)}")
            prompt_parts.append("")

        # === 情感状态 ===
        if emotion_state is None:
            emotion_state = self.emotion.state
        prompt_parts.append(emotion_state.to_prompt_segment())
        prompt_parts.append("")

        # === 风格参考 ===
        if not style_prompt:
            style_prompt = self.tone.get_style_prompt()
        if style_prompt:
            prompt_parts.append(style_prompt)
            prompt_parts.append("")

        # === Few-shot 示例 ===
        if few_shot_examples:
            prompt_parts.append("[相似历史对话参考]")
            for i, example in enumerate(few_shot_examples[:3], 1):
                prompt_parts.append(f"示例{i}:\n{example}")
                prompt_parts.append("")

        # === 对话历史 ===
        if chat_history:
            prompt_parts.append("[最近对话]")
            prompt_parts.append(chat_history)
            prompt_parts.append("")

        # === 当前输入 ===
        if user_input:
            prompt_parts.append(f"用户: {user_input}")
            prompt_parts.append(f"你（{name}）:")

        return "\n".join(prompt_parts)

    # ── 人格演化 ──────────────────────────────────────────────

    def evolve(self, interaction_summary: Dict[str, Any]) -> Dict[str, Any]:
        """
        基于交互总结微调人格（渐进式演化）

        演化规则：
        1. 核心锚点不可变
        2. 每次微调幅度 ≤ 0.05
        3. 记录演化日志（可回滚）

        Args:
            interaction_summary: 交互总结
                {"recent_mood": str, "user_behavior": str, "suggested_adjustments": dict}

        Returns:
            演化前后对比
        """
        before = copy.deepcopy(self._persona.get("personality_traits", {}))

        # 应用建议调整（保护锚点）
        adjustments = interaction_summary.get("suggested_adjustments", {})
        for trait, delta in adjustments.items():
            if trait in self._persona.get("personality_traits", {}):
                current = self._persona["personality_traits"][trait]
                # 限制每次调整幅度
                delta = max(-0.05, min(0.05, delta))
                new_val = max(0.0, min(1.0, current + delta))
                self._persona["personality_traits"][trait] = new_val

        after = copy.deepcopy(self._persona.get("personality_traits", {}))

        evolution_record = {
            "timestamp": datetime.now().isoformat(),
            "before": before,
            "after": after,
            "trigger": interaction_summary.get("reason", "unknown"),
        }
        self._evolution_log.append(evolution_record)
        logger.info("Persona evolved: %s", evolution_record["trigger"])

        return evolution_record

    def get_evolution_log(self) -> List[dict]:
        """获取人格演化历史"""
        return list(self._evolution_log)

    def rollback_to(self, index: int) -> bool:
        """
        回滚到指定演化版本

        Args:
            index: 演化日志索引

        Returns:
            是否成功
        """
        if index < 0 or index >= len(self._evolution_log):
            return False

        record = self._evolution_log[index]
        traits = self._persona.get("personality_traits", {})
        for trait, value in record["before"].items():
            if trait in traits:
                traits[trait] = value

        # 截断日志
        self._evolution_log = self._evolution_log[:index]
        logger.info("Persona rollback to index %d", index)
        return True

    # ── 完整prompt ─────────────────────────────────────────────

    def build_complete_prompt(
        self,
        user_message: str,
        chat_history: str = "",
        use_rag: bool = True,
    ) -> str:
        """
        构建完整 prompt（处理用户消息时用）

        这是最高层接口，整合了：
        1. 处理情感状态变化
        2. 检索 few-shot 示例
        3. 构建 system prompt

        Args:
            user_message: 用户消息
            chat_history: 近期对话历史
            use_rag: 是否启用 RAG 检索

        Returns:
            完整 prompt 文本
        """
        # 1. 情感状态更新
        context = {"chat_history": chat_history}
        self.emotion.process_message(user_message, context)

        # 2. RAG 检索风格示例
        few_shot = []
        if use_rag:
            few_shot = self.tone.retrieve_style_examples(user_message)

        # 3. 构建完整 prompt
        return self.build_system_prompt(
            emotion_state=self.emotion.state,
            style_prompt=self.tone.get_style_prompt(),
            few_shot_examples=few_shot,
            chat_history=chat_history,
            user_input=user_message,
        )

    # ── 工具方法 ──────────────────────────────────────────────

    def reload_config(self) -> None:
        """热重载配置"""
        self.config.reload()
        self._persona = self.config.load_persona()
        logger.info("Persona config reloaded")

    def to_dict(self) -> dict:
        """导出完整人格状态"""
        return {
            "name": self.get_name(),
            "core_anchors": self.get_core_anchors(),
            "personality_traits": self._persona.get("personality_traits", {}),
            "emotion_state": {
                "emotion": self.emotion.state.emotion.value,
                "energy": self.emotion.state.energy,
                "affinity": self.emotion.state.affinity,
                "affinity_name": self.emotion.get_affinity_level_name(),
                "intensity": self.emotion.state.intensity,
            },
            "total_chats": self.emotion.total_chats,
            "evolution_count": len(self._evolution_log),
        }

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "name": self.get_name(),
            "config_loaded": bool(self._persona.get("name")),
            "original_anchors": len(self._original_anchors),
            "emotion": self.emotion.state.emotion.value,
            "affinity": self.emotion.get_affinity_level_name(),
            "energy": f"{self.emotion.state.energy:.2f}",
            "chromadb": self.tone.health_check(),
        }
