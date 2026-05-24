"""
5维风格向量提取器 — 从用户聊天记录分析表达风格

增强 ToneMimic 的分析能力，将风格从经验统计升级为
多维度向量分析，支持人格驱动演化。

StyleVector 五维度:
  - formality:      正式度 (0~1)
  - expressiveness: 情感表达度 (0~1)
  - humor:          幽默感 (0~1)
  - directness:     直接度 (0~1)
  - sentiment:      情感倾向 (0~1, 0=负面 0.5=中性 1=正面)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .models import StyleVector

logger = logging.getLogger("style_vectorizer")

# 风格分析用的正则/常量
EMOJI_PATTERN = re.compile(
    r'[～~❤️😊😝🥰✨😘😳🤔💕🥺😏😭😤💪😌😋🤗😉🙃😎🤩😢😣😖😫😩🥱😴😪🤤😈]'
    r'|[\U0001F300-\U0001FFFF]|[\u2600-\u27BF]'
)
EXCLAMATION_PATTERN = re.compile(r'[！!]{1,}')
QUESTION_PATTERN = re.compile(r'[？?]{1,}')
ELLIPSIS_PATTERN = re.compile(r'[。.。]{2,}|\.{2,}')
COLON_EMOJI = re.compile(r'[：:]\s*[）\)\]DdPp]')  # :) :D :p

# 正式/非正式语气词
FORMAL_MARKERS = [
    '您好', '请问', '感谢', '抱歉', '不好意思', '麻烦',
    '请', '您', '贵', '可否', '能否',
]
INFORMAL_MARKERS = [
    '啦', '嘛', '呀', '呢', '哈', '嘿嘿', '嘻嘻',
    '好滴', 'ok', '嗯嗯', '哦哦', '嗷',
]
# 幽默相关
HUMOR_MARKERS = [
    '哈哈', '嘿嘿', '嘻嘻', 'hhh', '笑死', '哈哈哈',
    '开玩笑', '逗你', '调皮', 'ww', 'hhhh',
]
# 直接表达
DIRECT_MARKERS = [
    '我觉得', '我认为', '我要', '我想', '就是',
    '肯定', '绝对', '一定', '必须', '别',
]
INDIRECT_MARKERS = [
    '可能', '也许', '大概', '应该', '或许',
    '有点', '稍微', '不太', '不一定',
]
# 正面/负面情绪词
POSITIVE_WORDS = [
    '开心', '高兴', '喜欢', '爱', '好棒', '赞', '不错',
    '幸福', '感动', '美好', '快乐', '享受', '期待',
    '舒服', '满意', '厉害', '优秀', '可爱', '好看',
]
NEGATIVE_WORDS = [
    '难过', '伤心', '生气', '讨厌', '烦', '累', '无聊',
    '失望', '焦虑', '担心', '害怕', '痛苦', '不开心',
    '郁闷', '委屈', '难受', '崩溃', '受不了',
]


class StyleVectorizer:
    """风格向量提取器

    从聊天记录中提取 5 维风格向量，支持:
    - 单次分析 (analyze)
    - 增量更新 (update)
    - OCEAN驱动初始化 (from_ocean)
    """

    def __init__(self):
        self._history: list[dict[str, Any]] = []

    def analyze(self, messages: list[str]) -> StyleVector:
        """从一组消息中分析风格向量

        Args:
            messages: 用户消息列表（最新在前）

        Returns:
            StyleVector 风格向量
        """
        if not messages:
            return StyleVector()

        total = len(messages)
        all_text = " ".join(messages)

        # ── 正式度 formality ──
        formal_count = sum(1 for m in messages
                           for marker in FORMAL_MARKERS if marker in m)
        informal_count = sum(1 for m in messages
                             for marker in INFORMAL_MARKERS if marker in m)
        total_markers = formal_count + informal_count
        if total_markers > 0:
            formality = formal_count / total_markers
        else:
            formality = 0.3  # 默认偏低（日常对话偏随意）

        # ── 情感表达度 expressiveness ──
        emoji_count = len(EMOJI_PATTERN.findall(all_text))
        exclaim_count = len(EXCLAMATION_PATTERN.findall(all_text))
        expression_density = (emoji_count + exclaim_count * 0.5) / max(1, total)
        expressiveness = min(1.0, expression_density * 2)

        # ── 幽默感 humor ──
        humor_count = sum(1 for m in messages
                          for marker in HUMOR_MARKERS if marker in m)
        colon_emoji_count = len(COLON_EMOJI.findall(all_text))
        humor = min(1.0, (humor_count + colon_emoji_count) / max(1, total))

        # ── 直接度 directness ──
        direct_count = sum(1 for m in messages
                           for marker in DIRECT_MARKERS if marker in m)
        indirect_count = sum(1 for m in messages
                             for marker in INDIRECT_MARKERS if marker in m)
        total_di = direct_count + indirect_count
        directness = direct_count / max(1, total_di) if total_di > 0 else 0.5

        # ── 情感倾向 sentiment ──
        pos_count = sum(1 for w in POSITIVE_WORDS if w in all_text)
        neg_count = sum(1 for w in NEGATIVE_WORDS if w in all_text)
        total_pn = pos_count + neg_count
        if total_pn > 0:
            sentiment = pos_count / total_pn
        else:
            sentiment = 0.6  # 默认偏正面

        return StyleVector(
            formality=formality,
            expressiveness=expressiveness,
            humor=humor,
            directness=directness,
            sentiment=sentiment,
        )

    def update(self, new_message: str, current: StyleVector,
              weight: float = 0.3) -> StyleVector:
        """用新消息增量更新风格向量

        Args:
            new_message: 新用户消息
            current: 当前风格向量
            weight: 新消息权重 (0.0~1.0)

        Returns:
            更新后的 StyleVector
        """
        new_vec = self.analyze([new_message])
        return StyleVector(
            formality=current.formality * (1 - weight) + new_vec.formality * weight,
            expressiveness=current.expressiveness * (1 - weight) + new_vec.expressiveness * weight,
            humor=current.humor * (1 - weight) + new_vec.humor * weight,
            directness=current.directness * (1 - weight) + new_vec.directness * weight,
            sentiment=current.sentiment * (1 - weight) + new_vec.sentiment * weight,
        )

    def to_tone_mimic_profile(self, style: StyleVector) -> dict[str, Any]:
        """将 StyleVector 转为 ToneMimic 可用的配置

        适配 my_character/tone_mimic.py 的 StyleProfile 格式
        """
        return {
            "formality": style.formality,
            "emotion_expr": style.expressiveness,
            "humor": style.humor,
            "directness": style.directness,
            "sentiment": style.sentiment,
            "emoji_freq": style.expressiveness * 0.8 + 0.2,
        }

    @staticmethod
    def batch_analyze(messages: list[str], window: int = 20) -> list[float]:
        """批量分析一段时间内的风格趋势

        Returns:
            [formality, expressiveness, humor, directness, sentiment]
            每个值是该维度在窗口内的平均值
        """
        if not messages:
            return [0.5, 0.5, 0.5, 0.5, 0.5]

        recent = messages[:window]
        vec = StyleVectorizer().analyze(recent)
        return [vec.formality, vec.expressiveness, vec.humor,
                vec.directness, vec.sentiment]
