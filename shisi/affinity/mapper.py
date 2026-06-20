"""情感引擎 affection_points 与 shisi affinity 的对齐映射器。

消除 `*0.05` 这类 magic number，映射公式完全基于 AffinityLevel 的阈值体系：
- emotion 维度：affection_points 满级为 AffinityLevel.BOND.threshold (500)
- shisi 维度：AffinityEnhancer 的尺度为配置中的 [min_value, max_value]（默认 0~100）
- shisi_affinity = affection_points / BOND_THRESHOLD * max_value
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shisi.core.models import AffinityLevel

if TYPE_CHECKING:
    from ..emotion_stage.stage_engine import EmotionStageEngine
    from .enhancer import AffinityEnhancer

# 情感引擎 affection_points 的满级刻度 = AffinityLevel 9 级阶梯的终点
_EMOTION_AFFECTION_MAX = float(AffinityLevel.BOND.threshold)


class AffinityMapper:
    """把 EmotionEngine 的 affection_points 同步为 shisi affinity 增量。

    追踪每个 character 上次同步后的 shisi affinity，按目标值与当前值的差分
    调用 AffinityEnhancer.update，再同步到 EmotionStageEngine。
    """

    def __init__(
        self,
        enhancer: AffinityEnhancer | None = None,
        stage_engine: EmotionStageEngine | None = None,
    ):
        self._enhancer = enhancer
        self._stage_engine = stage_engine
        self._last_shisi: dict[str, float] = {}

    def set_enhancer(self, enhancer: AffinityEnhancer) -> None:
        self._enhancer = enhancer

    def set_stage_engine(self, stage_engine: EmotionStageEngine) -> None:
        self._stage_engine = stage_engine

    @staticmethod
    def emotion_max() -> float:
        """情感引擎 affection_points 的理论上限（AffinityLevel.BOND 阈值）。"""
        return _EMOTION_AFFECTION_MAX

    def to_shisi(self, affection_points: float) -> float:
        """把 emotion affection_points 映射为 shisi affinity 绝对值。"""
        if self._enhancer is None:
            raise RuntimeError("AffinityEnhancer 未设置")
        min_value = self._enhancer._min
        max_value = self._enhancer._max
        if _EMOTION_AFFECTION_MAX <= 0 or max_value <= min_value:
            return min_value
        ratio = max(0.0, float(affection_points)) / _EMOTION_AFFECTION_MAX
        return max(min_value, min(max_value, ratio * max_value))

    def to_emotion(self, shisi_affinity: float) -> float:
        """把 shisi affinity 反向映射为 emotion affection_points。"""
        if self._enhancer is None:
            raise RuntimeError("AffinityEnhancer 未设置")
        min_value = self._enhancer._min
        max_value = self._enhancer._max
        if max_value <= min_value:
            return 0.0
        clamped = max(min_value, min(max_value, float(shisi_affinity)))
        ratio = (clamped - min_value) / (max_value - min_value)
        return ratio * _EMOTION_AFFECTION_MAX

    def sync(
        self,
        character_id: str,
        affection_points: float,
        reason: str = "emotion_sync",
        source: str = "chat",
        max_delta: float = 3.0,
    ) -> dict[str, Any] | None:
        """同步一次 emotion affection_points 到 shisi 体系。

        Returns:
            包含 ``affinity`` 和 ``unlocks`` 的字典；未初始化时返回 None。
        """
        if self._enhancer is None:
            return None

        target = self.to_shisi(affection_points)

        # 首次同步以当前 enhancer 值为基准，避免一次性跳变过大
        if character_id not in self._last_shisi:
            self._last_shisi[character_id] = self._enhancer.get_value(character_id)

        last = self._last_shisi[character_id]
        delta = target - last
        if abs(delta) < 0.01:
            return {"affinity": last, "unlocks": []}

        delta = max(-max_delta, min(max_delta, delta))
        new_val, unlocks = self._enhancer.update(character_id, delta, reason, source)
        self._last_shisi[character_id] = new_val

        if self._stage_engine is not None:
            self._stage_engine.evaluate(character_id, new_val)

        return {"affinity": new_val, "unlocks": unlocks}
