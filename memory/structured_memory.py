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
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

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
        except Exception as e:
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
                except Exception as e:
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

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
            conn.commit()  # type: ignore

    @contextmanager
    def _conn(self, write: bool = False):  # type: ignore
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

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "") -> int:
        """添加用户事实"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(  # type: ignore
                "INSERT INTO user_facts (fact, category, confidence, source) VALUES (?, ?, ?, ?)",
                (fact, category, confidence, source),
            )
            conn.commit()  # type: ignore
            return cursor.lastrowid  # type: ignore

    def get_facts(self, category: str | None = None,
                  min_confidence: float = 0.0,
                  limit: int = 50) -> list[dict[str, Any]]:
        """获取用户事实"""
        with self._conn() as conn:
            if category:
                rows = conn.execute(  # type: ignore
                    "SELECT * FROM user_facts WHERE category = ? AND confidence >= ? ORDER BY updated_at DESC LIMIT ?",
                    (category, min_confidence, limit),
                ).fetchall()
            else:
                rows = conn.execute(  # type: ignore
                    "SELECT * FROM user_facts WHERE confidence >= ? ORDER BY updated_at DESC LIMIT ?",
                    (min_confidence, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def search_facts(self, keyword: str) -> list[dict[str, Any]]:
        """关键词搜索事实 — 优先FTS5，降级LIKE"""
        with self._conn() as conn:
            try:
                rows = conn.execute(  # type: ignore
                    """SELECT f.* FROM user_facts f
                       JOIN user_facts_fts fts ON f.id = fts.rowid
                       WHERE user_facts_fts MATCH ?
                       ORDER BY rank
                       LIMIT 20""",
                    (keyword,),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except Exception as e:
                logger.debug("FTS5 search failed, falling back to LIKE: %s", e)
            rows = conn.execute(  # type: ignore
                "SELECT * FROM user_facts WHERE fact LIKE ? ORDER BY confidence DESC LIMIT 20",
                (f"%{keyword}%",),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_fact_confidence(self, fact_id: int, confidence: float) -> None:
        """更新事实置信度"""
        with self._conn(write=True) as conn:
            conn.execute(  # type: ignore
                "UPDATE user_facts SET confidence = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (confidence, fact_id),
            )
            conn.commit()  # type: ignore

    def delete_fact(self, fact_id: int) -> None:
        """删除事实"""
        with self._conn(write=True) as conn:
            conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))  # type: ignore
            conn.commit()  # type: ignore

    def add_facts_batch(self, facts: list[dict[str, Any]]) -> list[int]:
        """批量添加事实"""
        if not facts:
            return []
        with self._conn(write=True) as conn:
            rows = self._execute_write(
                lambda: (
                    conn.executemany(  # type: ignore
                        "INSERT INTO user_facts (fact, category, confidence, source) VALUES (?, ?, ?, ?)",
                        [(f.get("fact", ""), f.get("category", "general"),
                          f.get("confidence", 0.5), f.get("source", "")) for f in facts],
                    ),
                    conn.commit(),  # type: ignore
                    conn.execute("SELECT last_insert_rowid()").fetchone()[0],  # type: ignore
                )[2]
            )
            start_id = rows - len(facts) + 1  # type: ignore
            return list(range(start_id, start_id + len(facts)))

    # ── 好感度日志 ────────────────────────────────────────

    def add_affinity_log(self, level: int, level_name: str,
                         affection_points: float, reason: str = "") -> int:
        """记录好感度变化"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(  # type: ignore
                "INSERT INTO affinity_log (level, level_name, affection_points, reason) VALUES (?, ?, ?, ?)",
                (level, level_name, affection_points, reason),
            )
            conn.commit()  # type: ignore
            return cursor.lastrowid  # type: ignore

    def get_affinity_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取好感度历史"""
        with self._conn() as conn:
            rows = conn.execute(  # type: ignore
                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_affinity(self) -> dict[str, Any] | None:
        """获取最新好感度记录"""
        with self._conn() as conn:
            row = conn.execute(  # type: ignore
                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # ── 聊天历史 ──────────────────────────────────────────

    def add_chat(self, role: str, content: str,
                 emotion_tag: str = "", session_id: str = "") -> int:
        """添加聊天记录"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(  # type: ignore
                "INSERT INTO chat_history (role, content, emotion_tag, session_id) VALUES (?, ?, ?, ?)",
                (role, content, emotion_tag, session_id),
            )
            conn.commit()  # type: ignore
            return cursor.lastrowid  # type: ignore

    def get_recent_chats(self, n: int = 20) -> list[dict[str, Any]]:
        """获取最近 N 条聊天"""
        with self._conn() as conn:
            rows = conn.execute(  # type: ignore
                "SELECT * FROM chat_history ORDER BY created_at DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]  # 反转成时间正序

    def get_chats_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """获取某次会话的聊天"""
        with self._conn() as conn:
            rows = conn.execute(  # type: ignore
                "SELECT * FROM chat_history WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chats_today(self) -> list[dict[str, Any]]:
        """获取今天的聊天"""
        with self._conn() as conn:
            rows = conn.execute(  # type: ignore
                "SELECT * FROM chat_history WHERE date(created_at) = date('now') ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def count_chats_today(self) -> int:
        """今天聊了多少条"""
        with self._conn() as conn:
            row = conn.execute(  # type: ignore
                "SELECT COUNT(*) as cnt FROM chat_history WHERE date(created_at) = date('now')"
            ).fetchone()
            return row["cnt"] if row else 0

    # ── 提醒 ──────────────────────────────────────────────

    def add_reminder(self, content: str, trigger_time: str | None = None) -> int:
        """添加提醒"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(  # type: ignore
                "INSERT INTO reminders (content, trigger_time) VALUES (?, ?)",
                (content, trigger_time),
            )
            conn.commit()  # type: ignore
            return cursor.lastrowid  # type: ignore

    def get_pending_reminders(self) -> list[dict[str, Any]]:
        """获取待触发的提醒"""
        with self._conn() as conn:
            rows = conn.execute(  # type: ignore
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND (trigger_time IS NULL OR trigger_time <= datetime('now')) "
                "ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_triggered(self, reminder_id: int) -> None:
        """标记提醒已触发"""
        with self._conn(write=True) as conn:
            conn.execute(  # type: ignore
                "UPDATE reminders SET triggered = 1 WHERE id = ?",
                (reminder_id,),
            )
            conn.commit()  # type: ignore

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取记忆统计"""
        with self._conn() as conn:
            fact_count = conn.execute("SELECT COUNT(*) FROM user_facts").fetchone()[0]  # type: ignore
            chat_count = conn.execute("SELECT COUNT(*) FROM chat_history").fetchone()[0]  # type: ignore
            today_chats = self.count_chats_today()
            affinity_count = conn.execute("SELECT COUNT(*) FROM affinity_log").fetchone()[0]  # type: ignore

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
                conn.execute("SELECT 1")  # type: ignore
                return {"connected": True, "path": self.db_path}
        except Exception:
            logger.exception("StructuredMemory健康检查异常")
            return {"connected": False, "error": "db_check_failed"}
