"""十四模块数据库迁移 — 建表SQL与迁移执行器。"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

_DB_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

_MIGRATIONS: list[str] = [
    """CREATE TABLE IF NOT EXISTS characters (
        character_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        chara_card_json TEXT NOT NULL,
        format TEXT NOT NULL DEFAULT 'chara_card_v2',
        is_active INTEGER NOT NULL DEFAULT 0,
        avatar_url TEXT,
        tags TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS affinity_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id TEXT NOT NULL,
        old_value REAL NOT NULL,
        new_value REAL NOT NULL,
        delta REAL NOT NULL,
        reason TEXT,
        source TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (character_id) REFERENCES characters(character_id)
    )""",
    """CREATE TABLE IF NOT EXISTS affinity_unlocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id TEXT NOT NULL,
        threshold INTEGER NOT NULL,
        unlock_type TEXT NOT NULL,
        unlock_name TEXT NOT NULL,
        unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(character_id, threshold, unlock_type)
    )""",
    """CREATE TABLE IF NOT EXISTS affinity_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS emotion_stage_state (
        character_id TEXT PRIMARY KEY,
        current_stage TEXT NOT NULL DEFAULT '陌生',
        stage_index INTEGER NOT NULL DEFAULT 0,
        affinity_value REAL NOT NULL DEFAULT 0.0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (character_id) REFERENCES characters(character_id)
    )""",
    """CREATE TABLE IF NOT EXISTS stickers (
        sticker_id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        emotion_tags TEXT NOT NULL,
        file_path TEXT NOT NULL,
        format TEXT NOT NULL DEFAULT 'png',
        character_id TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS character_stickers (
        character_id TEXT NOT NULL,
        sticker_id TEXT NOT NULL,
        unlock_threshold INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (character_id, sticker_id),
        FOREIGN KEY (character_id) REFERENCES characters(character_id),
        FOREIGN KEY (sticker_id) REFERENCES stickers(sticker_id)
    )""",
    """CREATE TABLE IF NOT EXISTS vital_signs_state (
        character_id TEXT PRIMARY KEY,
        heart_rate REAL NOT NULL DEFAULT 72.0,
        temperature REAL NOT NULL DEFAULT 36.5,
        breath_rate REAL NOT NULL DEFAULT 16.0,
        last_emotion TEXT NOT NULL DEFAULT 'neutral',
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (character_id) REFERENCES characters(character_id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        favorited_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(character_id, memory_id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_recycle_bin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        character_id TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        memory_content TEXT NOT NULL,
        deleted_at TEXT NOT NULL DEFAULT (datetime('now')),
        restore_before TEXT NOT NULL,
        restored INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS shisi_schema_version (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_affinity_records_cid ON affinity_records(character_id)""",
    """CREATE INDEX IF NOT EXISTS idx_affinity_audit_cid ON affinity_audit(character_id)""",
    """CREATE INDEX IF NOT EXISTS idx_affinity_unlocks_cid ON affinity_unlocks(character_id)""",
    """CREATE INDEX IF NOT EXISTS idx_stickers_category ON stickers(category)""",
    """CREATE INDEX IF NOT EXISTS idx_memory_fav_cid ON memory_favorites(character_id)""",
    """CREATE INDEX IF NOT EXISTS idx_memory_recycle_cid ON memory_recycle_bin(character_id)""",
    """CREATE TABLE IF NOT EXISTS characters_v2 (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        avatar_url TEXT,
        tags TEXT DEFAULT '[]',
        persona_json TEXT NOT NULL,
        emotional_state_json TEXT NOT NULL,
        is_active INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        version INTEGER DEFAULT 1,
        source_format TEXT DEFAULT 'chara_card_v2',
        source_data_json TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chars_v2_active ON characters_v2(is_active)""",
    """CREATE INDEX IF NOT EXISTS idx_chars_v2_updated ON characters_v2(updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_chars_v2_name ON characters_v2(name)""",
]


def get_table_names() -> list[str]:
    return [
        "characters", "affinity_records", "affinity_unlocks", "affinity_audit",
        "emotion_stage_state", "stickers", "character_stickers",
        "vital_signs_state", "memory_favorites", "memory_recycle_bin",
        "shisi_schema_version", "characters_v2",
    ]


def run_migrations(db_path: Path | str | None = None) -> Sequence[str]:
    path = Path(db_path) if db_path else _DB_DEFAULT
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()

        applied: list[str] = []
        for i, sql in enumerate(_MIGRATIONS):
            conn.execute(sql)
            applied.append(f"migration_{i:03d}")

        cursor.execute(
            "INSERT OR REPLACE INTO shisi_schema_version VALUES (?, ?)",
            ("version", "1.0"),
        )
        cursor.execute(
            "INSERT OR REPLACE INTO shisi_schema_version VALUES (?, ?)",
            ("migrations_applied", str(len(_MIGRATIONS))),
        )
        conn.commit()
        return applied
    finally:
        conn.close()


def verify_tables(db_path: Path | str | None = None) -> dict[str, bool]:
    path = Path(db_path) if db_path else _DB_DEFAULT
    conn = sqlite3.connect(str(path))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing = {row[0] for row in cursor.fetchall()}
        return {name: name in existing for name in get_table_names()}
    finally:
        conn.close()
