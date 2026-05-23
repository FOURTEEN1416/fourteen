"""
PAD 3轴情感耦合器 — 连接人格检测与情感引擎

核心职责:
  1. 将原系统 EmotionEngine 的单一情感标签映射到 PAD 3轴空间
  2. 从用户消息中提取 PAD 信号，供人格检测参考
  3. 实现"Chameleon效应"隔离：区分用户真实人格 vs 被对话感染的临时情绪
  4. 提供双向转换: Emotion <-> PAD <-> Ocean

设计:
  - 情绪 → PAD: 查表 PAD_EMOTION_MAP
  - PAD → 情绪: 最近邻查找 closest_emotion()
  - OCEAN → 基线PAD: Agethos算法 from_ocean()
  - Chameleon隔离: 短消息/高情绪消息降低PAD权重
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Tuple

from .models import OceanTraits, PadState

logger = logging.getLogger("emotion_coupler")

# 消息长度阈值（字符数）
SHORT_MSG_THRESHOLD = 15
HIGH_EMOTION_THRESHOLD = 0.7


class EmotionCoupler:
    """情感耦合器 — 连接 EmotionEngine <-> PAD <-> PersonaExtractor

    用法:
        coupler = EmotionCoupler()
        pad = coupler.emotion_to_pad("开心", 0.8)
        emotion_name = coupler.pad_to_emotion(pad)
        pad_blended = coupler.blend_with_ocean(pad, ocean)
    """

    def __init__(self, chameleon_threshold: float = 0.5):
        self.chameleon_threshold = chameleon_threshold

    # ── 正向转换: Emotion → PAD ──

    def emotion_to_pad(self, emotion_name: str, intensity: float = 0.5) -> PadState:
        """将原系统情感转换为 PAD 3轴

        Args:
            emotion_name: 情感名称（如 "开心", "生气"）
            intensity: 情感强度 0~1

        Returns:
            PadState: PAD值（已按intensity缩放）
        """
        pad = PadState.from_emotion(emotion_name)
        # 按强度缩放（中性点*0 + 极值*intensity）
        pad.pleasure *= intensity
        pad.arousal *= intensity
        pad.dominance *= intensity
        return pad

    def batch_emotions_to_pad(
        self, emotions: List[Tuple[str, float]]
    ) -> List[PadState]:
        """批量转换情感列表为PAD"""
        return [self.emotion_to_pad(name, intensity)
                for name, intensity in emotions]

    # ── 反向转换: PAD → Emotion ──

    def pad_to_emotion(self, pad: PadState) -> Tuple[str, float]:
        """将PAD反向映射到最近的情感名称+匹配度

        Returns:
            (emotion_name, match_score 0~1)
        """
        closest = pad.closest_emotion()
        # 计算匹配度 (余弦距离归一化)
        target = PadState.from_emotion(closest)
        dist = math.sqrt(
            (pad.pleasure - target.pleasure) ** 2 +
            (pad.arousal - target.arousal) ** 2 +
            (pad.dominance - target.dominance) ** 2
        )
        # 最大可能距离 = sqrt(2^2+2^2+2^2) = 3.464
        match = max(0.0, 1.0 - dist / 3.464)
        return closest, match

    # ── Chameleon 效应隔离 ──

    def chameleon_filter(
        self,
        pad: PadState,
        message_len: int,
        emotion_intensity: float,
    ) -> PadState:
        """Chameleon 效应隔离

        当消息很短或情感强度很高时，
        用户可能只是在"响应"AI虚拟伴侣的情感，
        而非表达自己的真实人格。

        处理:
          - 短消息 (<15字): 降低PAD权重 (压缩到中性)
          - 高情绪消息 (intensity > 0.7): 怀疑是Chameleon效应
        """
        # 压缩系数 (0~1, 1=完全不压缩, 0=压缩到中性)
        compression = 1.0

        if message_len < SHORT_MSG_THRESHOLD:
            factor = message_len / SHORT_MSG_THRESHOLD
            compression *= factor
            logger.debug("Chameleon: short msg, compression=%.2f", compression)

        if emotion_intensity > HIGH_EMOTION_THRESHOLD:
            factor = 1.0 - (emotion_intensity - HIGH_EMOTION_THRESHOLD)
            compression *= max(0.3, factor)
            logger.debug("Chameleon: high emotion, compression=%.2f", compression)

        if compression < 1.0:
            # 向中性压缩
            return PadState(
                pleasure=pad.pleasure * compression,
                arousal=pad.arousal * compression,
                dominance=pad.dominance * compression,
            )

        return pad

    # ── OCEAN-PAD 耦合 ──

    def get_ocean_adjusted_pad(self, ocean: OceanTraits, pad: PadState) -> PadState:
        """获取 OCEAN 调整后的 PAD (融合基线和当前)

        基线来自 OCEAN (Agethos算法)，当前来自即时情感检测。
        融合使情感表达不脱离人格基线太远。
        """
        baseline = PadState.from_ocean(ocean)

        # 允许 ±0.3 的浮动范围
        return PadState(
            pleasure=max(-1.0, min(1.0, baseline.pleasure + pad.pleasure * 0.3)),
            arousal=max(-1.0, min(1.0, baseline.arousal + pad.arousal * 0.3)),
            dominance=max(-1.0, min(1.0, baseline.dominance + pad.dominance * 0.3)),
        )

    # ── 与 EmotionEngine 集成 ──

    def adapt_to_emotion_engine(
        self,
        pad: PadState,
        engine_emotion_name: str,
    ) -> Dict[str, Any]:
        """将PAD适配到EmotionEngine的输入格式

        返回提供给 EmotionEngine.analyze() 的附加上下文
        """
        return {
            "pad_pleasure": round(pad.pleasure, 2),
            "pad_arousal": round(pad.arousal, 2),
            "pad_dominance": round(pad.dominance, 2),
            "pad_source_emotion": engine_emotion_name,
            "pad_closest_emotion": self.pad_to_emotion(pad)[0],
        }

    def get_emotion_intensity(
        self,
        pad: PadState,
        emotion_name: str,
    ) -> float:
        """从PAD计算指定情感的强度"""
        target = PadState.from_emotion(emotion_name)
        dist = math.sqrt(
            (pad.pleasure - target.pleasure) ** 2 +
            (pad.arousal - target.arousal) ** 2 +
            (pad.dominance - target.dominance) ** 2
        )
        return max(0.0, 1.0 - dist / 3.464)
