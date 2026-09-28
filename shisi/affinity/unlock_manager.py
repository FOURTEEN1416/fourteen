"""好感度解锁管理 — 阈值→解锁动作映射 + 首次解锁落库。

2026-09-28 D10 接线完善（W14）：此前 check_unlocks 只写日志 + 通知 listener
（subscribe 全仓零调用），解锁全是展示层事实。现给 UnlockManager 挂可选
db_path：有库时把首次解锁结果 INSERT 进 ``affinity_unlocks``（表结构见
shisi/migrations.py，本模块不建表）。``character_id`` 列存**完整隔离键**
（``user::character``，沿 affinity_records 先例）——UNIQUE(character_id,
threshold, unlock_type) 即 user×character×档位的"首次"语义；重复导入幂等。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import get_config

logger = logging.getLogger("shisi.affinity.unlock_manager")


@dataclass
class UnlockEvent:
    threshold: int
    unlock_type: str
    name: str


class UnlockManager:
    def __init__(self, db_path: Path | str | None = None):
        self._unlocks: list[UnlockEvent] = []
        self._listeners: list[Callable[[str, UnlockEvent], Any]] = []
        self._db_path = Path(db_path) if db_path else None
        self._load_from_config()

    def _load_from_config(self) -> None:
        raw = get_config("affinity", "unlocks", [])
        for u in raw:
            self._unlocks.append(UnlockEvent(
                threshold=u.get("threshold", 0),
                unlock_type=u.get("type", ""),
                name=u.get("name", ""),
            ))

    def check_unlocks(self, character_id: str, old_affinity: float, new_affinity: float) -> list[UnlockEvent]:
        newly_unlocked = []
        for u in self._unlocks:
            if old_affinity < u.threshold <= new_affinity:
                newly_unlocked.append(u)
                logger.info("好感度解锁: %s → %s (阈值%d)", character_id, u.name, u.threshold)
                for listener in self._listeners:
                    try:
                        listener(character_id, u)
                    except Exception as e:  # noqa: BLE001
                        logger.error("解锁事件监听器异常: %s", e)
        if newly_unlocked and self._db_path is not None:
            self._persist_unlocks(character_id, newly_unlocked)
        return newly_unlocked

    def _persist_unlocks(self, character_id: str, events: list[UnlockEvent]) -> None:
        """首次解锁落库；表缺失/写失败只告警（与 enhancer._record_affinity 同口径）。"""
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                for u in events:
                    conn.execute(
                        "INSERT OR IGNORE INTO affinity_unlocks "
                        "(character_id, threshold, unlock_type, unlock_name) VALUES (?,?,?,?)",
                        (character_id, int(u.threshold), u.unlock_type, u.name),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("解锁结果落库失败（不阻断解锁事件）: %s", e)

    def recorded_unlocks(self, character_id: str) -> list[dict[str, Any]]:
        """读取已落库的解锁行（HTTP 展示面）。库/表不可用返回空。"""
        if self._db_path is None:
            return []
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT character_id, threshold, unlock_type, unlock_name, unlocked_at "
                    "FROM affinity_unlocks WHERE character_id=? ORDER BY threshold",
                    (character_id,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.warning("解锁记录读取失败（返回空）: %s", e)
            return []

    def get_unlocks_at(self, affinity: float) -> list[UnlockEvent]:
        return [u for u in self._unlocks if affinity >= u.threshold]

    def subscribe(self, listener: Callable[[str, UnlockEvent], Any]) -> None:
        self._listeners.append(listener)


# ---------------------------------------------------------------------------
# 模块级档位查询与生产亲和读取（供表情包/语音/话题注入面共用）
# ---------------------------------------------------------------------------

_TIER_MANAGER: UnlockManager | None = None


def _tier_manager() -> UnlockManager:
    """无库 UnlockManager 单例——只读 config 档位，不落库。"""
    global _TIER_MANAGER
    if _TIER_MANAGER is None:
        _TIER_MANAGER = UnlockManager()
    return _TIER_MANAGER


def get_unlock_tiers() -> list[UnlockEvent]:
    """config affinity.unlocks 全部档位（唯一真源 shisi.yaml）。"""
    return _tier_manager()._unlocks


def unlocked_topic_names(affinity: float) -> list[str]:
    """当前好感已解锁的话题档位名（type == topic）。"""
    return [u.name for u in get_unlock_tiers() if u.unlock_type == "topic" and affinity >= u.threshold]


def voice_unlock_threshold() -> float | None:
    """专属语音档位阈值（type == voice 的最小阈值）；config 无该档则不设门禁。"""
    thresholds = [float(u.threshold) for u in get_unlock_tiers() if u.unlock_type == "voice"]
    return min(thresholds) if thresholds else None


def read_user_affinity(character_id: str, user_id: str) -> float | None:
    """生产侧亲和读取：shisi 刻度 user×character 唯一真源。

    经 ``api.deps.shisi_reg.affinity_enhancer``（懒导入防循环；enhancer 构造时
    已从审计回放恢复）。无 user / 装配缺失 / 异常一律返回 None —— 调用方以
    None 表示"无法评估"并跳过门禁（fail-open 仅限无用户上下文场景）。
    """
    if not user_id:
        return None
    try:
        from api.deps import deps

        enhancer = getattr(getattr(deps, "shisi_reg", None), "affinity_enhancer", None)
        if enhancer is None:
            return None
        return float(enhancer.get_value(character_id, user_id=user_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("亲和读取降级（门禁跳过）: %s", e)
        return None
