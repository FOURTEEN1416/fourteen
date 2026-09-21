"""AffinityEnhancer — 好感度更新、衰减、解锁、审计。

2026-09-21 P1 隔离：内存键与审计键支持 `user_id::character_id`。
旧实现仅按 character_id —— 多用户与同一角色聊天时亲密度互相污染。
未传 user_id 时保持旧键（管理/测试兼容），运行时聊天路径必须传 session_id。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_config
from .decay_engine import DecayEngine
from .unlock_manager import UnlockEvent, UnlockManager

logger = logging.getLogger("shisi.affinity.enhancer")

_DB_DEFAULT = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"


def affinity_key(character_id: str, user_id: str = "") -> str:
    """隔离键：有 user_id 时为 `user::character`，否则退回 character。"""
    cid = str(character_id or "")
    uid = str(user_id or "").strip()
    return f"{uid}::{cid}" if uid else cid


class AffinityEnhancer:
    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else _DB_DEFAULT
        self._decay = DecayEngine()
        self._unlock = UnlockManager()
        self._min = get_config("affinity", "min_value", 0)
        self._max = get_config("affinity", "max_value", 100)
        self._audit_enabled = get_config("affinity", "audit_enabled", True)
        self._values: dict[str, float] = {}
        self._last_interaction: dict[str, datetime] = {}
        # 2026-09-22 重启归零根治：`_values` 原为纯内存 dict，重启后全部键归 0
        # —— mapper.sync 以「目标−已存」差分且钳 ±3，每轮只能爬回 3 分；解锁
        # 在爬坡途中重复触发；get_progress 恒显 ≈0。affinity_records 是本类
        # 自己逐次写入的审计日志（character_id 列即完整隔离键），现作为恢复源
        # 回放每键最新值（含 _last_interaction，衰减宽限期据此跨重启连续）。
        self._restore_from_audit()

    @property
    def unlock_manager(self) -> UnlockManager:
        return self._unlock

    def _restore_from_audit(self) -> None:
        """从 affinity_records 审计日志回放每键最新值（进程重启恢复）。

        - ``created_at`` 由 SQLite ``datetime('now')`` 写入（**naive UTC** 串），
          统一转为 aware UTC —— DecayEngine 用 aware now 相减，naive 会
          TypeError 使 decay_all 崩溃；
        - 回放失败只告警不抛（构造在装配热路径上，降级为旧行为从 0 起步）。
        """
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                rows = conn.execute(
                    "SELECT ar.character_id, ar.new_value, ar.created_at "
                    "FROM affinity_records ar "
                    "JOIN (SELECT character_id AS cid, MAX(id) AS mid "
                    "      FROM affinity_records GROUP BY character_id) latest "
                    "  ON ar.id = latest.mid"
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.warning("好感度审计回放失败（从 0 起步）: %s", e)
            return
        for key, value, created_at in rows:
            k = str(key or "")
            if not k:
                continue
            self._values[k] = max(self._min, min(self._max, float(value or 0)))
            ts: datetime | None = None
            if created_at:
                try:
                    ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    ts = None
            self._last_interaction[k] = ts or datetime.now(tz=timezone.utc)
        if self._values:
            logger.info("好感度审计回放恢复 %d 个隔离键", len(self._values))

    @staticmethod
    def _key(character_id: str, user_id: str = "") -> str:
        return affinity_key(character_id, user_id)

    @staticmethod
    def split_key(key: str) -> tuple[str, str]:
        """`user::character` → (user, character)；裸 character → ("", character)。"""
        if "::" in key:
            uid, cid = key.split("::", 1)
            return uid, cid
        return "", key

    def update(
        self,
        character_id: str,
        delta: float,
        reason: str = "",
        source: str = "chat",
        user_id: str = "",
    ) -> tuple[float, list[UnlockEvent]]:
        key = self._key(character_id, user_id)
        old = self._values.get(key, 0.0)
        new = max(self._min, min(self._max, old + delta))
        self._values[key] = new
        self._last_interaction[key] = datetime.now(tz=timezone.utc)

        # 解锁阈值配置挂在角色上；数值本身按 user×character 独立
        unlocks = self._unlock.check_unlocks(
            key if user_id else str(character_id), old, new
        )

        self._record_affinity(key, old, new, delta, reason, source)
        if self._audit_enabled:
            self._audit(key, "update", f"delta={delta}, reason={reason}, user={user_id or '-'}")

        return new, unlocks

    def apply_decay(self, character_id: str, now: datetime | None = None,
                    user_id: str = "") -> float:
        key = self._key(character_id, user_id)
        if key not in self._values:
            return 0.0
        last = self._last_interaction.get(key, datetime.now(tz=timezone.utc))
        decay = self._decay.calculate_decay(self._values[key], last, now)
        if decay > 0:
            old = self._values[key]
            new = max(self._min, old - decay)
            self._values[key] = new
            self._record_affinity(key, old, new, -decay, "decay", "system")
        return decay

    def decay_all(self, now: datetime | None = None) -> float:
        """对内存中全部好感度键（含 user×character）应用衰减。"""
        total = 0.0
        for key in list(self._values.keys()):
            uid, cid = self.split_key(key)
            total += self.apply_decay(cid, now=now, user_id=uid)
        return total

    def get_progress(self, character_id: str, user_id: str = "") -> dict[str, Any]:
        key = self._key(character_id, user_id)
        value = self._values.get(key, 0.0)
        return {
            "character_id": character_id,
            "user_id": user_id or "",
            "affinity_key": key,
            "affinity": value,
            "min": self._min,
            "max": self._max,
            "percentage": (value - self._min) / (self._max - self._min) * 100 if self._max > self._min else 0,
            "unlocks": [u.__dict__ for u in self._unlock.get_unlocks_at(value)],
        }

    def get_value(self, character_id: str, user_id: str = "") -> float:
        """读取好感度。有 user_id 时**只读该用户键**，不回退到全局角色键（防串台）。"""
        key = self._key(character_id, user_id)
        return self._values.get(key, 0.0)

    def _record_affinity(self, cid: str, old: float, new: float, delta: float, reason: str, source: str) -> None:
        try:
            # 必须用 closing()：`with sqlite3.connect(...)` 只管理**事务**（退出时
            # commit/rollback），**不会关闭连接** —— 每次调用都会泄漏一个连接，
            # 累积后耗尽文件句柄。
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                conn.execute(
                    "INSERT INTO affinity_records (character_id, old_value, new_value, delta, reason, source) VALUES (?,?,?,?,?,?)",
                    (cid, old, new, delta, reason, source),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("记录好感度变更失败: %s", e)

    def _audit(self, cid: str, action: str, detail: str) -> None:
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                conn.execute(
                    "INSERT INTO affinity_audit (character_id, action, detail) VALUES (?,?,?)",
                    (cid, action, detail),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("审计记录失败: %s", e)
