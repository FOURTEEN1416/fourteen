"""亲密度持久化（B6 效果项：进程重启不丢 affection_points）。

权威数值 `affection_points` 存 `data/affinity_state.json`，
键为 `user_id::character_id`。派生档位一律经 `shisi.affinity.scale`。

持久化委托 `utils.json_state`（原子写 + 跨进程锁）：旧实现直接
``write_text`` 整体覆写，写一半进程退出即**全量用户好感度损坏**。
"""

from __future__ import annotations

import logging

from shisi.affinity import scale
from utils import json_state
from utils.project_paths import project_path

logger = logging.getLogger("utils.affinity_state")

_PATH = project_path("data", "affinity_state.json")


def _key(user_id: str, character_id: str) -> str:
    return f"{user_id}::{character_id}"


def load_points(user_id: str, character_id: str) -> float:
    raw = json_state.read_json(_PATH, default={}).get(_key(user_id, character_id), {})
    points = float(raw.get("affection_points", 0.0) or 0.0)
    return scale.clamp_points(points)


def save_points(user_id: str, character_id: str, points: float) -> None:
    points = scale.clamp_points(points)

    def _mutate(data: dict) -> None:
        data[_key(user_id, character_id)] = {
            "affection_points": points,
            "affinity_level": scale.points_to_level(points),
            "shisi_affinity": scale.points_to_shisi(points),
        }

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("写入 affinity_state 失败: %s", e)


def clear(user_id: str, character_id: str) -> None:
    def _mutate(data: dict) -> None:
        data.pop(_key(user_id, character_id), None)

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("清除 affinity_state 失败: %s", e)
