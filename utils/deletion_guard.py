"""已删除账号的迟到写入守卫（W9）。

问题：删除作业与在途生成存在竞争——生成中的回合在 purge 完成后才走到写侧
（chat 回写 / 事实抽取 / 外发），把已「遗忘」的数据重新写回存储。

方案：删除作业在**冻结阶段**先把 owner uid 记入坟场文件
（``data/lifecycle_jobs/graveyard.json``，跨 worker 共享），写侧与外发侧在
进入前查询本模块；查询按 mtime 缓存（默认 3s TTL），跨进程生效的窗口即
TTL 上限，同进程内 ``block_owner`` 立即生效。

分层：本模块属于 ``utils``，供 shisi 记忆层与 proactive 外发层导入，
不反向依赖 api。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from utils.project_paths import project_path
from utils.session_key import owner_of

logger = logging.getLogger("deletion_guard")

_TTL_SECONDS = 3.0
_blocked_uids: frozenset[int] = frozenset()
_cache_at: float = 0.0
_local_blocked: set[int] = set()


def graveyard_path() -> Path:
    return project_path("data", "lifecycle_jobs", "graveyard.json")


def read_graveyard() -> dict[str, Any]:
    try:
        return json.loads(graveyard_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_graveyard(mutate) -> None:
    """坟场写入（跨进程锁内读改写；mutate 原地修改 dict，返回 None）。"""
    from utils import json_state

    json_state.update_json(graveyard_path(), mutate)


def _refresh() -> None:
    global _blocked_uids, _cache_at
    raw = read_graveyard()
    uids: set[int] = set(_local_blocked)
    for k in raw:
        try:
            uids.add(int(k))
        except (TypeError, ValueError):
            continue
    _blocked_uids = frozenset(uids)
    _cache_at = time.monotonic()


def _uids() -> frozenset[int]:
    if time.monotonic() - _cache_at > _TTL_SECONDS:
        _refresh()
    return _blocked_uids


def block_owner(user_id: int) -> None:
    """同进程立即生效；跨进程由坟场文件 + TTL 兜底。"""
    _local_blocked.add(int(user_id))
    _refresh()


def is_owner_blocked(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return int(user_id) in _uids()


def is_session_blocked(session_key: str) -> bool:
    """会话键归属的账号是否已被删除（禁止再写入/外发）。

    无主会话（遗留裸键 / 匿名）没有账号可删，恒放行。
    """
    return is_owner_blocked(owner_of(session_key))
