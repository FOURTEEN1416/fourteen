"""十四模块数据库迁移 — 建表SQL与迁移执行器。"""

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

_DB_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

# 迁移条目：纯 SQL 字符串（须自身幂等，如 CREATE ... IF NOT EXISTS），或
# 接收连接的 callable（用于无法用单条 SQL 幂等表达的变更——如条件 ALTER、
# 数据清理；callable 内部自行保证幂等）。
Migration = str | Callable[[sqlite3.Connection], None]


def _migrate_user_persona_dimensions(conn: sqlite3.Connection) -> None:
    from persona_extractor.persona_bank import (
        CREATE_USER_PERSONA_TABLE,
        CREATE_USER_SNAPSHOT_TABLE,
        PERSONA_DIMENSION_COLUMNS,
    )

    conn.execute(CREATE_USER_PERSONA_TABLE)
    conn.execute(CREATE_USER_SNAPSHOT_TABLE)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(user_persona)")}
    for col in PERSONA_DIMENSION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE user_persona ADD COLUMN {col} TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_user_time "
        "ON user_persona_snapshots (user_id, timestamp DESC)"
    )


def _privatize_persona_trigger_messages(conn: sqlite3.Connection) -> None:
    from persona_extractor.persona_bank import CREATE_USER_SNAPSHOT_TABLE

    conn.execute(CREATE_USER_SNAPSHOT_TABLE)
    conn.execute(
        "UPDATE user_persona_snapshots SET trigger_message = '' "
        "WHERE trigger_message != '' "
        "AND trigger_message NOT LIKE 'sha256:%'"
    )


_MIGRATIONS: list[Migration] = [
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
    # 批6b 项8：转发历史落库。旧 ForwardManager 只存进程内 list，
    # 重启即丢（前端「转发收藏」端点的历史记录随之蒸发）。
    """CREATE TABLE IF NOT EXISTS memory_forwards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_character TEXT NOT NULL,
        to_character TEXT NOT NULL,
        memory_id TEXT NOT NULL,
        content TEXT DEFAULT '',
        forwarded_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    """CREATE INDEX IF NOT EXISTS idx_memory_forwards_to ON memory_forwards(to_character)""",
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
    # W13 · D11：user_persona 五维度列（hexaco/dark_triad/mental_health/liwc/
    # cognitive）。persona_bank 的自治建表对新库直接建全列；本迁移负责存量旧
    # 结构库的幂等补列。DDL 与列名真源复用 persona_bank 常量防同构漂移。
    _migrate_user_persona_dimensions,
    # W13 · D11：清空快照表存量原话（trigger_message 曾存用户原话前 200 字、
    # 无 TTL）。只清非 sha256: 格式行 ⇒ 幂等，且不伤隐私化后的新写入。
    _privatize_persona_trigger_messages,
]


def get_table_names() -> list[str]:
    return [
        "characters", "affinity_records", "affinity_unlocks", "affinity_audit",
        "emotion_stage_state", "stickers", "character_stickers",
        "vital_signs_state", "memory_favorites", "memory_forwards", "memory_recycle_bin",
        "shisi_schema_version", "characters_v2",
        "user_persona", "user_persona_snapshots",
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
        for i, migration in enumerate(_MIGRATIONS):
            if callable(migration):
                migration(conn)
            else:
                conn.execute(migration)
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
