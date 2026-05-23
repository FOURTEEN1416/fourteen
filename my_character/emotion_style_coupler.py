"""
情感-风格耦合器 — 深度联动情感状态与说话风格

10种情感×9级好感度的完整映射矩阵，
支持次要情感的加权融合，动态调整说话风格。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("emotion_style_coupler")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class StyleAdjustment:
    warmth_delta: float = 0.0
    playfulness_delta: float = 0.0
    sarcasm_delta: float = 0.0
    intimacy_delta: float = 0.0
    emoji_multiplier: float = 1.0
    sentence_length: str = "medium"
    rhetorical_devices: List[str] = field(default_factory=list)
    particles: List[str] = field(default_factory=list)
    indirect_expression: bool = False


@dataclass
class CoupledStyle:
    warmth: float = 0.5
    playfulness: float = 0.5
    sarcasm: float = 0.0
    intimacy: float = 0.3
    emoji_freq: float = 0.5
    sentence_length: str = "medium"
    rhetorical_devices: List[str] = field(default_factory=list)
    particles: List[str] = field(default_factory=list)
    formality: float = 0.3
    use_nickname: bool = True
    indirect_expression: bool = False


EMOTION_STYLE_MATRIX: Dict[str, Dict[str, Any]] = {
    "开心": {
        "warmth_delta": 0.1,
        "playfulness_delta": 0.2,
        "emoji_multiplier": 1.3,
        "sentence_length": "medium",
        "rhetorical_devices": ["感叹", "夸张"],
        "particles": ["哈哈", "嘻嘻", "呀"],
    },
    "生气": {
        "warmth_delta": -0.3,
        "sarcasm_delta": 0.4,
        "emoji_multiplier": 0.3,
        "sentence_length": "short",
        "rhetorical_devices": ["反问", "讽刺"],
        "particles": ["哼", "呵", "哦"],
    },
    "伤心": {
        "warmth_delta": 0.2,
        "intimacy_delta": 0.1,
        "emoji_multiplier": 0.2,
        "sentence_length": "short",
        "rhetorical_devices": ["暗示"],
        "particles": ["...", "唉"],
    },
    "撒娇": {
        "warmth_delta": 0.2,
        "intimacy_delta": 0.3,
        "emoji_multiplier": 1.5,
        "sentence_length": "medium",
        "particles": ["嘛", "呢", "呀", "呜"],
    },
    "吃醋": {
        "warmth_delta": -0.1,
        "sarcasm_delta": 0.3,
        "emoji_multiplier": 0.4,
        "sentence_length": "short",
        "rhetorical_devices": ["反话", "暗示"],
        "particles": ["哼", "算了"],
    },
    "傲娇": {
        "warmth_delta": -0.1,
        "sarcasm_delta": 0.3,
        "indirect_expression": True,
        "rhetorical_devices": ["反话", "暗示"],
        "particles": ["才", "哼", "又"],
    },
    "担心": {
        "warmth_delta": 0.3,
        "intimacy_delta": 0.2,
        "emoji_multiplier": 0.5,
        "sentence_length": "medium",
        "particles": ["吧", "呢"],
    },
    "无聊": {
        "warmth_delta": 0.0,
        "playfulness_delta": -0.1,
        "emoji_multiplier": 0.8,
        "sentence_length": "short",
        "particles": ["啊", "嘛"],
    },
    "温柔": {
        "warmth_delta": 0.3,
        "intimacy_delta": 0.2,
        "emoji_multiplier": 1.1,
        "sentence_length": "medium",
        "particles": ["呀", "呢", "哦"],
    },
    "平常": {
        "warmth_delta": 0.0,
        "emoji_multiplier": 1.0,
        "sentence_length": "medium",
    },
}

AFFINITY_STYLE_MATRIX: Dict[int, Dict[str, Any]] = {
    0: {"formality": 0.8, "intimacy": 0.0, "use_nickname": False, "emoji_freq": 0.2, "proactivity": 0.1},
    1: {"formality": 0.6, "intimacy": 0.2, "use_nickname": False, "emoji_freq": 0.3, "proactivity": 0.2},
    2: {"formality": 0.4, "intimacy": 0.4, "use_nickname": True, "emoji_freq": 0.5, "proactivity": 0.3},
    3: {"formality": 0.3, "intimacy": 0.5, "use_nickname": True, "emoji_freq": 0.6, "proactivity": 0.5},
    4: {"formality": 0.2, "intimacy": 0.6, "use_nickname": True, "emoji_freq": 0.7, "proactivity": 0.6},
    5: {"formality": 0.1, "intimacy": 0.7, "use_nickname": True, "emoji_freq": 0.8, "proactivity": 0.7},
    6: {"formality": 0.05, "intimacy": 0.8, "use_nickname": True, "emoji_freq": 0.8, "proactivity": 0.8},
    7: {"formality": 0.0, "intimacy": 0.9, "use_nickname": True, "emoji_freq": 0.9, "proactivity": 0.9},
    8: {"formality": 0.0, "intimacy": 1.0, "use_nickname": True, "emoji_freq": 0.9, "proactivity": 1.0},
}


class EmotionStyleCoupler:
    """情感-风格耦合器"""

    def __init__(self, config_path: Optional[str] = None):
        self._emotion_matrix = EMOTION_STYLE_MATRIX
        self._affinity_matrix = AFFINITY_STYLE_MATRIX
        if config_path and HAS_YAML:
            self._load_config(config_path)

    def _load_config(self, path: str) -> None:
        try:
            p = Path(path)
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and "emotion_style_map" in data:
                    self._emotion_matrix.update(data["emotion_style_map"])
                if data and "affinity_levels" in data:
                    for k, v in data["affinity_levels"].items():
                        self._affinity_matrix[int(k)] = v
                logger.info("Loaded emotion-style config from %s", path)
        except Exception as e:
            logger.warning("Failed to load config: %s", e)

    def couple(
        self,
        emotion_state: Dict[str, Any],
        base_style: Optional[Dict[str, Any]] = None,
    ) -> CoupledStyle:
        """情感-风格耦合，输出融合了情感状态和好感度的动态风格配置"""
        base = base_style or {}
        style = CoupledStyle(
            warmth=base.get("warmth", 0.5),
            playfulness=base.get("playfulness", 0.5),
            sarcasm=base.get("sarcasm", 0.0),
            intimacy=base.get("intimacy", 0.3),
            emoji_freq=base.get("emoji_freq", 0.5),
            sentence_length=base.get("sentence_length", "medium"),
            formality=base.get("formality", 0.3),
        )

        primary = emotion_state.get("primary", {}).get("type", "平常")
        emotion_adj = self._emotion_matrix.get(primary, {})
        style = self._apply_emotion_adjustment(style, emotion_adj)

        affinity = emotion_state.get("affinity", 0)
        affinity_level = min(8, max(0, affinity))
        affinity_adj = self._affinity_matrix.get(affinity_level, {})
        style = self._apply_affinity_adjustment(style, affinity_adj)

        secondary = emotion_state.get("secondary")
        if secondary and isinstance(secondary, dict):
            style = self._blend_secondary(style, secondary)

        return style

    def _apply_emotion_adjustment(
        self, style: CoupledStyle, adj: Dict[str, Any],
    ) -> CoupledStyle:
        style.warmth = max(0.0, min(1.0, style.warmth + adj.get("warmth_delta", 0.0)))
        style.playfulness = max(0.0, min(1.0, style.playfulness + adj.get("playfulness_delta", 0.0)))
        style.sarcasm = max(0.0, min(1.0, style.sarcasm + adj.get("sarcasm_delta", 0.0)))
        style.intimacy = max(0.0, min(1.0, style.intimacy + adj.get("intimacy_delta", 0.0)))
        style.emoji_freq = max(0.0, min(1.0, style.emoji_freq * adj.get("emoji_multiplier", 1.0)))
        if adj.get("sentence_length"):
            style.sentence_length = adj["sentence_length"]
        if adj.get("rhetorical_devices"):
            style.rhetorical_devices = adj["rhetorical_devices"]
        if adj.get("particles"):
            style.particles = adj["particles"]
        if adj.get("indirect_expression"):
            style.indirect_expression = True
        return style

    def _apply_affinity_adjustment(
        self, style: CoupledStyle, adj: Dict[str, Any],
    ) -> CoupledStyle:
        style.formality = adj.get("formality", style.formality)
        style.intimacy = max(style.intimacy, adj.get("intimacy", style.intimacy))
        style.use_nickname = adj.get("use_nickname", style.use_nickname)
        style.emoji_freq = max(style.emoji_freq, adj.get("emoji_freq", style.emoji_freq))
        return style

    def _blend_secondary(
        self, style: CoupledStyle, secondary: Dict[str, Any],
    ) -> CoupledStyle:
        secondary_type = secondary.get("type", "")
        weight = secondary.get("weight", 0.3)
        sec_adj = self._emotion_matrix.get(secondary_type, {})
        if not sec_adj:
            return style

        blend = lambda a, b, w: a * (1 - w) + b * w
        style.warmth = max(0, min(1, blend(style.warmth, style.warmth + sec_adj.get("warmth_delta", 0), weight)))
        style.sarcasm = max(0, min(1, blend(style.sarcasm, style.sarcasm + sec_adj.get("sarcasm_delta", 0), weight)))
        return style

    def get_style_prompt_segment(self, style: CoupledStyle) -> str:
        """生成风格指导的提示词段"""
        parts = []

        if style.indirect_expression:
            parts.append("用间接、含蓄的方式表达，常用反话或暗示")

        if style.sarcasm > 0.3:
            parts.append("语气带点小傲娇和讽刺")

        if style.intimacy > 0.6:
            parts.append("表达亲密和依赖")
        elif style.intimacy > 0.3:
            parts.append("适度表达亲近")

        if style.emoji_freq > 0.7:
            parts.append("多用表情符号和语气词")
        elif style.emoji_freq < 0.3:
            parts.append("少用表情，语气克制")

        if style.sentence_length == "short":
            parts.append("回复简短有力")
        elif style.sentence_length == "medium":
            parts.append("回复适中")

        if style.formality > 0.5:
            parts.append("保持一定距离感")

        if style.particles:
            parts.append(f"常用语气词：{'、'.join(style.particles[:5])}")

        if style.rhetorical_devices:
            parts.append(f"可使用修辞：{'、'.join(style.rhetorical_devices[:3])}")

        return "；".join(parts) if parts else ""
