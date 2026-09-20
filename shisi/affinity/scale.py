"""亲密度刻度唯一真源（2026-09-20 用户裁决「彻底重构，一切为了效果」）。

**权威数值**：`affection_points ∈ [0, 500]`（EmotionEngine 持有；
满级 = `AffinityLevel.BOND.threshold`）。

**派生视图**（一律经本模块换算，禁止各处自写 magic number）：

| 视图 | 刻度 | 换算 |
|------|------|------|
| affinity_level | 0–8 整数档 | `points → AffinityLevel` 阈值表 |
| shisi_affinity | 0–100 | `points / 500 * 100` |
| unlock 阈值 | 作用于 shisi 0–100 | 25/50/75/90 → points 125/250/375/450 |
| 工具权限档 | 0/2/6/99 → 落在 level 0–8 | 由 level 消费 |

反转：`shisi_to_points` / `level_to_min_points`。
"""

from __future__ import annotations

from shisi.core.models import AffinityLevel

# 权威满级
POINTS_MAX: float = float(AffinityLevel.BOND.threshold)  # 500
# shisi 亲密度刻度（AffinityEnhancer 默认 min/max）
SHISI_MIN: float = 0.0
SHISI_MAX: float = 100.0
# 解锁阈值（作用于 shisi 0–100，config/shisi.yaml affinity.unlocks）
UNLOCK_THRESHOLDS_SHISI: tuple[int, ...] = (25, 50, 75, 90)

# AffinityLevel → 该档最小 affection_points（来自 AffinityLevel.threshold 表）
_LEVEL_MIN_POINTS: tuple[int, ...] = tuple(
    lv.threshold for lv in AffinityLevel
)  # (0, 10, 25, 50, 80, 120, 200, 350, 500)


def clamp_points(points: float) -> float:
    return max(0.0, min(POINTS_MAX, float(points)))


def points_to_level(points: float) -> int:
    """affection_points → 0–8 档位。"""
    p = clamp_points(points)
    level = 0
    for i, th in enumerate(_LEVEL_MIN_POINTS):
        if p >= th:
            level = i
    return level


def level_to_min_points(level: int) -> float:
    """档位 → 该档最小 affection_points。"""
    lv = max(0, min(8, int(level)))
    return float(_LEVEL_MIN_POINTS[lv])


def points_to_shisi(points: float,
                    shisi_min: float = SHISI_MIN,
                    shisi_max: float = SHISI_MAX) -> float:
    """affection_points → shisi affinity（默认 0–100）。"""
    if POINTS_MAX <= 0 or shisi_max <= shisi_min:
        return shisi_min
    ratio = clamp_points(points) / POINTS_MAX
    return max(shisi_min, min(shisi_max, shisi_min + ratio * (shisi_max - shisi_min)))


def shisi_to_points(shisi: float,
                    shisi_min: float = SHISI_MIN,
                    shisi_max: float = SHISI_MAX) -> float:
    """shisi affinity → affection_points。"""
    if shisi_max <= shisi_min:
        return 0.0
    clamped = max(shisi_min, min(shisi_max, float(shisi)))
    ratio = (clamped - shisi_min) / (shisi_max - shisi_min)
    return ratio * POINTS_MAX


def unlock_threshold_to_points(threshold: int | float,
                               shisi_min: float = SHISI_MIN,
                               shisi_max: float = SHISI_MAX) -> float:
    """解锁阈值（shisi 刻度）→ affection_points 等价阈值。"""
    return shisi_to_points(threshold, shisi_min, shisi_max)


def normalize_from_dict(payload: dict) -> dict:
    """从任意形态状态载荷抽出权威 points，并补齐派生视图（供持久化/序列化）。"""
    points = float(payload.get("affection_points", payload.get("points", 0.0)) or 0.0)
    points = clamp_points(points)
    level = int(payload.get("affinity_level", payload.get("affinity", points_to_level(points))) or 0)
    # 权威是 points；level 若与 points 不一致，以 points 为准
    level = points_to_level(points)
    return {
        "affection_points": points,
        "affinity_level": level,
        "shisi_affinity": points_to_shisi(points),
        "level_name": AffinityLevel.get_name(level) if hasattr(AffinityLevel, "get_name") else AffinityLevel(level).display_name,
    }
