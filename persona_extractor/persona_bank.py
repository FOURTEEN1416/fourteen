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

import contextlib
import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Sequence

from utils.session_key import owner_of

from .models import (
    OceanTraits,
    PadState,
    StyleVector,
    UserPersona,
    UserPersonaSnapshot,
)

logger = logging.getLogger("persona_bank")

# 五维度列名单一真源（migrations 的存量库补列迁移复用本常量）。
# 对应 UserPersona 属性：hexaco / dark_triad / mental_health / liwc / cognitive，
# 落库为 JSON 文本（D11：fusion 算出的五维度此前从未落库，重启全丢）。
PERSONA_DIMENSION_COLUMNS: tuple[str, ...] = (
    "hexaco_json",
    "dark_triad_json",
    "mental_health_json",
    "liwc_json",
    "cognitive_json",
)

_PERSONA_DIMENSION_FIELDS: dict[str, str] = {
    "hexaco_json": "hexaco",
    "dark_triad_json": "dark_triad",
    "mental_health_json": "mental_health",
    "liwc_json": "liwc",
    "cognitive_json": "cognitive",
}

_TRIGGER_HASH_PREFIX = "sha256:"

# 建表SQL（migrations.run_migrations 复用本常量保证同构；本表由本组件自治
# 建表，_init_db 另对存量旧结构做幂等补列）
CREATE_USER_PERSONA_TABLE = """
CREATE TABLE IF NOT EXISTS user_persona (
    user_id TEXT PRIMARY KEY,
    ocean_json TEXT NOT NULL,
    pad_json TEXT NOT NULL,
    style_json TEXT NOT NULL,
    snapshot_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    hexaco_json TEXT,
    dark_triad_json TEXT,
    mental_health_json TEXT,
    liwc_json TEXT,
    cognitive_json TEXT
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


def privatize_trigger_message(text: str | None) -> str:
    """把触发检测的原话隐私化为 ``sha256:<前16hex>|len:<长度>``。

    只保留指纹与存档窗口长度，不留任何原文；空串保持空串。哈希与长度
    基于实际存档窗口（前 200 字，与旧 ``[:200]`` 截断口径一致）。
    """
    raw = (text or "").strip()[:200]
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{_TRIGGER_HASH_PREFIX}{digest}|len:{len(raw)}"


def scope_owner_uid(scope: str) -> int | None:
    """从 persona scope 键解析归属 uid；无归属返回 None。

    scope 构造点（orchestrator.process_message）：``{character_id}:{session_id}``，
    其中 session_id 是会话键家族（``N:peer@im.wechat`` / ``N:web:hex``）。
    剥掉 character 段后用 utils.session_key.owner_of 取 owner——即「session_id
    的 owner 段」。无 character 前缀的裸键（历史 default 桶）与 owner 段非
    数字的键都判无归属，删除作业不得误伤。
    """
    _char, sep, session = str(scope or "").partition(":")
    if not sep:
        return None
    return owner_of(session)


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
            self._ensure_dimension_columns()
            with contextlib.suppress(Exception):  # noqa: BLE001
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_snapshots_user_time "
                    "ON user_persona_snapshots (user_id, timestamp DESC)"
                )
            self._conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("PersonaBank DB init error: %s", e)

    def _ensure_dimension_columns(self) -> None:
        """存量旧结构表幂等补列（新表由 CREATE 的全列 DDL 直接建齐）。

        与 shisi/migrations 的补列迁移同源：列名真源都是
        PERSONA_DIMENSION_COLUMNS，判定用 PRAGMA table_info，缺列才 ALTER。
        """
        if self._conn is None:
            return
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(user_persona)")
        }
        for col in PERSONA_DIMENSION_COLUMNS:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE user_persona ADD COLUMN {col} TEXT")

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
        """保存或更新用户人格画像（含五维度 JSON 列，D11 真落库）。"""
        if not self._conn:
            return False

        def _dim_json(value: dict | None) -> str | None:
            return json.dumps(value, ensure_ascii=False) if value else None

        with self._lock:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO user_persona
                       (user_id, ocean_json, pad_json, style_json,
                        snapshot_count, first_seen, last_updated,
                        hexaco_json, dark_triad_json, mental_health_json,
                        liwc_json, cognitive_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        persona.user_id,
                        json.dumps(persona.ocean.to_dict(), ensure_ascii=False),
                        json.dumps(persona.pad.to_dict(), ensure_ascii=False),
                        json.dumps(persona.style.to_dict(), ensure_ascii=False),
                        persona.snapshot_count,
                        persona.first_seen,
                        persona.last_updated,
                        _dim_json(persona.hexaco),
                        _dim_json(persona.dark_triad),
                        _dim_json(persona.mental_health),
                        _dim_json(persona.liwc),
                        _dim_json(persona.cognitive),
                    ),
                )
                self._conn.commit()
                self._cache[persona.user_id] = persona
                return True
            except Exception as e:  # noqa: BLE001
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
                        privatize_trigger_message(snapshot.trigger_message),
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
            except Exception as e:  # noqa: BLE001
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
        return 1.0 / (1.0 + 10.0 * (2.718 ** (-0.5 * count)))  # type: ignore[no-any-return]

    # ── 内部方法 ──

    def _load_from_db(self, user_id: str) -> UserPersona | None:
        if not self._conn:
            return None
        try:
            # 显式列名（不用 SELECT * 位置索引）：五维度列是后补的，存量库
            # 列序可能与新库不一致，位置索引会把错列读进字段。
            cols = [
                "user_id", "ocean_json", "pad_json", "style_json",
                "snapshot_count", "first_seen", "last_updated",
                *PERSONA_DIMENSION_COLUMNS,
            ]
            row = self._conn.execute(
                f"SELECT {', '.join(cols)} FROM user_persona WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            by_name = dict(zip(cols, row, strict=True))

            def _dim(col: str) -> dict | None:
                raw = by_name.get(col)
                if not raw:
                    return None
                try:
                    loaded = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning("persona %s 列 %s 不是合法 JSON，按缺失处理",
                                   user_id, col)
                    return None
                return loaded if isinstance(loaded, dict) else None

            persona = UserPersona(
                user_id=by_name["user_id"],
                ocean=OceanTraits.from_dict(json.loads(by_name["ocean_json"])),
                pad=PadState.from_dict(json.loads(by_name["pad_json"])),
                style=StyleVector.from_dict(json.loads(by_name["style_json"])),
                snapshot_count=by_name["snapshot_count"],
                first_seen=by_name["first_seen"],
                last_updated=by_name["last_updated"],
                **{
                    _PERSONA_DIMENSION_FIELDS[col]: _dim(col)
                    for col in PERSONA_DIMENSION_COLUMNS
                },
            )
            self._cache[user_id] = persona
            return persona
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
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
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to load snapshots: %s", e)
            return []

    def list_persona_scopes(self) -> list[str]:
        """已存画像的 scope 键，按 `last_updated` 新→旧。

        scope 由调用方决定（生成侧是 `{character_id}:{session_id}`），bank 只如实
        列出，不做语义解释。
        """
        if not self._conn:
            return []
        try:
            rows = self._conn.execute(
                "SELECT user_id FROM user_persona ORDER BY last_updated DESC"
            ).fetchall()
            return [row[0] for row in rows]
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to list persona scopes: %s", e)
            return []

    def clear_users(self, user_ids: Sequence[str]) -> int:
        """批量清除画像与快照，返回**成功清除**的 scope 数。

        逐键提交：某一键失败不影响其余，失败数由调用方与目标数比对后暴露。
        """
        if not self._conn:
            return 0
        cleared = 0
        for user_id in user_ids:
            try:
                with self._lock:
                    self._conn.execute(
                        "DELETE FROM user_persona WHERE user_id = ?", (user_id,)
                    )
                    self._conn.execute(
                        "DELETE FROM user_persona_snapshots WHERE user_id = ?",
                        (user_id,),
                    )
                    self._conn.commit()
                    self._cache.pop(user_id, None)
                cleared += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to clear scope %s: %s", user_id, e)
        return cleared

    # ── 账号生命周期（W13 · D11 删号级联）──

    def _owner_scope_rows(self, user_id: int) -> dict[str, list[str]]:
        """两表中归属 uid 的 scope 键（逐表 distinct 后按 owner 判定筛选）。"""
        out: dict[str, list[str]] = {}
        for table in ("user_persona", "user_persona_snapshots"):
            rows = self._conn.execute(
                f"SELECT DISTINCT user_id FROM {table}"  # noqa: S608
            ).fetchall()
            out[table] = [
                r[0] for r in rows if scope_owner_uid(r[0]) == user_id
            ]
        return out

    def purge_owner_scopes(self, user_id: int) -> dict[str, int]:
        """删除归属 uid 的全部画像与快照，回执按表带删除行数。

        归属判定 = scope 剥 character 前缀后 ``owner_of(session_id) == uid``
        （见 :func:`scope_owner_uid`）；他人 scope 与无主 scope（裸 default、
        owner 段非数字）一律保留。幂等：重跑对已清空库返回全 0。
        """
        if not self._conn:
            return {"user_persona": 0, "user_persona_snapshots": 0}
        uid = int(user_id)
        deleted = {"user_persona": 0, "user_persona_snapshots": 0}
        with self._lock:
            try:
                for table, victims in self._owner_scope_rows(uid).items():
                    for scope in victims:
                        cur = self._conn.execute(
                            f"DELETE FROM {table} WHERE user_id = ?",  # noqa: S608
                            (scope,),
                        )
                        deleted[table] += max(cur.rowcount, 0)
                        self._cache.pop(scope, None)
                self._conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to purge persona scopes for uid %s: %s",
                             uid, e)
                raise
        return deleted

    def count_owner_scopes(self, user_id: int) -> dict[str, int]:
        """归属 uid 的残留行数（生命周期 verify 真源；应清零后为全 0）。"""
        if not self._conn:
            return {"user_persona": 0, "user_persona_snapshots": 0}
        uid = int(user_id)
        counts = {"user_persona": 0, "user_persona_snapshots": 0}
        for table, victims in self._owner_scope_rows(uid).items():
            if not victims:
                continue
            placeholders = ",".join("?" for _ in victims)
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id IN ({placeholders})",  # noqa: S608
                victims,
            ).fetchone()
            counts[table] = int(row[0])
        return counts

    def health_check(self) -> dict:
        return {
            "db_path": self.db_path,
            "db_connected": self._conn is not None,
            "cached_users": len(self._cache),
            "history_limit": self.history_limit,
        }
