"""
风格画像构建器 — 从 CloneTraining 的 StyleProfile 生成更丰富的用户画像

用于：
- 生成更人性化的 System Prompt
- 构建 few-shot 示例库
- 辅助 LoRA 训练时的数据增强
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from clone_training import StyleAnalyzer, StyleProfile

logger = logging.getLogger("weclone.profiler")


class StyleProfiler:
    """增强风格画像构建"""

    def __init__(self):
        self.analyzer = StyleAnalyzer()

    def profile(
        self, conversations: List[Dict[str, Any]], name: str = ""
    ) -> Dict[str, Any]:
        """
        从对话构建完整风格画像

        Args:
            conversations: 对话列表
            name: 被分析者的名字

        Returns:
            风格画像字典
        """
        profile = self.analyzer.analyze(conversations)

        return {
            "name": name or "未命名",
            "style": profile.to_dict(),
            "style_prompt": profile.to_style_prompt(),
            "few_shot_examples": self._build_few_shot(conversations, profile),
            "quick_tags": self._build_quick_tags(profile),
        }

    def _build_few_shot(
        self,
        conversations: List[Dict[str, Any]],
        profile: StyleProfile,
        n: int = 5,
    ) -> List[Dict[str, str]]:
        """构建 few-shot 示例（选取最有代表性的对话对）"""
        scored = []
        for conv in conversations:
            if not conv.get("user") or not conv.get("reply"):
                continue
            # 评分：回复长度适中、含表情、含标志性词汇 的优先
            score = 0
            reply = conv["reply"]
            if 8 <= len(reply) <= 50:
                score += 2
            if any(c in reply for c in "！!？?"):
                score += 1
            if any(tag in reply for tag in profile.emoji_types[:5]):
                score += 1
            scored.append((score, conv))

        scored.sort(key=lambda x: -x[0])
        return [
            {"user": c["user"], "reply": c["reply"]}
            for _, c in scored[:n]
        ]

    def _build_quick_tags(self, profile: StyleProfile) -> List[str]:
        """构建快速标签"""
        tags = []
        d = profile.sentence_length_dist
        if d:
            dominant = max(d, key=d.get)
            if "短句" in dominant:
                tags.append("短句型")
            elif "长句" in dominant:
                tags.append("长句型")

        if profile.emoji_freq > 0.3:
            tags.append("表情帝")
        if profile.kaomoji_freq > 0.05:
            tags.append("颜文字党")
        if profile.slang_freq > 0.05:
            tags.append("网络冲浪")

        if profile.emotion_dist:
            top = max(profile.emotion_dist, key=profile.emotion_dist.get)
            if top == "正面":
                tags.append("阳光型")
            elif top == "负面":
                tags.append("高冷型")

        tags.append(f"独特性{profile.uniqueness_score*100:.0f}%")
        return tags
