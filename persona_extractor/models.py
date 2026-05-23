"""
PersonaExtractor 数据模型 — 融合 Agethos OCEAN+PAD + 原系统五维画像

核心概念：
  - OceanTraits: 5维人格（OCEAN），0.0-1.0 连续值
  - PadState: 3轴情感（Pleasure-Arousal-Dominance），-1.0~1.0
  - UserPersona: 用户人格画像（含演化历史）
  - StyleVector: 5维风格向量（用于 tone_mimic 增强）
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
#  OCEAN 五大人格特质
# ---------------------------------------------------------------------------

@dataclass
class OceanTraits:
    """OCEAN 五大人格特质 (0.0 ~ 1.0 连续值)

    来源: Agethos OceanTraits 数据模型
    映射关系:
      - 0.0 = 低端 (如 Introversion)
      - 1.0 = 高端 (如 Extraversion)
    """
    openness: float = 0.5       # 开放性 - 好奇心/创造力 vs 传统/务实
    conscientiousness: float = 0.5  # 尽责性 - 自律/条理 vs 随性/混乱
    extraversion: float = 0.5   # 外向性 - 社交/热情 vs 安静/独处
    agreeableness: float = 0.5  # 宜人性 - 友善/合作 vs 竞争/怀疑
    neuroticism: float = 0.5    # 神经质 - 敏感/焦虑 vs 情绪稳定

    def __post_init__(self):
        for trait in ['openness', 'conscientiousness', 'extraversion',
                       'agreeableness', 'neuroticism']:
            val = getattr(self, trait)
            setattr(self, trait, max(0.0, min(1.0, val)))

    @classmethod
    def random(cls) -> 'OceanTraits':
        """生成随机人格（用于初始化）"""
        return cls(
            openness=random.uniform(0.3, 0.8),
            conscientiousness=random.uniform(0.3, 0.8),
            extraversion=random.uniform(0.3, 0.8),
            agreeableness=random.uniform(0.3, 0.8),
            neuroticism=random.uniform(0.2, 0.7),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> 'OceanTraits':
        return cls(
            openness=data.get('openness', 0.5),
            conscientiousness=data.get('conscientiousness', 0.5),
            extraversion=data.get('extraversion', 0.5),
            agreeableness=data.get('agreeableness', 0.5),
            neuroticism=data.get('neuroticism', 0.5),
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            'openness': round(self.openness, 3),
            'conscientiousness': round(self.conscientiousness, 3),
            'extraversion': round(self.extraversion, 3),
            'agreeableness': round(self.agreeableness, 3),
            'neuroticism': round(self.neuroticism, 3),
        }

    def to_prompt_segment(self, prefix: str = "[用户人格]") -> str:
        """生成可注入 system prompt 的描述文本"""
        labels = {
            ('openness', 'low'): '保守务实，喜欢熟悉的事物',
            ('openness', 'mid'): '对新事物有一定好奇心',
            ('openness', 'high'): '开放好奇，乐于尝试新事物',
            ('conscientiousness', 'low'): '随性自由，不喜欢条条框框',
            ('conscientiousness', 'mid'): '做事有条理但不过分',
            ('conscientiousness', 'high'): '认真负责，做事有计划',
            ('extraversion', 'low'): '内向安静，享受独处',
            ('extraversion', 'mid'): '性格适中，看场合',
            ('extraversion', 'high'): '外向热情，喜欢社交',
            ('agreeableness', 'low'): '直率坦诚，有自己的主见',
            ('agreeableness', 'mid'): '友善随和',
            ('agreeableness', 'high'): '非常体贴，在意他人感受',
            ('neuroticism', 'low'): '情绪稳定，心态平和',
            ('neuroticism', 'mid'): '偶尔会有点小情绪',
            ('neuroticism', 'high'): '敏感细腻，容易想多',
        }

        def _level(val: float) -> str:
            if val < 0.35:
                return 'low'
            elif val > 0.65:
                return 'high'
            return 'mid'

        lines = [f"{prefix}"]
        for trait in ['openness', 'conscientiousness', 'extraversion',
                       'agreeableness', 'neuroticism']:
            val = getattr(self, trait)
            desc = labels.get((trait, _level(val)), '')
            if desc:
                lines.append(f"- {desc}")
        return "\n".join(lines)

    def similarity(self, other: 'OceanTraits') -> float:
        """余弦相似度 (0~1)"""
        dot = sum(getattr(self, t) * getattr(other, t)
                  for t in ['openness', 'conscientiousness', 'extraversion',
                            'agreeableness', 'neuroticism'])
        norm1 = math.sqrt(sum(getattr(self, t) ** 2 for t in [
            'openness', 'conscientiousness', 'extraversion',
            'agreeableness', 'neuroticism']))
        norm2 = math.sqrt(sum(getattr(other, t) ** 2 for t in [
            'openness', 'conscientiousness', 'extraversion',
            'agreeableness', 'neuroticism']))
        if norm1 * norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def blend(self, other: 'OceanTraits', weight: float = 0.3) -> 'OceanTraits':
        """加权融合: self * (1-w) + other * w"""
        return OceanTraits(
            openness=self.openness * (1 - weight) + other.openness * weight,
            conscientiousness=self.conscientiousness * (1 - weight) + other.conscientiousness * weight,
            extraversion=self.extraversion * (1 - weight) + other.extraversion * weight,
            agreeableness=self.agreeableness * (1 - weight) + other.agreeableness * weight,
            neuroticism=self.neuroticism * (1 - weight) + other.neuroticism * weight,
        )

    def __repr__(self) -> str:
        return (f"Ocean(O={self.openness:.2f}, C={self.conscientiousness:.2f}, "
                f"E={self.extraversion:.2f}, A={self.agreeableness:.2f}, "
                f"N={self.neuroticism:.2f})")


# ---------------------------------------------------------------------------
#  PAD 情感三轴模型 (Pleasure-Arousal-Dominance)
# ---------------------------------------------------------------------------

# 12种基本情绪在PAD空间中的映射 (来源: Agethos EmotionalState + PAD理论)
PAD_EMOTION_MAP: Dict[str, Tuple[float, float, float]] = {
    "开心":     (0.8,  0.6,  0.5),   # Joy
    "伤心":     (-0.7, 0.1, -0.4),   # Sadness
    "生气":     (-0.5, 0.7,  0.6),   # Anger
    "撒娇":     (0.7,  0.3, -0.2),   # Lovely/Playful
    "吃醋":     (0.1,  0.5,  0.1),   # Jealousy
    "傲娇":     (-0.2, 0.4, -0.1),   # Sullen/Tsundere
    "温柔":     (0.7, -0.2, -0.1),   # Caring
    "调皮":     (0.6,  0.7,  0.2),   # Playful
    "疲惫":     (-0.3, -0.6, -0.4),  # Tired
    "平常":     (0.0,  0.0,  0.0),   # Neutral
    "焦虑":     (-0.3, 0.6, -0.3),   # Anxious
    "感动":     (0.8,  0.4,  0.1),   # Moved
}


@dataclass
class PadState:
    """PAD 3轴情感状态

    Attributes:
        pleasure: 愉悦度 (-1.0 ~ 1.0), 正=愉快, 负=不愉快
        arousal:  激活度 (-1.0 ~ 1.0), 正=兴奋, 负=平静
        dominance: 支配度 (-1.0 ~ 1.0), 正=主导, 负=顺从
    """
    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def __post_init__(self):
        self.pleasure = max(-1.0, min(1.0, self.pleasure))
        self.arousal = max(-1.0, min(1.0, self.arousal))
        self.dominance = max(-1.0, min(1.0, self.dominance))

    @classmethod
    def from_ocean(cls, ocean: OceanTraits) -> 'PadState':
        """从OCEAN计算基线PAD (Agethos算法)

        基线映射:
          - Pleasure  ← Agreeableness + (1 - Neuroticism)
          - Arousal   ← Extraversion
          - Dominance ← Conscientiousness + (1 - Agreeableness)
        """
        pleasure = ocean.agreeableness * 0.7 + (1 - ocean.neuroticism) * 0.3
        arousal = ocean.extraversion * 1.0
        dominance = ocean.conscientiousness * 0.6 + (1 - ocean.agreeableness) * 0.4
        return cls(
            pleasure=pleasure * 2 - 1,   # 映射到 -1~1
            arousal=arousal * 2 - 1,
            dominance=dominance * 2 - 1,
        )

    @classmethod
    def from_emotion(cls, emotion_name: str) -> 'PadState':
        """从情感名称获取PAD值"""
        if emotion_name in PAD_EMOTION_MAP:
            p, a, d = PAD_EMOTION_MAP[emotion_name]
            return cls(pleasure=p, arousal=a, dominance=d)
        return cls()

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> 'PadState':
        return cls(
            pleasure=data.get('pleasure', 0.0),
            arousal=data.get('arousal', 0.0),
            dominance=data.get('dominance', 0.0),
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            'pleasure': round(self.pleasure, 3),
            'arousal': round(self.arousal, 3),
            'dominance': round(self.dominance, 3),
        }

    def to_prompt_segment(self) -> str:
        """PAD → 可读文本"""
        parts = []
        if self.pleasure > 0.3:
            parts.append("心情愉快")
        elif self.pleasure < -0.3:
            parts.append("心情低落")
        else:
            parts.append("心情平稳")

        if self.arousal > 0.3:
            parts.append("精力充沛")
        elif self.arousal < -0.3:
            parts.append("安静平和")
        else:
            parts.append("状态平稳")

        if self.dominance > 0.3:
            parts.append("态度主动")
        elif self.dominance < -0.3:
            parts.append("态度温顺")
        else:
            parts.append("态度中立")

        return "，".join(parts)

    def closest_emotion(self) -> str:
        """找最近的PAD情感映射"""
        min_dist = float('inf')
        closest = "平常"
        for name, (p, a, d) in PAD_EMOTION_MAP.items():
            dist = math.sqrt(
                (self.pleasure - p) ** 2 +
                (self.arousal - a) ** 2 +
                (self.dominance - d) ** 2
            )
            if dist < min_dist:
                min_dist = dist
                closest = name
        return closest

    def blend(self, other: 'PadState', weight: float = 0.3) -> 'PadState':
        """加权融合: self * (1-w) + other * w"""
        return PadState(
            pleasure=self.pleasure * (1 - weight) + other.pleasure * weight,
            arousal=self.arousal * (1 - weight) + other.arousal * weight,
            dominance=self.dominance * (1 - weight) + other.dominance * weight,
        )

    def decay(self, rate: float = 0.1) -> None:
        """情感衰减 → 趋向中性"""
        self.pleasure *= (1 - rate)
        self.arousal *= (1 - rate)
        self.dominance *= (1 - rate)
        # 防止 -0.0 问题
        if abs(self.pleasure) < 0.01:
            self.pleasure = 0.0
        if abs(self.arousal) < 0.01:
            self.arousal = 0.0
        if abs(self.dominance) < 0.01:
            self.dominance = 0.0

    def apply_stimulus(self, delta_pleasure: float = 0.0,
                       delta_arousal: float = 0.0,
                       delta_dominance: float = 0.0) -> None:
        """应用情感刺激"""
        self.pleasure = max(-1.0, min(1.0, self.pleasure + delta_pleasure))
        self.arousal = max(-1.0, min(1.0, self.arousal + delta_arousal))
        self.dominance = max(-1.0, min(1.0, self.dominance + delta_dominance))

    def __repr__(self) -> str:
        return f"Pad(P={self.pleasure:.2f}, A={self.arousal:.2f}, D={self.dominance:.2f})"


# ---------------------------------------------------------------------------
#  5维风格向量 (StyleVector)
# ---------------------------------------------------------------------------

@dataclass
class StyleVector:
    """5维风格表达向量

    用于增强 ToneMimic 的风格分析，可随时间演化。

    Dimensions (0.0~1.0):
      - formality:    正式度 (0=非常随意, 1=非常正式)
      - expressiveness: 情感表达度 (0=内敛, 1=外露)
      - humor:        幽默感 (0=严肃, 1=调皮)
      - directness:   直接度 (0=含蓄, 1=直白)
      - sentiment:    情感倾向 (0=负面, 0.5=中性, 1=正面)
    """
    formality: float = 0.3
    expressiveness: float = 0.7
    humor: float = 0.5
    directness: float = 0.5
    sentiment: float = 0.6

    def __post_init__(self):
        for dim in ['formality', 'expressiveness', 'humor',
                     'directness', 'sentiment']:
            val = getattr(self, dim)
            setattr(self, dim, max(0.0, min(1.0, val)))

    @classmethod
    def from_ocean(cls, ocean: OceanTraits) -> 'StyleVector':
        """从OCEAN推断风格向量"""
        return cls(
            formality=ocean.conscientiousness * 0.6 + (1 - ocean.openness) * 0.4,
            expressiveness=ocean.extraversion * 0.7 + ocean.agreeableness * 0.3,
            humor=ocean.openness * 0.5 + ocean.extraversion * 0.5,
            directness=(1 - ocean.agreeableness) * 0.6 + ocean.extraversion * 0.4,
            sentiment=(1 - ocean.neuroticism) * 0.5 + ocean.agreeableness * 0.5,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> 'StyleVector':
        return cls(
            formality=data.get('formality', 0.3),
            expressiveness=data.get('expressiveness', 0.7),
            humor=data.get('humor', 0.5),
            directness=data.get('directness', 0.5),
            sentiment=data.get('sentiment', 0.6),
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            'formality': round(self.formality, 3),
            'expressiveness': round(self.expressiveness, 3),
            'humor': round(self.humor, 3),
            'directness': round(self.directness, 3),
            'sentiment': round(self.sentiment, 3),
        }

    def to_prompt_segment(self, prefix: str = "[用户风格]") -> str:
        """生成可注入 system prompt 的风格描述"""
        lines = [f"{prefix}"]
        if self.formality < 0.3:
            lines.append("- 说话非常随意自然")
        elif self.formality < 0.6:
            lines.append("- 说话适度放松")
        else:
            lines.append("- 说话偏正式")

        if self.expressiveness > 0.6:
            lines.append("- 情感表达丰富外露")
        else:
            lines.append("- 情感表达比较内敛")

        if self.humor > 0.6:
            lines.append("- 喜欢开玩笑")
        else:
            lines.append("- 比较正经")

        if self.directness > 0.6:
            lines.append("- 说话直来直去")
        else:
            lines.append("- 说话比较委婉")

        if self.sentiment > 0.6:
            lines.append("- 整体情绪偏正面")
        elif self.sentiment < 0.4:
            lines.append("- 整体情绪偏负面")
        else:
            lines.append("- 情绪中性")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
#  UserPersona — 用户人格画像 (完整)
# ---------------------------------------------------------------------------

@dataclass
class UserPersonaSnapshot:
    """用户人格快照（单次检测结果）"""
    timestamp: str = ""  # ISO datetime
    ocean: OceanTraits = field(default_factory=OceanTraits)
    pad: PadState = field(default_factory=PadState)
    style: StyleVector = field(default_factory=StyleVector)
    confidence: float = 0.5  # 检测置信度 0~1
    source: str = ""  # 检测来源: "pado", "rule", "llm"
    trigger_message: str = ""  # 触发检测的消息

    def to_dict(self) -> dict:
        return {
            'timestamp': self.timestamp,
            'ocean': self.ocean.to_dict(),
            'pad': self.pad.to_dict(),
            'style': self.style.to_dict(),
            'confidence': round(self.confidence, 3),
            'source': self.source,
            'trigger_message': self.trigger_message[:100],
        }


@dataclass
class UserPersona:
    """用户人格画像（融合+演化）

    以 Bayesian 方式融合多次检测结果:
      - ocean: 当前最优估计（加权平均）
      - pad: 当前情感状态（PAD）
      - style: 当前风格向量
      - snapshot_count: 检测总次数
      - first_seen / last_updated: 时间戳
    """
    user_id: str = "default"
    ocean: OceanTraits = field(default_factory=OceanTraits)
    pad: PadState = field(default_factory=PadState)
    style: StyleVector = field(default_factory=StyleVector)
    snapshot_count: int = 0
    first_seen: str = ""
    last_updated: str = ""
    _history: List[UserPersonaSnapshot] = field(default_factory=list)
    # 新增心理维度
    hexaco: Optional[dict] = None     # HEXACO 六因素
    dark_triad: Optional[dict] = None # 暗黑三人格
    mental_health: Optional[dict] = None  # 心理健康摘要
    liwc: Optional[dict] = None       # LIWC 心理语言学
    cognitive: Optional[dict] = None  # 认知扭曲摘要

    def apply_snapshot(self, snapshot: UserPersonaSnapshot) -> None:
        """融合新快照（Bayesian加权更新）"""
        if not snapshot.ocean:
            return

        if self.snapshot_count == 0:
            # 第一次检测，直接替换
            self.ocean = snapshot.ocean
            self.pad = snapshot.pad
            self.style = snapshot.style
            self.first_seen = snapshot.timestamp
        else:
            # 加权融合：confidence越高权重越大
            w = min(0.5, snapshot.confidence * 0.4 + 0.1)  # 10~50%
            self.ocean = self.ocean.blend(snapshot.ocean, w)
            # PAD 和 Style 近实时更新
            self.pad = self.pad.blend(snapshot.pad, 0.3)
            self.style = StyleVector(
                formality=self.style.formality * 0.7 + snapshot.style.formality * 0.3,
                expressiveness=self.style.expressiveness * 0.7 + snapshot.style.expressiveness * 0.3,
                humor=self.style.humor * 0.7 + snapshot.style.humor * 0.3,
                directness=self.style.directness * 0.7 + snapshot.style.directness * 0.3,
                sentiment=self.style.sentiment * 0.7 + snapshot.style.sentiment * 0.3,
            )

        self.snapshot_count += 1
        self.last_updated = snapshot.timestamp
        self._history.append(snapshot)

        # 保留最近 100 条历史
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def get_stable_ocean(self, min_samples: int = 3) -> OceanTraits:
        """返回稳定版OCEAN（样本数不足时返回默认）"""
        if self.snapshot_count >= min_samples:
            return self.ocean
        return OceanTraits()  # 默认中间值

    def get_style_adapter(self) -> Dict[str, float]:
        """生成给 tone_mimic 的风格适配参数"""
        return self.style.to_dict()

    def get_pad_adapter(self) -> Dict[str, float]:
        """生成给 emotion_engine 的PAD适配参数"""
        return self.pad.to_dict()

    def to_dict(self) -> dict:
        d = {
            'user_id': self.user_id,
            'ocean': self.ocean.to_dict(),
            'pad': self.pad.to_dict(),
            'style': self.style.to_dict(),
            'snapshot_count': self.snapshot_count,
            'first_seen': self.first_seen,
            'last_updated': self.last_updated,
            'recent_history': [s.to_dict() for s in self._history[-5:]],
        }
        if self.hexaco:
            d['hexaco'] = self.hexaco
        if self.dark_triad:
            d['dark_triad'] = self.dark_triad
        if self.mental_health:
            d['mental_health'] = self.mental_health
        if self.liwc:
            d['liwc'] = self.liwc
        if self.cognitive:
            d['cognitive'] = self.cognitive
        return d

    def to_prompt_enhancement(self) -> str:
        """生成增强 system prompt 的人格段"""
        parts = []
        ocean_text = self.get_stable_ocean().to_prompt_segment()
        if ocean_text:
            parts.append(ocean_text)
        style_text = self.style.to_prompt_segment()
        if style_text:
            parts.append(style_text)

        # HEXACO 人格
        if self.hexaco:
            try:
                from .hexaco import HexacoTraits
                h = HexacoTraits(**self.hexaco) if isinstance(self.hexaco, dict) else self.hexaco
                parts.append(h.to_prompt_segment())
            except Exception:
                pass

        # 暗黑人格提示
        if self.dark_triad and isinstance(self.dark_triad, dict):
            parts.append(self._dark_triad_prompt(self.dark_triad))

        # 心理健康提示
        if self.mental_health and isinstance(self.mental_health, dict):
            risk = self.mental_health.get("overall_risk", "low")
            if risk in ("high", "critical"):
                parts.append("[心理健康提示] 用户当前心理状态需要特别关注，回应时保持温和、支持、非评判的态度。避免刺激性和负面话题。")
            elif risk == "moderate":
                parts.append("[心理健康提示] 用户可能有轻微情绪困扰，回应时保持支持和理解。")

        # 认知扭曲提示
        if self.cognitive and isinstance(self.cognitive, dict):
            severity = self.cognitive.get("severity", "none")
            dominant = self.cognitive.get("dominant_pattern", "")
            if severity in ("moderate", "frequent") and dominant:
                dist_labels = {
                    "all_or_nothing": "全或无思维", "overgeneralization": "过度概括",
                    "mental_filter": "心理过滤", "disqualifying_positive": "否定正面",
                    "jumping_to_conclusions": "妄下结论", "magnification": "灾难化",
                    "emotional_reasoning": "情绪推理", "should_statements": "应该陈述",
                    "labeling": "贴标签", "personalization": "个人化",
                }
                label = dist_labels.get(dominant, dominant)
                parts.append(f"[认知扭曲提示] 用户表现出'{label}'的思维模式，回应时避免强化，提供温和的替代视角。")

        return "\n\n".join(parts)

    @staticmethod
    def _dark_triad_prompt(dt: dict) -> str:
        avg = (dt.get("narcissism", 0) + dt.get("machiavellianism", 0) + dt.get("psychopathy", 0)) / 3
        if avg <= 0.25:
            return ""
        lines = ["[暗黑人格提示]"]
        if dt.get("narcissism", 0) > 0.3:
            lines.append("- 用户可能较自我中心，适当给予肯定但避免过度迎合")
        if dt.get("machiavellianism", 0) > 0.3:
            lines.append("- 用户可能有功利倾向，注意保持真诚")
        if dt.get("psychopathy", 0) > 0.3:
            lines.append("- 用户可能缺乏共情，需要温和引导")
        return "\n".join(lines) if len(lines) > 1 else ""
