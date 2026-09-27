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
    """隔离键 —— 与 `shisi.affinity.enhancer.affinity_key` **完全同构**。

    旧实现无条件 ``f"{user_id}::{character_id}"``，空 user 时得到 ``"::char"``
    而 enhancer 的 ``affinity_key(char, "")`` 返回裸 ``"char"`` —— 点存兜底
    回填永远对不上键。现空 user 退回裸 character_id（与 enhancer 一致）。
    """
    uid = str(user_id or "").strip()
    cid = str(character_id or "").strip()
    return f"{uid}::{cid}" if uid else cid


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


def purge_owner(owner_keys: list[str], owner_uid: int | None = None) -> int:
    """账号生命周期（W9）：移除归属该账号的全部点存键。

    键形态由本 owner 解析：点存键 = ``"{user_key}::{character_id}"``（user_key
    为完整会话键；历史裸 uid 键经前缀同构匹配），这里按「键以任一
    ``{owner_key}::`` 开头或恰为 owner_key」判定归属。返回移除的键数。
    """
    prefixes = tuple(f"{str(k)}::" for k in owner_keys if str(k))
    if owner_uid is not None:
        prefixes = prefixes + (f"{int(owner_uid)}:",)
    exact = {str(k) for k in owner_keys if str(k)}

    removed = 0

    # update_json 的 mutate 原地修改且不得返回非 None（返回值会被当作整份
    # 新文件内容写回）；删除计数走闭包。
    def _mutate(data: dict) -> None:
        nonlocal removed
        victims = [
            key for key in data
            if key in exact or key.startswith(prefixes)
        ]
        for key in victims:
            data.pop(key, None)
        removed = len(victims)

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("purge affinity_state 失败: %s", e)
    return int(removed)


def count_owner(owner_keys: list[str], owner_uid: int | None = None) -> int:
    prefixes = tuple(f"{str(k)}::" for k in owner_keys if str(k))
    if owner_uid is not None:
        prefixes = prefixes + (f"{int(owner_uid)}:",)
    exact = {str(k) for k in owner_keys if str(k)}
    data = json_state.read_json(_PATH, default={})
    return sum(1 for key in data if key in exact or key.startswith(prefixes))


def clear(user_id: str, character_id: str) -> None:
    def _mutate(data: dict) -> None:
        data.pop(_key(user_id, character_id), None)

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("清除 affinity_state 失败: %s", e)
