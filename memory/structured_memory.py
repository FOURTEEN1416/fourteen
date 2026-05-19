"""
结构化记忆系统 — 基于 SQLite

管理结构化数据：
- user_facts: 用户事实（偏好、习惯、事件）
- affinity_log: 好感度变化记录
- chat_history: 对话历史（结构化版本）
- reminders: 提醒事项
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("structured_memory")


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

        self._init_db()
        logger.info("StructuredMemory ready: %s", self.db_path)

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None

    def __del__(self):
        self.close()

    def _init_db(self) -> None:
        """初始化数据库和表结构"""
        with self._conn() as conn:
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
            """)
            conn.commit()

    @contextmanager
    def _conn(self):
        yield self._connection

    # ── 用户事实 ──────────────────────────────────────────

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "") -> int:
        """添加用户事实"""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO user_facts (fact, category, confidence, source) VALUES (?, ?, ?, ?)",
                (fact, category, confidence, source),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_facts(self, category: Optional[str] = None,
                  min_confidence: float = 0.0,
                  limit: int = 50) -> List[Dict[str, Any]]:
        """获取用户事实"""
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM user_facts WHERE category = ? AND confidence >= ? ORDER BY updated_at DESC LIMIT ?",
                    (category, min_confidence, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM user_facts WHERE confidence >= ? ORDER BY updated_at DESC LIMIT ?",
                    (min_confidence, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def search_facts(self, keyword: str) -> List[Dict[str, Any]]:
        """关键词搜索事实"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM user_facts WHERE fact LIKE ? ORDER BY confidence DESC LIMIT 20",
                (f"%{keyword}%",),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_fact_confidence(self, fact_id: int, confidence: float) -> None:
        """更新事实置信度"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE user_facts SET confidence = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (confidence, fact_id),
            )
            conn.commit()

    def delete_fact(self, fact_id: int) -> None:
        """删除事实"""
        with self._conn() as conn:
            conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
            conn.commit()

    # ── 好感度日志 ────────────────────────────────────────

    def add_affinity_log(self, level: int, level_name: str,
                         affection_points: float, reason: str = "") -> int:
        """记录好感度变化"""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO affinity_log (level, level_name, affection_points, reason) VALUES (?, ?, ?, ?)",
                (level, level_name, affection_points, reason),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_affinity_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取好感度历史"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_affinity(self) -> Optional[Dict[str, Any]]:
        """获取最新好感度记录"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM affinity_log ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    # ── 聊天历史 ──────────────────────────────────────────

    def add_chat(self, role: str, content: str,
                 emotion_tag: str = "", session_id: str = "") -> int:
        """添加聊天记录"""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO chat_history (role, content, emotion_tag, session_id) VALUES (?, ?, ?, ?)",
                (role, content, emotion_tag, session_id),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_recent_chats(self, n: int = 20) -> List[Dict[str, Any]]:
        """获取最近 N 条聊天"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history ORDER BY created_at DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]  # 反转成时间正序

    def get_chats_by_session(self, session_id: str) -> List[Dict[str, Any]]:
        """获取某次会话的聊天"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chats_today(self) -> List[Dict[str, Any]]:
        """获取今天的聊天"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE date(created_at) = date('now') ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def count_chats_today(self) -> int:
        """今天聊了多少条"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_history WHERE date(created_at) = date('now')"
            ).fetchone()
            return row["cnt"] if row else 0

    # ── 提醒 ──────────────────────────────────────────────

    def add_reminder(self, content: str, trigger_time: Optional[str] = None) -> int:
        """添加提醒"""
        with self._conn() as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (content, trigger_time) VALUES (?, ?)",
                (content, trigger_time),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_pending_reminders(self) -> List[Dict[str, Any]]:
        """获取待触发的提醒"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND (trigger_time IS NULL OR trigger_time <= datetime('now')) "
                "ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_triggered(self, reminder_id: int) -> None:
        """标记提醒已触发"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE reminders SET triggered = 1 WHERE id = ?",
                (reminder_id,),
            )
            conn.commit()

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
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
        except Exception as e:
            return {"connected": False, "error": str(e)}
