"""
人设增强公共工具 — 消除跨模块重复逻辑

提供 emotion_state 属性提取、AnchorContext 构建、
TimeContext 提取等公共函数。
"""

from __future__ import annotations

from typing import Any


def extract_emotion_attrs(emotion_state: Any) -> tuple[int, str, float]:
    """从 emotion_state 提取 affinity, emotion_type, energy

    Returns:
        (affinity, emotion_type, energy) — 默认 (0, "平常", 1.0)
    """
    affinity = 0
    emotion_type = "平常"
    energy = 1.0

    if emotion_state is None:
        return affinity, emotion_type, energy

    if hasattr(emotion_state, "affinity"):
        affinity = emotion_state.affinity
    if hasattr(emotion_state, "primary_emotion"):
        pe = emotion_state.primary_emotion
        if pe is not None:
            emotion_type = pe.value if hasattr(pe, "value") else str(pe)
    if hasattr(emotion_state, "energy"):
        energy = emotion_state.energy

    return affinity, emotion_type, energy


def build_anchor_context(
    emotion_state: Any = None,
    chat_round: int = 0,
    affinity_override: int | None = None,
) -> Any:
    """从 emotion_state 构建 AnchorContext

    Args:
        emotion_state: CompoundEmotionalState 实例
        chat_round: 当前对话轮次
        affinity_override: 覆盖 affinity 值（优先于 emotion_state 中的值）

    Returns:
        AnchorContext 实例，或 None（模块不可用时）
    """
    try:
        from my_character.dynamic_anchor import AnchorContext
    except ImportError:
        return None

    affinity, emotion_type, energy = extract_emotion_attrs(emotion_state)
    if affinity_override is not None:
        affinity = affinity_override

    return AnchorContext(
        affinity=affinity,
        energy=energy,
        emotion_type=emotion_type,
        chat_round=chat_round,
    )


def build_time_context() -> Any:
    """构建当前时间的 TimeContext

    Returns:
        TimeContext 实例，或 None（模块不可用时）
    """
    try:
        from my_character.enhanced_prompt_engine import TimeContext
        return TimeContext.now()
    except ImportError:
        return None


def extract_context_vars(emotion_state: Any = None, time_context: Any = None) -> dict[str, Any]:
    """从 emotion_state + time_context 提取行为规则所需的上下文变量

    Returns:
        dict with keys: time_period, energy, affinity, is_weekend, is_holiday
    """
    vars_ = {
        "time_period": "afternoon",
        "energy": 1.0,
        "affinity": 0,
        "is_weekend": False,
        "is_holiday": False,
    }

    if time_context:
        vars_["time_period"] = getattr(time_context, "period", "afternoon")
        vars_["is_weekend"] = getattr(time_context, "is_weekend", False)
        vars_["is_holiday"] = getattr(time_context, "is_holiday", False)

    if emotion_state:
        affinity, emotion_type, energy = extract_emotion_attrs(emotion_state)
        vars_["energy"] = energy
        vars_["affinity"] = affinity

    return vars_
