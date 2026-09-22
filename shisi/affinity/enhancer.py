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

#: `user_scheduler._persist_affinity` 审计镜像的刻度判据唯一真源。
#: 04229eb 镜像写 affection_points（0–500）→ 旧标记；df59752 起写侧改 shisi
#: 刻度并换用新标记。写读两侧必须引这里的常量——判据错位即二次换算
#: （250 points → 镜像 50 → 回放 10）。
MIRROR_REASON_POINTS = "user_scheduler_persist"
MIRROR_REASON_SHISI = "user_scheduler_persist_shisi"


def _scale_points_to_shisi(pts: float, shisi_min: float, shisi_max: float) -> float:
    """affection_points（0–500）→ shisi 亲密度（默认 0–100）。刻度真源 scale。"""
    try:
        from . import scale as affinity_scale

        return affinity_scale.points_to_shisi(pts, shisi_min, shisi_max)
    except Exception:  # noqa: BLE001
        # scale 导入失败时退化为线性（与 POINTS_MAX=500 同构）
        ratio = max(0.0, min(1.0, float(pts or 0.0) / 500.0))
        return shisi_min + ratio * (shisi_max - shisi_min)


def default_db_path() -> Path:
    """审计库默认路径（供 `user_scheduler` 等外部写入方对齐，避免各自拼路径）。

    返回**模块真源** `_DB_DEFAULT`（测试可 monkeypatch 重定向到临时库）。
    """
    return _DB_DEFAULT


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

        🔴 块E（2026-09-22）：审计**读取失败时不再 `return`**，而是继续走点存
        兜底。旧实现 `except: return` —— 审计表缺失（全新库 / 迁移未跑）时
        连兜底回填也一并跳过，两条恢复路径同时失效，且日志只说"审计回放失败"，
        完全看不出"点存其实有数据却没用"。恢复路径应当**彼此独立降级**。
        """
        rows: list = []
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                rows = conn.execute(
                    "SELECT ar.character_id, ar.new_value, ar.created_at, ar.reason "
                    "FROM affinity_records ar "
                    "JOIN (SELECT character_id AS cid, MAX(id) AS mid "
                    "      FROM affinity_records GROUP BY character_id) latest "
                    "  ON ar.id = latest.mid"
                ).fetchall()
        except Exception as e:  # noqa: BLE001
            logger.warning("好感度审计回放失败（转点存兜底）: %s", e)
        for key, value, created_at, row_reason in rows:
            k = str(key or "")
            if not k:
                continue
            # 刻度唯一真源：`AffinityEnhancer._values` 是 **shisi 0–100**。
            # 只有**旧标记** `user_scheduler_persist` 的镜像行是 affection_points
            # （04229eb 时代），回放须换算；新标记 `..._shisi` 与 enhancer 自有行
            # 本就同刻度，直取。判据常量见模块头 MIRROR_REASON_*。
            raw_val = float(value or 0)
            if str(row_reason or "") == MIRROR_REASON_POINTS:
                raw_val = _scale_points_to_shisi(raw_val, self._min, self._max)
            self._values[k] = max(self._min, min(self._max, raw_val))
            ts: datetime | None = None
            if created_at:
                try:
                    ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    ts = None
            self._last_interaction[k] = ts or datetime.now(tz=timezone.utc)
        # 🔴 块E（2026-09-22）：审计日志可能**没有**该键的历史（生产实证审计
        # 与点存两份键空间零交集），但点存里有 —— 此时用点存补齐，避免
        # "回放成功但值仍为 0"。仅补审计缺失的键，不覆盖已有值。
        self._restore_missing_from_points()
        if self._values:
            logger.info("好感度审计回放恢复 %d 个隔离键", len(self._values))

    def _restore_missing_from_points(self) -> None:
        """点存兜底回填：审计无记录但点存有值的键（键格式**完全一致**才匹配）。

        键口径已由 `orchestrator` 统一为 `user_key_from_session`（会话键原样），
        与 `user_scheduler._persist_affinity` 写入点存时的 `user_id` 同源。
        """
        try:
            from utils import affinity_state, json_state

            data = json_state.read_json(affinity_state._PATH, default={}) or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("好感度点存兜底读取失败（忽略）: %s", e)
            return
        restored = 0
        for key, val in data.items():
            k = str(key or "")
            if not k or k in self._values:
                continue
            try:
                pts = float((val or {}).get("affection_points", 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            # 点存权威值是 affection_points（0–500），`_values` 是 shisi（0–100）。
            # 旧实现直接 min/max 钳入 → 250 points 被钳成 100（应为 50），
            # mapper.current_points 再反转成 500 —— 好感度被系统性放大。
            self._values[k] = _scale_points_to_shisi(pts, self._min, self._max)
            self._last_interaction[k] = datetime.now(tz=timezone.utc)
            restored += 1
        if restored:
            logger.info("好感度点存兜底回填 %d 个审计缺失键", restored)

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
