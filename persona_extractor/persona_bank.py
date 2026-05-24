"""
用户人格画像银行 — 持久化存储与演化追踪

功能:
  - SQLite 持久化存储用户人格画像
  - 支持多用户 (user_id)
  - 时间序列追踪人格变化
  - 人格稳定性评估 (snapshot_count 足够时返回稳定版本)
  - 与现有 StructuredMemory 共享数据库 (复用 sqlite.db)

架构:
  UserPersonaBank
    ├── _db: SQLite 连接 (复用现有)
    ├── _cache: Dict[str, UserPersona] 内存缓存
    └── _history_limit: 每个用户保留的快照上限
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading

from .models import (
    OceanTraits,
    PadState,
    StyleVector,
    UserPersona,
    UserPersonaSnapshot,
)

logger = logging.getLogger("persona_bank")

# 建表SQL
CREATE_USER_PERSONA_TABLE = """
CREATE TABLE IF NOT EXISTS user_persona (
    user_id TEXT PRIMARY KEY,
    ocean_json TEXT NOT NULL,
    pad_json TEXT NOT NULL,
    style_json TEXT NOT NULL,
    snapshot_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_updated TEXT NOT NULL
)
"""

CREATE_USER_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS user_persona_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ocean_json TEXT NOT NULL,
    pad_json TEXT NOT NULL,
    style_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT '',
    trigger_message TEXT DEFAULT ''
)
"""


