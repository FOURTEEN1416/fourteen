"""characters_v2 表结构DDL"""

CHARACTERS_V2_DDL = """
CREATE TABLE IF NOT EXISTS characters_v2 (
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
)
"""

CHARACTERS_V2_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chars_v2_active ON characters_v2(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_chars_v2_updated ON characters_v2(updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_chars_v2_name ON characters_v2(name)",
]
