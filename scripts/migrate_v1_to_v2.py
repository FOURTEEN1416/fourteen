from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("migrate_v1_to_v2")

NEW_TABLES_SQL = """
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

CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(is_active);
CREATE INDEX IF NOT EXISTS idx_working_session ON working_memory(session_id);
CREATE INDEX IF NOT EXISTS idx_pending_time ON pending_events(expected_time);
CREATE INDEX IF NOT EXISTS idx_trace_id ON trace_log(trace_id);
CREATE INDEX IF NOT EXISTS idx_emotion_traj_time ON emotion_trajectory(created_at);
"""


def migrate(db_path: str = "./data/sqlite.db"):
    logger.info("Starting V1 -> V2 migration for %s", db_path)
    conn = sqlite3.connect(db_path)
    try:
        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.executescript(NEW_TABLES_SQL)
        conn.commit()

        new_tables = {"sessions", "working_memory", "pending_events", "persona_evolution_log",
                      "tool_call_log", "emotion_trajectory", "trace_log"}
        created = new_tables - existing_tables
        logger.info("Migration complete. Created tables: %s", created)
        return {"success": True, "created_tables": list(created)}
    except Exception as e:
        logger.error("Migration failed: %s", e)
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = migrate()
    print(result)