class UserPersonaBank:
    """用户人格画像银行

    Args:
        db_path: SQLite 数据库路径 (复用 memory 的 sqlite.db)
        history_limit: 每个用户保留的最大快照数
    """

    def __init__(
        self,
        db_path: str = "./data/sqlite.db",
        history_limit: int = 200,
    ):
        self.db_path = db_path
        self.history_limit = history_limit
        self._lock = threading.Lock()
        self._cache: dict[str, UserPersona] = {}
        self._conn: sqlite3.Connection | None = None

        self._init_db()
        self._load_all()
        logger.info("UserPersonaBank initialized (db=%s, cached=%d)",
                     db_path, len(self._cache))

    # ── 数据库初始化 ──

    def _init_db(self) -> None:
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute(CREATE_USER_PERSONA_TABLE)
            self._conn.execute(CREATE_USER_SNAPSHOT_TABLE)
            try:
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_snapshots_user_time "
                    "ON user_persona_snapshots (user_id, timestamp DESC)"
                )
            except Exception:
                pass  # 索引创建失败不影响使用
            self._conn.commit()
        except Exception as e:
            logger.error("PersonaBank DB init error: %s", e)

    def get_connection(self) -> sqlite3.Connection | None:
        """获取数据库连接（给外部复用）"""
        return self._conn

    # ── CRUD ──

    def get_persona(self, user_id: str = "default") -> UserPersona | None:
        """获取用户人格画像（优先缓存）"""
        if user_id in self._cache:
            return self._cache[user_id]
        return self._load_from_db(user_id)

    def save_persona(self, persona: UserPersona) -> bool:
        """保存或更新用户人格画像"""
        if not self._conn:
            return False

        with self._lock:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO user_persona
                       (user_id, ocean_json, pad_json, style_json,
                        snapshot_count, first_seen, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        persona.user_id,
                        json.dumps(persona.ocean.to_dict(), ensure_ascii=False),
                        json.dumps(persona.pad.to_dict(), ensure_ascii=False),
                        json.dumps(persona.style.to_dict(), ensure_ascii=False),
                        persona.snapshot_count,
                        persona.first_seen,
                        persona.last_updated,
                    ),
                )
                self._conn.commit()
                self._cache[persona.user_id] = persona
                return True
            except Exception as e:
                logger.error("Failed to save persona: %s", e)
                return False

    def add_snapshot(self, snapshot: UserPersonaSnapshot,
                     user_id: str = "default") -> bool:
        """添加检测快照（同时更新主画像）"""
        if not self._conn:
            return False

        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO user_persona_snapshots
                       (user_id, timestamp, ocean_json, pad_json, style_json,
                        confidence, source, trigger_message)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        snapshot.timestamp,
                        json.dumps(snapshot.ocean.to_dict(), ensure_ascii=False),
                        json.dumps(snapshot.pad.to_dict(), ensure_ascii=False),
                        json.dumps(snapshot.style.to_dict(), ensure_ascii=False),
                        snapshot.confidence,
                        snapshot.source,
                        snapshot.trigger_message[:200],
                    ),
                )

                # 清理超量历史
                self._conn.execute(
                    """DELETE FROM user_persona_snapshots
                       WHERE id IN (
                           SELECT id FROM user_persona_snapshots
                           WHERE user_id = ?
                           ORDER BY timestamp DESC
                           LIMIT -1 OFFSET ?
                       )""",
                    (user_id, self.history_limit),
                )
                self._conn.commit()
                return True
            except Exception as e:
                logger.error("Failed to add snapshot: %s", e)
                return False

    def update_persona_with_snapshot(
        self, snapshot: UserPersonaSnapshot, user_id: str = "default"
    ) -> UserPersona:
        """基于快照更新用户画像（核心流程）

        1. 获取或创建 UserPersona
        2. 应用快照 (Bayesian融合)
        3. 存储快照到历史表
        4. 保存更新后的画像
        """
        persona = self.get_persona(user_id)
        if persona is None:
            persona = UserPersona(user_id=user_id)

        persona.apply_snapshot(snapshot)
        self.add_snapshot(snapshot, user_id)
        self.save_persona(persona)

        return persona

    # ── 稳定性评估 ──

    def is_stable(self, user_id: str = "default",
                  min_samples: int = 5) -> bool:
        """用户人格画像是否稳定（样本数足够）"""
        persona = self.get_persona(user_id)
        if persona is None:
            return False
        return persona.snapshot_count >= min_samples

    def get_stability_score(self, user_id: str = "default") -> float:
        """计算稳定性分数 (0~1)"""
        persona = self.get_persona(user_id)
        if persona is None:
            return 0.0
        # 基于样本数的S曲线
        count = persona.snapshot_count
        return 1.0 / (1.0 + 10.0 * (2.718 ** (-0.5 * count)))

    # ── 内部方法 ──

    def _load_from_db(self, user_id: str) -> UserPersona | None:
        if not self._conn:
            return None
        try:
            row = self._conn.execute(
                "SELECT * FROM user_persona WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            persona = UserPersona(
                user_id=row[0],
                ocean=OceanTraits.from_dict(json.loads(row[1])),
                pad=PadState.from_dict(json.loads(row[2])),
                style=StyleVector.from_dict(json.loads(row[3])),
                snapshot_count=row[4],
                first_seen=row[5],
                last_updated=row[6],
            )
            self._cache[user_id] = persona
            return persona
        except Exception as e:
            logger.error("Failed to load persona from DB: %s", e)
            return None

    def _load_all(self) -> None:
        """启动时加载所有用户画像到缓存"""
        if not self._conn:
            return
        try:
            rows = self._conn.execute(
                "SELECT user_id FROM user_persona"
            ).fetchall()
            for (user_id,) in rows:
                self._load_from_db(user_id)
        except Exception as e:
            logger.debug("No existing personas to load: %s", e)

    def get_recent_snapshots(
        self, user_id: str = "default", limit: int = 10
    ) -> list[UserPersonaSnapshot]:
        """获取最近的检测快照（用于趋势分析）"""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute(
                """SELECT timestamp, ocean_json, pad_json, style_json,
                          confidence, source, trigger_message
                   FROM user_persona_snapshots
                   WHERE user_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (user_id, limit),
            ).fetchall()
            snapshots = []
            for row in rows:
                snapshots.append(UserPersonaSnapshot(
                    timestamp=row[0],
                    ocean=OceanTraits.from_dict(json.loads(row[1])),
                    pad=PadState.from_dict(json.loads(row[2])),
                    style=StyleVector.from_dict(json.loads(row[3])),
                    confidence=row[4],
                    source=row[5],
                    trigger_message=row[6] or "",
                ))
            return snapshots
        except Exception as e:
            logger.error("Failed to load snapshots: %s", e)
            return []

    def clear_user(self, user_id: str = "default") -> bool:
        """清除用户数据（用于测试）"""
        if not self._conn:
            return False
        try:
            self._conn.execute(
                "DELETE FROM user_persona WHERE user_id = ?", (user_id,)
            )
            self._conn.execute(
                "DELETE FROM user_persona_snapshots WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
            self._cache.pop(user_id, None)
            return True
        except Exception as e:
            logger.error("Failed to clear user: %s", e)
            return False

    def health_check(self) -> dict:
        return {
            "db_path": self.db_path,
            "db_connected": self._conn is not None,
            "cached_users": len(self._cache),
            "history_limit": self.history_limit,
        }
