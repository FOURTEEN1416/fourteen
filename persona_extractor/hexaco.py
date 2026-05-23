"""
HEXACO 六因素人格模型

参考文献:
  - Ashton & Lee (2007). Empirical, theoretical, and practical advantages
    of the HEXACO model of personality structure.
  - Lee & Ashton (2018). Psychometric properties of the HEXACO-100.

HEXACO 在 OCEAN(Big Five) 基础上增加第六维度:
  H - Honesty-Humility (诚实-谦逊)
  E - Emotionality (情绪性, ≈ 反向Neuroticism)
  X - eXtraversion (外向性)
  A - Agreeableness (宜人性, vs Anger)
  C - Conscientiousness (尽责性)
  O - Openness to Experience (开放性)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class HexacoTraits:
    """HEXACO 六因素人格 (0.0~1.0)"""
    honesty_humility: float = 0.5   # 诚实-谦逊: 真诚/公平/不贪婪 vs 操纵/自恋
    emotionality: float = 0.5       # 情绪性: 焦虑/依赖/多愁善感 vs 坚强/独立
    extraversion: float = 0.5       # 外向性: 社交/活泼/乐观 vs 安静/独处
    agreeableness: float = 0.5      # 宜人性: 宽容/温和 vs 易怒/批判
    conscientiousness: float = 0.5  # 尽责性: 勤奋/完美主义 vs 随性/灵活
    openness: float = 0.5           # 开放性: 好奇/审美/创新 vs 传统/务实

    def __post_init__(self):
        for trait in ['honesty_humility', 'emotionality', 'extraversion',
                       'agreeableness', 'conscientiousness', 'openness']:
            setattr(self, trait, max(0.0, min(1.0, getattr(self, trait))))

    @classmethod
    def from_ocean(cls, ocean) -> 'HexacoTraits':
        """从 OCEAN 推断 HEXACO

        H = (A + C) / 2 (宜人性和尽责性的组合反映诚实-谦逊)
        E = 1 - N (反向神经质 = 情绪性)
        X = E (外向性直接对应)
        A = A (但HEXACO宜人性更偏向容忍度)
        C = C (尽责性相近但略有不同)
        O = O (开放性直接对应)
        """
        return cls(
            honesty_humility=(ocean.agreeableness + ocean.conscientiousness) / 2,
            emotionality=1.0 - ocean.neuroticism,
            extraversion=ocean.extraversion,
            agreeableness=ocean.agreeableness * 0.85 + 0.075,
            conscientiousness=ocean.conscientiousness,
            openness=ocean.openness,
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            'honesty_humility': round(self.honesty_humility, 3),
            'emotionality': round(self.emotionality, 3),
            'extraversion': round(self.extraversion, 3),
            'agreeableness': round(self.agreeableness, 3),
            'conscientiousness': round(self.conscientiousness, 3),
            'openness': round(self.openness, 3),
        }

    def to_prompt_segment(self) -> str:
        """生成中文人格描述"""
        labels = {
            'honesty_humility': {
                'low': '精于计算，重视地位和物质',
                'mid': '诚实适中，有基本道德感',
                'high': '真诚谦逊，不贪图私利',
            },
            'emotionality': {
                'low': '情绪独立坚强，不易被影响',
                'mid': '情绪反应适中',
                'high': '情感丰富细腻，富有同理心',
            },
            'extraversion': {
                'low': '内向安静，喜欢独处',
                'mid': '外向适中，看场合表现',
                'high': '外向热情，喜欢社交互动',
            },
            'agreeableness': {
                'low': '直率坦诚，不容易妥协',
                'mid': '温和适中',
                'high': '非常宽容，善解人意',
            },
            'conscientiousness': {
                'low': '随性自由，灵活应变',
                'mid': '做事有条理',
                'high': '极其认真，追求完美',
            },
            'openness': {
                'low': '传统务实，喜欢熟悉的事物',
                'mid': '对新事物有一定接受度',
                'high': '开放好奇，喜欢探索新鲜事物',
            },
        }

        def _level(val: float) -> str:
            if val < 0.35:
                return 'low'
            elif val > 0.65:
                return 'high'
            return 'mid'

        lines = ["[用户人格-HEXACO]"]
        for trait in ['honesty_humility', 'emotionality', 'extraversion',
                       'agreeableness', 'conscientiousness', 'openness']:
            val = getattr(self, trait)
            desc = labels[trait][_level(val)]
            lines.append(f"- {desc}")
        return "\n".join(lines)
