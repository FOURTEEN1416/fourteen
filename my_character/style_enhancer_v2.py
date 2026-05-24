"""
风格增强器V2 — 20维度（16基础+4交叉）

在现有16维度基础上新增4个交叉维度，
实现维度间依赖关系建模。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("style_enhancer_v2")


@dataclass
class DimensionDependency:
    dimension: str
    depends_on: List[str]
    formula: str = ""


@dataclass
class EnhancedStyleV2:
    base: Any
    cross_dimensions: Dict[str, float] = field(default_factory=dict)
    dimensions: Dict[str, float] = field(default_factory=dict)

    def to_prompt_segment(self) -> str:
        parts = []
        dim_names = {
            "sentence_length": "句长分布",
            "emoji_freq": "表情频率",
            "particle_freq": "语气词频率",
            "rhetorical_devices": "修辞手法",
            "intimacy_expression": "亲密表达",
            "formality": "正式程度",
            "emotion_style_cross": "情感-风格交叉",
            "context_style_cross": "情境-风格交叉",
            "relationship_style_cross": "关系-风格交叉",
            "time_style_cross": "时间-风格交叉",
        }
        for dim, val in self.dimensions.items():
            name = dim_names.get(dim, dim)
            if abs(val) > 0.01:
                parts.append(f"{name}: {val:.2f}")
        return "风格维度: " + ", ".join(parts[:8]) if parts else ""


DEFAULT_DEPENDENCIES = [
    DimensionDependency("emoji_freq", ["emotion_style_cross", "relationship_style_cross"], "受情感和关系联合调节"),
    DimensionDependency("intimacy_expression", ["relationship_style_cross", "emotion_style_cross"], "受关系和情感联合调节"),
    DimensionDependency("sentence_length", ["time_style_cross", "emotion_style_cross"], "受时间和情感联合调节"),
    DimensionDependency("formality", ["relationship_style_cross", "context_style_cross"], "受关系和情境联合调节"),
    DimensionDependency("rhetorical_devices", ["emotion_style_cross", "context_style_cross"], "受情感和情境联合调节"),
]


class StyleEnhancerV2:
    """风格增强器V2 — 20维度（16基础+4交叉）"""

    CROSS_DIMENSIONS = [
        "emotion_style_cross",
        "context_style_cross",
        "relationship_style_cross",
        "time_style_cross",
    ]

    def __init__(self, base_enhancer: Optional[Any] = None):
        self._base = base_enhancer
        self._dimension_dependencies: List[DimensionDependency] = list(DEFAULT_DEPENDENCIES)

    def enhance_style(
        self,
        base_style: Dict[str, Any],
        emotion_state: Optional[Dict] = None,
        context: Optional[Any] = None,
        chat_history: Optional[List[Dict]] = None,
        persona_style: Optional[Dict[str, Any]] = None,
    ) -> EnhancedStyleV2:
        base_result = None
        if self._base and hasattr(self._base, "enhance_style"):
            try:
                base_result = self._base.enhance_style(base_style, chat_history=chat_history, persona_style=persona_style)
            except Exception as e:
                logger.debug("Base enhancer failed: %s", e)

        base_dims = {}
        if base_result and hasattr(base_result, "dimensions"):
            base_dims = dict(base_result.dimensions)
        else:
            base_dims = dict(base_style)

        cross_dims = self._calculate_cross_dimensions(base_dims, emotion_state, context)
        resolved = self._resolve_dependencies(base_dims, cross_dims)

        return EnhancedStyleV2(
            base=base_result,
            cross_dimensions=cross_dims,
            dimensions=resolved,
        )

    def _calculate_cross_dimensions(
        self,
        base_dims: Dict[str, float],
        emotion_state: Optional[Dict],
        context: Optional[Any],
    ) -> Dict[str, float]:
        cross = {}

        if emotion_state:
            emotion_type = "平常"
            if isinstance(emotion_state, dict):
                primary = emotion_state.get("primary", {})
                if isinstance(primary, dict):
                    emotion_type = primary.get("type", "平常")
                else:
                    emotion_type = str(primary) if primary else "平常"
            elif hasattr(emotion_state, "primary_emotion"):
                emotion_type = emotion_state.primary_emotion.value
            cross["emotion_style_cross"] = self._emotion_style_interaction(emotion_type, base_dims)

        if context and hasattr(context, "time_context") and context.time_context:
            cross["context_style_cross"] = self._context_style_interaction(context.time_context, base_dims)

        if emotion_state:
            affinity = 0
            if isinstance(emotion_state, dict):
                affinity = emotion_state.get("affinity", 0)
            elif hasattr(emotion_state, "affinity"):
                affinity = emotion_state.affinity
            cross["relationship_style_cross"] = self._relationship_style_interaction(affinity, base_dims)

        if context and hasattr(context, "time_context") and context.time_context:
            cross["time_style_cross"] = self._time_style_interaction(context.time_context, base_dims)

        return cross

    def _emotion_style_interaction(self, emotion_type: str, base_dims: Dict[str, float]) -> float:
        EMOTION_EFFECT = {
            "开心": 0.15, "撒娇": 0.2, "傲娇": -0.1,
            "生气": -0.2, "伤心": -0.15, "温柔": 0.1,
            "吃醋": 0.05, "害怕": -0.1, "害羞": 0.05,
        }
        return EMOTION_EFFECT.get(emotion_type, 0.0)

    def _context_style_interaction(self, time_ctx: Any, base_dims: Dict[str, float]) -> float:
        period = getattr(time_ctx, "period", "afternoon")
        CONTEXT_EFFECT = {
            "late_night": -0.15, "night": -0.1,
            "morning": 0.05, "forenoon": 0.0,
            "afternoon": 0.0, "evening": 0.05,
        }
        return CONTEXT_EFFECT.get(period, 0.0)

    def _relationship_style_interaction(self, affinity: int, base_dims: Dict[str, float]) -> float:
        if affinity >= 7:
            return 0.2
        elif affinity >= 5:
            return 0.1
        elif affinity >= 3:
            return 0.0
        return -0.1

    def _time_style_interaction(self, time_ctx: Any, base_dims: Dict[str, float]) -> float:
        period = getattr(time_ctx, "period", "afternoon")
        is_weekend = getattr(time_ctx, "is_weekend", False)
        base = {"late_night": -0.2, "night": -0.1, "morning": 0.05}.get(period, 0.0)
        if is_weekend:
            base += 0.05
        return base

    def _resolve_dependencies(
        self,
        base_dims: Dict[str, float],
        cross_dims: Dict[str, float],
    ) -> Dict[str, float]:
        resolved = dict(base_dims)

        for dep in self._dimension_dependencies:
            if dep.dimension in resolved:
                cross_influence = 0.0
                for source in dep.depends_on:
                    cross_influence += cross_dims.get(source, 0.0)
                cross_influence /= max(len(dep.depends_on), 1)
                resolved[dep.dimension] = round(
                    max(0.0, min(1.0, resolved[dep.dimension] + cross_influence * 0.3)), 4
                )

        for dim, val in cross_dims.items():
            if dim not in resolved:
                resolved[dim] = round(val, 4)

        return resolved

    def health_check(self) -> dict:
        return {
            "base_loaded": self._base is not None,
            "cross_dimensions": len(self.CROSS_DIMENSIONS),
            "dependencies_count": len(self._dimension_dependencies),
        }
