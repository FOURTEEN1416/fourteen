"""情感引擎 affection_points 与 shisi affinity 的对齐映射器。

**刻度唯一真源**：`shisi.affinity.scale`（2026-09-20 B6 彻底重构）。
- emotion 维度：affection_points 满级为 AffinityLevel.BOND.threshold (500)
- shisi 维度：AffinityEnhancer 的尺度为配置中的 [min_value, max_value]（默认 0~100）
- 映射一律走 `scale.points_to_shisi` / `scale.shisi_to_points`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shisi.affinity import scale as affinity_scale

if TYPE_CHECKING:
    from ..emotion_stage.stage_engine import EmotionStageEngine
    from .enhancer import AffinityEnhancer

# 情感引擎 affection_points 的满级刻度 = AffinityLevel 9 级阶梯的终点
_EMOTION_AFFECTION_MAX = affinity_scale.POINTS_MAX


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
        return affinity_scale.POINTS_MAX

    @staticmethod
    def points_to_level(points: float) -> int:
        """affection_points → 0–8 档（经 scale 唯一真源）。"""
        return affinity_scale.points_to_level(points)

    @staticmethod
    def points_to_shisi(points: float) -> float:
        """affection_points → shisi 0–100（不依赖 enhancer 时用默认刻度）。"""
        return affinity_scale.points_to_shisi(points)

    def to_shisi(self, affection_points: float) -> float:
        """把 emotion affection_points 映射为 shisi affinity 绝对值。"""
        if self._enhancer is None:
            raise RuntimeError("AffinityEnhancer 未设置")
        min_value = self._enhancer._min
        max_value = self._enhancer._max
        return affinity_scale.points_to_shisi(affection_points, min_value, max_value)

    def to_emotion(self, shisi_affinity: float) -> float:
        """把 shisi affinity 反向映射为 emotion affection_points。"""
        if self._enhancer is None:
            raise RuntimeError("AffinityEnhancer 未设置")
        min_value = self._enhancer._min
        max_value = self._enhancer._max
        return affinity_scale.shisi_to_points(shisi_affinity, min_value, max_value)

    @staticmethod
    def _track_key(character_id: str, user_id: str = "") -> str:
        uid = str(user_id or "").strip()
        return f"{uid}::{character_id}" if uid else str(character_id)

    def sync(
        self,
        character_id: str,
        affection_points: float,
        reason: str = "emotion_sync",
        source: str = "chat",
        max_delta: float = 3.0,
        user_id: str = "",
    ) -> dict[str, Any] | None:
        """同步一次 emotion affection_points 到 shisi 体系。

        2026-09-21：支持 user×character —— 不同用户的同一角色互不影响。
        """
        if self._enhancer is None:
            return None

        target = self.to_shisi(affection_points)
        track = self._track_key(character_id, user_id)

        if track not in self._last_shisi:
            self._last_shisi[track] = self._enhancer.get_value(
                character_id, user_id=user_id
            )

        last = self._last_shisi[track]
        delta = target - last
        if abs(delta) < 0.01:
            return {"affinity": last, "unlocks": []}

        delta = max(-max_delta, min(max_delta, delta))
        new_val, unlocks = self._enhancer.update(
            character_id, delta, reason, source, user_id=user_id
        )
        self._last_shisi[track] = new_val

        if self._stage_engine is not None:
            try:
                self._stage_engine.evaluate(track, new_val)
            except Exception:  # noqa: BLE001
                self._stage_engine.evaluate(character_id, new_val)

        return {"affinity": new_val, "unlocks": unlocks}
