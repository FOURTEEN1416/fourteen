"""
结构化记忆系统 — 基于 SQLite

管理结构化数据：
- user_facts: 用户事实（偏好、习惯、事件）
- affinity_log: 好感度变化记录
- chat_history: 对话历史（结构化版本）
- reminders: 提醒事项
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from utils.local_time import local_day_utc_bounds, now_local

logger = logging.getLogger("structured_memory")

# 全局注册表，用于跟踪所有 StructuredMemory 实例，确保程序退出时关闭连接
_structured_memory_instances: list[StructuredMemory] = []
_instances_lock = threading.Lock()


def _register_structured_memory(instance: StructuredMemory) -> None:
    """注册 StructuredMemory 实例到全局注册表"""
    with _instances_lock:
        if instance not in _structured_memory_instances:
            _structured_memory_instances.append(instance)


def _unregister_structured_memory(instance: StructuredMemory) -> None:
    """从全局注册表移除 StructuredMemory 实例"""
    with _instances_lock:
        if instance in _structured_memory_instances:
            _structured_memory_instances.remove(instance)


def _close_all_structured_memory() -> None:
    """关闭所有注册的 StructuredMemory 实例（atexit 处理器）"""
    with _instances_lock:
        instances = _structured_memory_instances.copy()
    for instance in instances:
        try:
            instance.close()
            logger.debug("StructuredMemory 连接已关闭: %s", instance.db_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 StructuredMemory 连接时出错: %s", e)


# 注册 atexit 处理器，确保程序退出时关闭所有数据库连接
atexit.register(_close_all_structured_memory)


class StructuredMemory:
    """
    SQLite 结构化记忆

    提供各表的 CRUD 操作，线程安全（连接级锁）。
    """

    def __init__(self, db_path: str = "./data/sqlite.db"):
        self.db_path = os.path.abspath(db_path)

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._write_lock = threading.Lock()
        self._degraded = False
        self._closed = False

        # 注册实例到全局注册表，确保程序退出时关闭连接
        _register_structured_memory(self)

        self._init_db()
        logger.info("StructuredMemory ready: %s", self.db_path)

    def close(self):
        """关闭数据库连接

        线程安全的数据库连接关闭方法，确保连接被正确释放。
        可通过 atexit 处理器自动调用，也可手动调用。
        """
        if self._closed:
            return

        with self._write_lock:
            if self._connection:
                try:
                    self._connection.close()
                    logger.debug("SQLite 连接已关闭: %s", self.db_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("关闭 SQLite 连接时出错: %s", e)
                finally:
                    self._connection = None
                    self._closed = True

        # 从全局注册表移除
        _unregister_structured_memory(self)

    def _execute_write(self, fn, *args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self._write_lock:
                    return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
        return None

    def __del__(self):
        """析构函数 — 确保连接被关闭

        注意：__del__ 不保证一定被调用，因此主要依赖 atexit 处理器。
        这里作为双重保险，在对象被垃圾回收时尝试关闭连接。
        """
        self.close()

    def _init_db(self) -> None:
        """初始化数据库和表结构"""
        with self._conn() as conn:
            assert conn is not None
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS affinity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level INTEGER NOT NULL,
                    level_name TEXT NOT NULL DEFAULT '',
                    affection_points REAL NOT NULL DEFAULT 0,
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    emotion_tag TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    trigger_time TIMESTAMP,
                    active BOOLEAN DEFAULT 1,
                    triggered BOOLEAN DEFAULT 0,
                    session_key TEXT DEFAULT '',
                    user_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    delivered_at TIMESTAMP,
                    fail_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS pending_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    user_id INTEGER,
                    intent TEXT NOT NULL DEFAULT 'set_reminder',
                    slots_json TEXT NOT NULL DEFAULT '{}',
                    ask_count INTEGER NOT NULL DEFAULT 0,
                    last_question TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_pending_intents_session
                    ON pending_intents(session_key, status);
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(trigger_time, active, triggered);

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL DEFAULT 'wechat',
                    user_id TEXT NOT NULL DEFAULT 'default',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS working_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    emotion_tag TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE TABLE IF NOT EXISTS pending_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_desc TEXT NOT NULL,
                    expected_time TIMESTAMP,
                    source_session_id TEXT,
                    is_resolved BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS persona_evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dimension TEXT NOT NULL,
                    before_val REAL NOT NULL,
                    after_val REAL NOT NULL,
                    delta REAL NOT NULL,
                    trigger_reason TEXT DEFAULT '',
                    llm_reasoning TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tool_call_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    arguments TEXT DEFAULT '{}',
                    result TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    success BOOLEAN DEFAULT 1,
                    trace_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS emotion_trajectory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    primary_emotion TEXT NOT NULL,
                    primary_intensity REAL NOT NULL,
                    secondary_emotions TEXT DEFAULT '[]',
                    energy REAL DEFAULT 1.0,
                    affinity_level INTEGER DEFAULT 0,
                    trigger_msg TEXT DEFAULT '',
                    trace_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    source_fact_ids TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trace_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    node TEXT NOT NULL,
                    duration_ms REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_facts_category ON user_facts(category);
                CREATE INDEX IF NOT EXISTS idx_chat_timestamp ON chat_history(created_at);
                CREATE INDEX IF NOT EXISTS idx_affinity_time ON affinity_log(created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active);
                CREATE INDEX IF NOT EXISTS idx_working_session ON working_memory(session_id);
                CREATE INDEX IF NOT EXISTS idx_pending_time ON pending_events(expected_time);
                CREATE INDEX IF NOT EXISTS idx_trace_id ON trace_log(trace_id);
                CREATE INDEX IF NOT EXISTS idx_emotion_traj_time ON emotion_trajectory(created_at);

                CREATE INDEX IF NOT EXISTS idx_facts_category_confidence_updated
                    ON user_facts(category, confidence, updated_at);
                CREATE INDEX IF NOT EXISTS idx_chat_session_created
                    ON chat_history(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_emotion_traj_affinity_time
                    ON emotion_trajectory(affinity_level, created_at);

                CREATE INDEX IF NOT EXISTS idx_reflections_created
                    ON reflections(created_at DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS user_facts_fts USING fts5(
                    fact,
                    content='user_facts',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS facts_fts_insert
                AFTER INSERT ON user_facts BEGIN
                    INSERT INTO user_facts_fts(rowid, fact)
                    VALUES (new.id, new.fact);
                END;

                CREATE TRIGGER IF NOT EXISTS facts_fts_delete
                AFTER DELETE ON user_facts BEGIN
                    INSERT INTO user_facts_fts(user_facts_fts, rowid, fact)
                    VALUES ('delete', old.id, old.fact);
                END;
            """)
            self._migrate_reminders_columns(conn)
            self._migrate_user_facts_columns(conn)
            conn.commit()

    def _migrate_user_facts_columns(self, conn) -> None:
        """user_facts 幂等迁移：多用户隔离 + 回忆强化 + 遗忘状态。

        - user_key：事实归属（从 session_id 派生，见 memory_pipeline）；
          存量行默认 ''（legacy），完整隔离策略下**不注入任何会话**。
        - access_count：检索/注入时自增（B4 回忆强化）。
        - status：active|forgotten；遗忘进回收站后本表删除行，status 供软路径。
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(user_facts)").fetchall()
        }
        migrations = {
            "user_key": "ALTER TABLE user_facts ADD COLUMN user_key TEXT NOT NULL DEFAULT ''",
            "access_count": "ALTER TABLE user_facts ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
            "status": "ALTER TABLE user_facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            # 包 Q · B-b：话题标签 + 近重复强化时间
            "topics": "ALTER TABLE user_facts ADD COLUMN topics TEXT NOT NULL DEFAULT ''",
            "last_seen_at": "ALTER TABLE user_facts ADD COLUMN last_seen_at TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_facts_user_key "
            "ON user_facts(user_key, status, confidence)"
        )
        # 回收站表（shisi 侧已存在同名结构；StructuredMemory 独立库可能没有）
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_recycle_bin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                memory_content TEXT NOT NULL,
                deleted_at TEXT NOT NULL DEFAULT (datetime('now')),
                restore_before TEXT NOT NULL,
                restored INTEGER NOT NULL DEFAULT 0
            )"""
        )

    @staticmethod
    def user_key_from_session(session_id: str) -> str:
        """session_id → 事实归属 user_key。

        形态：`N:wxid` / `1:wxid` / `42:wxid` → `wxid`；裸 `wxid` 原样；空 → `''`。
        """
        if not session_id:
            return ""
        s = str(session_id).strip()
        if ":" in s:
            return s.split(":", 1)[1] or s
        return s

    def _migrate_reminders_columns(self, conn) -> None:
        """老库幂等迁移：reminders 补列（会话归属/投递状态）。

        存量行 session_key 保持空串——轮询只投递 session_key 非空的提醒，
        历史无主提醒自然静默（等价于旧行为：存了但永远不触发）。
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()
        }
        migrations = {
            "session_key": "ALTER TABLE reminders ADD COLUMN session_key TEXT DEFAULT ''",
            "user_id": "ALTER TABLE reminders ADD COLUMN user_id INTEGER",
            "status": "ALTER TABLE reminders ADD COLUMN status TEXT DEFAULT 'pending'",
            "delivered_at": "ALTER TABLE reminders ADD COLUMN delivered_at TIMESTAMP",
            "fail_count": "ALTER TABLE reminders ADD COLUMN fail_count INTEGER DEFAULT 0",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_due "
            "ON reminders(trigger_time, active, triggered)"
        )

    @contextmanager
    def _conn(self, write: bool = False):
        """获取数据库连接

        Args:
            write: 是否为写操作，写操作会获取写锁
        """
        if write:
            with self._write_lock:
                yield self._connection
        else:
            yield self._connection

    @contextmanager
    def get_connection(self, write: bool = False):
        """公开的连接获取接口（用于CrossSessionReasoner等外部组件）

        Args:
            write: 是否为写操作，写操作会获取写锁
        """
        if write:
            with self._write_lock:
                yield self._connection
        else:
            yield self._connection

    # ── 用户事实 ──────────────────────────────────────────

    @staticmethod
    def _fact_bigrams(text: str) -> set[str]:
        t = "".join(str(text or "").lower().split())
        # 常见同义归一：阿拉伯数字与中文数字、标点
        trans = str.maketrans({"０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
                              "５": "5", "６": "6", "７": "7", "８": "8", "９": "9"})
        t = t.translate(trans)
        for a, b in (("十二", "12"), ("十一", "11"), ("十", "10"), ("一点", "1点")):
            t = t.replace(a, b)
        if len(t) < 2:
            return {t} if t else set()
        return {t[i : i + 2] for i in range(len(t) - 1)}

    @classmethod
    def facts_near_duplicate(cls, a: str, b: str, threshold: float = 0.72) -> bool:
        """bigram 相似度 + 子串包含（对齐 my-raze spirit）；视为近重复则 True。"""
        def _norm(s: str) -> str:
            t = "".join(str(s or "").lower().split())
            for x, y in (("十二", "12"), ("十一", "11"), ("十", "10")):
                t = t.replace(x, y)
            return t
        ta, tb = _norm(a), _norm(b)
        if not ta or not tb:
            return False
        if ta == tb:
            return True
        if ta in tb or tb in ta:
            return True
        ba, bb = cls._fact_bigrams(a), cls._fact_bigrams(b)
        if not ba or not bb:
            return False
        inter = len(ba & bb)
        union = len(ba | bb)
        if union == 0:
            return False
        return (inter / union) >= threshold or inter / max(len(ba), len(bb)) >= threshold

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "",
                 user_key: str = "", topics: str | list[str] | None = None) -> int:
        """添加用户事实（按 user_key 隔离）。

        包 Q · B-b：同 user_key 下 near-dup → UPDATE（confidence/access/last_seen/topics），
        **不双插**。
        """
        topics_text = (
            ",".join(str(t).strip() for t in topics if str(t).strip())
            if isinstance(topics, (list, tuple))
            else str(topics or "")
        )
        fact = str(fact or "").strip()
        if not fact:
            return -1

        with self._conn(write=True) as conn:
            # near-dup 扫描（同 user_key + active）
            rows = conn.execute(
                "SELECT id, fact, confidence, access_count, category, topics FROM user_facts "
                "WHERE user_key = ? AND status = 'active'",
                (user_key or "",),
            ).fetchall()
            for row in rows:
                existing = dict(row)
                if self.facts_near_duplicate(fact, existing.get("fact") or ""):
                    new_conf = max(float(existing.get("confidence") or 0.0), float(confidence))
                    merged_topics = existing.get("topics") or ""
                    if topics_text:
                        parts = [p for p in (merged_topics.split(",") + topics_text.split(",")) if p]
                        merged_topics = ",".join(dict.fromkeys(parts))
                    # 语义升级：relationship/commitment 覆盖 general/preference
                    new_cat = existing.get("category") or category
                    if category in ("relationship", "commitment") and new_cat not in (
                        "relationship",
                        "commitment",
                    ):
                        new_cat = category
                    conn.execute(
                        "UPDATE user_facts SET confidence = ?, access_count = access_count + 1, "
                        "category = ?, topics = ?, last_seen_at = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_conf, new_cat, merged_topics, existing["id"]),
                    )
                    conn.commit()
                    return int(existing["id"])

            cursor = conn.execute(
                "INSERT INTO user_facts "
                "(fact, category, confidence, source, user_key, access_count, status, topics, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'active', ?, CURRENT_TIMESTAMP)",
                (fact, category, confidence, source, user_key or "", topics_text),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_facts(self, category: str | None = None,
                  min_confidence: float = 0.0,
                  limit: int = 50,
                  user_key: str | None = None,
                  include_legacy: bool = False) -> list[dict[str, Any]]:
        """获取用户事实。

        Args:
            user_key: 指定归属；`None` 表示**不按用户过滤**（仅管理/内部维护路径）。
            include_legacy: 是否附带 user_key='' 的历史孤儿事实（完整隔离默认 False）。
        """
        with self._conn() as conn:
            clauses = ["status = 'active'", "confidence >= ?"]
            params: list[Any] = [min_confidence]
            if user_key is not None:
                if include_legacy:
                    clauses.append("(user_key = ? OR user_key = '')")
                    params.append(user_key or "")
                else:
                    clauses.append("user_key = ?")
                    params.append(user_key or "")
            if category:
                clauses.append("category = ?")
                params.append(category)
            sql = (
                "SELECT * FROM user_facts WHERE " + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def search_facts(self, keyword: str, user_key: str | None = None,
                     include_legacy: bool = False) -> list[dict[str, Any]]:
        """关键词搜索事实 — 优先FTS5，降级LIKE；可按 user_key 隔离。"""
        with self._conn() as conn:
            def _filter(rows: list) -> list[dict[str, Any]]:
                out = [dict(r) for r in rows]
                if user_key is None:
                    return [r for r in out if r.get("status", "active") == "active"]
                allowed = {user_key or ""}
                if include_legacy:
                    allowed.add("")
                return [
                    r for r in out
                    if r.get("status", "active") == "active" and r.get("user_key", "") in allowed
                ]

            try:
                rows = conn.execute(
                    """SELECT f.* FROM user_facts f
                       JOIN user_facts_fts fts ON f.id = fts.rowid
                       WHERE user_facts_fts MATCH ?
                       ORDER BY rank
                       LIMIT 20""",
                    (keyword,),
                ).fetchall()
                filtered = _filter(rows)
                if filtered:
                    return filtered
            except Exception as e:  # noqa: BLE001
                logger.debug("FTS5 search failed, falling back to LIKE: %s", e)
            rows = conn.execute(
                "SELECT * FROM user_facts WHERE fact LIKE ? ORDER BY confidence DESC LIMIT 20",
                (f"%{keyword}%",),
            ).fetchall()
            return _filter(rows)

    def update_fact_confidence(self, fact_id: int, confidence: float) -> None:
        """更新事实置信度"""
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE user_facts SET confidence = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (confidence, fact_id),
            )
            conn.commit()

    def increment_fact_access(self, fact_ids: list[int]) -> int:
        """检索/注入时自增 access_count（B4 回忆强化）。返回实际更新行数。"""
        ids = [int(i) for i in fact_ids or [] if i is not None]
        if not ids:
            return 0
        with self._conn(write=True) as conn:
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE user_facts SET access_count = access_count + 1 WHERE id IN ({placeholders})",  # noqa: S608
                ids,
            )
            conn.commit()
            return cur.rowcount or 0

    def delete_fact(self, fact_id: int, recycle: bool = True,
                    user_key: str = "", retain_days: int = 30) -> bool:
        """删除事实。

        B5 裁决「进回收站表」：默认先写入 `memory_recycle_bin` 再删主表行，
        数据可恢复、只增不减。`recycle=False` 时物理删除（维护路径）。
        """
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM user_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                return False
            data = dict(row)
            if recycle:
                import json
                from datetime import datetime as _dt
                from datetime import timedelta as _td
                content = json.dumps(data, ensure_ascii=False, default=str)
                uk = user_key or data.get("user_key", "") or "legacy"
                deleted_at = _dt.now()
                restore_before = (deleted_at + _td(days=retain_days)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO memory_recycle_bin "
                    "(character_id, memory_id, memory_content, deleted_at, restore_before, restored) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        f"user_fact:{uk}",
                        str(fact_id),
                        content,
                        deleted_at.strftime("%Y-%m-%d %H:%M:%S"),
                        restore_before,
                    ),
                )
            conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
            conn.commit()
            return True

    def restore_fact_from_recycle(self, recycle_id: int) -> int | None:
        """从回收站恢复事实到 user_facts；返回新 fact_id（失败 None）。"""
        import json
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM memory_recycle_bin WHERE id = ? AND restored = 0",
                (recycle_id,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(dict(row)["memory_content"])
            cur = conn.execute(
                "INSERT INTO user_facts (fact, category, confidence, source, user_key, access_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (
                    data.get("fact", ""),
                    data.get("category", "general"),
                    data.get("confidence", 0.5),
                    data.get("source", ""),
                    data.get("user_key", ""),
                    int(data.get("access_count", 0) or 0),
                ),
            )
            conn.execute(
                "UPDATE memory_recycle_bin SET restored = 1 WHERE id = ?", (recycle_id,)
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[no-any-return]

    def add_facts_batch(self, facts: list[dict[str, Any]], user_key: str = "") -> list[int]:
        """批量添加事实"""
        if not facts:
            return []
        ids: list[int] = []
        with self._conn(write=True) as conn:
            for f in facts:
                cur = conn.execute(
                    "INSERT INTO user_facts (fact, category, confidence, source, user_key, access_count, status) "
                    "VALUES (?, ?, ?, ?, ?, 0, 'active')",
                    (
                        f.get("fact", ""),
                        f.get("category", "general"),
                        f.get("confidence", 0.5),
                        f.get("source", ""),
                        f.get("user_key", user_key or ""),
                    ),
                )
                ids.append(int(cur.lastrowid or 0))
            conn.commit()
            return ids

    # ── 记忆反思 ──────────────────────────────────────────

    def add_reflection(self, content: str, session_id: str = "",
                       source_fact_ids: list[int] | None = None) -> int:
        """添加反思洞察"""
        import json
        source_ids = json.dumps(source_fact_ids or [])
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "INSERT INTO reflections (content, session_id, source_fact_ids) VALUES (?, ?, ?)",
                (content, session_id, source_ids),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_reflections(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取最近反思洞察"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_reflections(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """关键词搜索反思洞察"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{keyword}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 好感度日志 ────────────────────────────────────────

    def add_affinity_log(self, level: int, level_name: str,
                         affection_points: float, reason: str = "") -> int:
        """记录好感度变化"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(                "INSERT INTO affinity_log (level, level_name, affection_points, reason) VALUES (?, ?, ?, ?)",
                (level, level_name, affection_points, reason),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_affinity_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取好感度历史"""
        with self._conn() as conn:
            rows = conn.execute(                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_affinity(self) -> dict[str, Any] | None:
        """获取最新好感度记录"""
        with self._conn() as conn:
            row = conn.execute(                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # ── 聊天历史 ──────────────────────────────────────────

    def add_chat(self, role: str, content: str,
                 emotion_tag: str = "", session_id: str = "") -> int:
        """添加聊天记录"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(                "INSERT INTO chat_history (role, content, emotion_tag, session_id) VALUES (?, ?, ?, ?)",
                (role, content, emotion_tag, session_id),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_recent_chats(self, n: int = 20) -> list[dict[str, Any]]:
        """获取最近 N 条聊天"""
        with self._conn() as conn:
            rows = conn.execute(                "SELECT * FROM chat_history ORDER BY created_at DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]  # 反转成时间正序

    def get_chats_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """获取某次会话的聊天"""
        with self._conn() as conn:
            rows = conn.execute(                "SELECT * FROM chat_history WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chats_by_session_limit(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        """获取某会话最近 limit 条聊天（时间正序）。

        2026-09-20 新增：对话上下文真源改读 chat_history 表后，按会话拉最近
        N 条的受限量查询（旧 get_chats_by_session 全量拉取，长会话会拖慢热路径）。
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows][::-1]  # 反转成时间正序

    def get_cross_session_tail(
        self,
        session_id: str,
        limit: int = 8,
        user_key: str | None = None,
    ) -> list[str]:
        """跨会话尾巴：按 user_key 取最近持久化消息（含会话双形态）。

        用于 B-d：实时窗口尚浅时注入「上次会话尾巴」，避免新会话冷启动失忆。
        返回 `- 用户：...` / `- 助手：...` 文本行（时间正序）。
        """
        if limit <= 0:
            return []
        uk = user_key if user_key is not None else self.user_key_from_session(session_id)
        if not uk:
            return []
        forms = {uk, f"N:{uk}"}
        if session_id:
            forms.add(str(session_id).strip())
            bare = self.user_key_from_session(session_id)
            if bare:
                forms.add(bare)
                forms.add(f"N:{bare}")
        placeholders = ",".join("?" for _ in forms)
        sql = (
            "SELECT role, content FROM chat_history "
            f"WHERE session_id IN ({placeholders}) "
            "ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (*forms, limit)).fetchall()
        lines: list[str] = []
        for r in reversed([dict(x) for x in rows]):
            role = "用户" if r.get("role") == "user" else "助手"
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            if "处理超时" in content or content.startswith("（处理消息"):
                continue
            if len(content) > 200:
                content = content[:200] + "…"
            lines.append(f"- {role}：{content}")
        return lines[-limit:]

    def get_chats_today(self) -> list[dict[str, Any]]:
        """获取「今天」（**本地日**）的聊天。

        2026-09-20 修复：原实现用 ``date(created_at) = date('now')`` ——
        ``created_at`` 由 ``DEFAULT CURRENT_TIMESTAMP`` 写入（UTC），
        ``date('now')`` 同样是 UTC 日，两者同源但**都不是本地日**：
        在 UTC+8 上「今天」实际从**本地 08:00** 才换日（凌晨对话被算进昨天），
        且与按本地日期写入的日记/摘要键**口径脱钩**。
        现改为应用层按本地日划 UTC 区间（与 ``utils.local_time`` 同源）。
        """
        start, end = local_day_utc_bounds()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE created_at >= ? AND created_at < ? "
                "ORDER BY created_at ASC",
                (start, end),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_chats_today(self) -> int:
        """今天（**本地日**）聊了多少条 —— 口径同 :meth:`get_chats_today`。"""
        start, end = local_day_utc_bounds()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_history "
                "WHERE created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
            return row["cnt"] if row else 0

    # ── 提醒 ──────────────────────────────────────────────

    @staticmethod
    def _now_local() -> str:
        """本地时间字符串（与 ``utils.local_time.now_local`` **同源**）。

        提醒的时间比较统一走应用层：SQLite ``datetime('now')`` 是 UTC，
        与 LLM 写入的北京时间字符串差 8 小时（历史缺陷）。

        ⚠️ 2026-09-20：原先直接 ``datetime.now()``（**依赖主机时区**，在非
        UTC+8 主机上会静默错 8 小时且无回退）—— 现统一委托公共真源，
        自动获得 UTC+8 回退，与 ASE/记忆管线共用同一时钟。
        """
        return now_local().strftime("%Y-%m-%d %H:%M:%S")

    def add_reminder(
        self,
        content: str,
        trigger_time: str | None = None,
        session_key: str = "",
        user_id: int | None = None,
    ) -> int:
        """添加提醒（session_key 为空 = 无投递目标，轮询不会投递它）"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (content, trigger_time, session_key, user_id) "
                "VALUES (?, ?, ?, ?)",
                (content, trigger_time, session_key, user_id),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_pending_reminders(self, session_key: str = "") -> list[dict[str, Any]]:
        """查询未触发的提醒（query 工具用；session_key 空 = 不过滤）"""
        with self._conn() as conn:
            sql = (
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND trigger_time IS NOT NULL"
            )
            params: list[Any] = []
            if session_key:
                sql += " AND session_key = ?"
                params.append(session_key)
            sql += " ORDER BY trigger_time ASC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_due_reminders(self, now_local: str | None = None) -> list[dict[str, Any]]:
        """轮询专用：已到期且具备投递目标的提醒（北京时间应用层比较）"""
        now_local = now_local or self._now_local()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND status = 'pending' AND session_key != '' "
                "AND trigger_time IS NOT NULL AND trigger_time <= ? "
                "ORDER BY trigger_time ASC",
                (now_local,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_result(self, reminder_id: int, delivered: bool) -> None:
        """记录投递结果：成功=已触发；失败累计，3 次后判死（可查不可发）"""
        now = self._now_local()
        with self._conn(write=True) as conn:
            if delivered:
                conn.execute(
                    "UPDATE reminders SET triggered = 1, status = 'delivered', "
                    "delivered_at = ? WHERE id = ?",
                    (now, reminder_id),
                )
            else:
                conn.execute(
                    "UPDATE reminders SET fail_count = fail_count + 1, "
                    "status = CASE WHEN fail_count + 1 >= 3 "
                    "THEN 'failed' ELSE status END, "
                    "active = CASE WHEN fail_count + 1 >= 3 "
                    "THEN 0 ELSE active END WHERE id = ?",
                    (reminder_id,),
                )
            conn.commit()

    def mark_reminder_triggered(self, reminder_id: int) -> None:
        """标记提醒已触发（兼容旧签名）"""
        self.mark_reminder_result(reminder_id, delivered=True)

    # ── 澄清任务状态机（pending_intents）──────────────────

    _PENDING_INTENT_TTL_MIN = 15

    def upsert_pending_intent(
        self,
        session_key: str,
        intent: str,
        slots: dict[str, Any],
        ask_count: int,
        last_question: str = "",
        user_id: int | None = None,
        ttl_minutes: int = _PENDING_INTENT_TTL_MIN,
    ) -> int:
        """记录/更新一条待澄清任务（同会话只保留最新一条）

        过期时刻必须与读侧 ``get_active_pending_intent`` / ``_now_local`` 同源：
        旧写法用裸 ``datetime.now()``（依赖主机时区），在 UTC CI/容器上比
        北京墙钟慢 8 小时，pending 一落库就被判过期。
        """
        now_str = self._now_local()
        now_dt = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
        expires = (now_dt + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT id FROM pending_intents "
                "WHERE session_key = ? AND status = 'active'",
                (session_key,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE pending_intents SET intent = ?, slots_json = ?, "
                    "ask_count = ?, last_question = ?, updated_at = ?, expires_at = ? "
                    "WHERE id = ?",
                    (
                        intent,
                        json.dumps(slots, ensure_ascii=False),
                        ask_count,
                        last_question,
                        now_str,
                        expires,
                        row["id"],
                    ),
                )
                return row["id"]  # type: ignore[no-any-return]
            cursor = conn.execute(
                "INSERT INTO pending_intents (session_key, user_id, intent, slots_json, "
                "ask_count, last_question, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_key,
                    user_id,
                    intent,
                    json.dumps(slots, ensure_ascii=False),
                    ask_count,
                    last_question,
                    expires,
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_active_pending_intent(self, session_key: str) -> dict[str, Any] | None:
        """取会话当前待澄清任务；过期的顺带惰性置为 expired"""
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM pending_intents "
                "WHERE session_key = ? AND status = 'active'",
                (session_key,),
            ).fetchone()
            if row and row["expires_at"] and row["expires_at"] <= self._now_local():
                conn.execute(
                    "UPDATE pending_intents SET status = 'expired' WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()
                return None
            return dict(row) if row else None

    def resolve_pending_intent(self, session_key: str, status: str = "fulfilled") -> None:
        """关闭会话的待澄清任务（fulfilled / cancelled）"""
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE pending_intents SET status = ?, updated_at = ? "
                "WHERE session_key = ? AND status = 'active'",
                (status, self._now_local(), session_key),
            )
            conn.commit()

    def expire_stale_intents(self) -> int:
        """批量过期超时任务（调度器周期调用；返回过期条数）"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "UPDATE pending_intents SET status = 'expired', updated_at = ? "
                "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (self._now_local(), self._now_local()),
            )
            conn.commit()
            return cursor.rowcount  # type: ignore[no-any-return]
    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取记忆统计"""
        with self._conn() as conn:
            fact_count = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]
            chat_count = conn.execute("SELECT COUNT(*) FROM chat_history").fetchone()[0]
            today_chats = self.count_chats_today()
            affinity_count = conn.execute("SELECT COUNT(*) FROM affinity_log").fetchone()[0]
            latest_affinity = self.get_latest_affinity()

            return {
                "total_facts": fact_count,
                "total_chats": chat_count,
                "today_chats": today_chats,
                "affinity_records": affinity_count,
                "latest_affinity": latest_affinity,
            }

    def health_check(self) -> dict:
        """健康检查"""
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
                return {"connected": True, "path": self.db_path}
        except Exception:
            logger.exception("StructuredMemory健康检查异常")
            return {"connected": False, "error": "db_check_failed"}
