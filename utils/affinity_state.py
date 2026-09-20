"""亲密度持久化（B6 效果项：进程重启不丢 affection_points）。

权威数值 `affection_points` 存 `data/affinity_state.json`，
键为 `user_id::character_id`。派生档位一律经 `shisi.affinity.scale`。
"""

from __future__ import annotations

import json
import logging
import threading

from shisi.affinity import scale
from utils.project_paths import project_path

logger = logging.getLogger("utils.affinity_state")

_PATH = project_path("data", "affinity_state.json")
_lock = threading.Lock()


def _key(user_id: str, character_id: str) -> str:
    return f"{user_id}::{character_id}"


def _read_all() -> dict:
    try:
        if _PATH.exists():
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 affinity_state 失败: %s", e)
    return {}


def load_points(user_id: str, character_id: str) -> float:
    with _lock:
        raw = _read_all().get(_key(user_id, character_id), {})
    points = float(raw.get("affection_points", 0.0) or 0.0)
    return scale.clamp_points(points)


def save_points(user_id: str, character_id: str, points: float) -> None:
    points = scale.clamp_points(points)
    with _lock:
        data = _read_all()
        data[_key(user_id, character_id)] = {
            "affection_points": points,
            "affinity_level": scale.points_to_level(points),
            "shisi_affinity": scale.points_to_shisi(points),
        }
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("写入 affinity_state 失败: %s", e)


def clear(user_id: str, character_id: str) -> None:
    with _lock:
        data = _read_all()
        data.pop(_key(user_id, character_id), None)
        try:
            _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning("清除 affinity_state 失败: %s", e)
