"""一步到位数据迁移运行器"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.infrastructure.persistence.sqlite_repository import SQLiteCharacterRepository


@dataclass
class MigrationResult:
    total_migrated: int = 0
    total_failed: int = 0
    errors: list[tuple] = field(default_factory=list)
    active_character: str | None = None
    backup_path: str | None = None


def run(db_path: Path = Path("data/sqlite.db"), char_dir: Path = Path("data/characters")) -> MigrationResult:
    result = MigrationResult()

    backup_path = db_path.parent / f"sqlite.db.backup.{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    if db_path.exists():
        shutil.copy(db_path, backup_path)
        result.backup_path = str(backup_path)
        print(f"1. ✓ 备份已创建: {backup_path}")
    else:
        print("1. ! 数据库不存在，将创建新数据库")

    old_characters = []
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT character_id, name, chara_card_json, "
                "format, is_active, tags, created_at, updated_at FROM characters"
            )
            for row in cursor.fetchall():
                old_characters.append(dict(row))
            print(f"2. ✓ 读取到 {len(old_characters)} 个旧角色")
        except sqlite3.OperationalError as e:
            print(f"2. ! 旧表不存在或结构不同: {e}")
        finally:
            conn.close()

    char_files = list(char_dir.glob("*.json")) if char_dir.exists() else []
    print(f"3. ✓ 发现 {len(char_files)} 个角色文件")

    new_repo = SQLiteCharacterRepository(db_path)
    print("4. ✓ 新表结构已创建")

    migrated_ids = set()
    for old_char in old_characters:
        try:
            chara_card = json.loads(old_char["chara_card_json"])
            chara_card["character_id"] = old_char["character_id"]
            new_char = CharacterAggregate.from_legacy_card(chara_card)
            new_repo.save(new_char)
            migrated_ids.add(new_char.id)
            if old_char.get("is_active"):
                new_repo.set_active(new_char.id)
                result.active_character = new_char.name
            result.total_migrated += 1
            print(f"5. ✓ 迁移: {new_char.name}")
        except Exception as e:  # noqa: BLE001
            result.total_failed += 1
            result.errors.append((old_char.get("name", "unknown"), str(e)))
            print(f"5. ✗ 失败: {old_char.get('name', 'unknown')} - {e}")

    for file_path in char_files:
        try:
            with open(file_path, encoding="utf-8") as f:
                file_data = json.load(f)
            char_id = file_path.stem
            if char_id in migrated_ids:
                continue
            file_data["character_id"] = char_id
            new_char = CharacterAggregate.from_legacy_card(file_data)
            new_repo.save(new_char)
            result.total_migrated += 1
            print(f"6. ✓ 从文件迁移: {new_char.name}")
        except Exception as e:  # noqa: BLE001
            result.total_failed += 1
            result.errors.append((file_path.name, str(e)))
            print(f"6. ✗ 文件迁移失败: {file_path.name} - {e}")

    active_char = new_repo.get_active()
    if active_char:
        result.active_character = active_char.name

    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("ALTER TABLE characters RENAME TO characters_legacy")
            conn.commit()
            print("7. ✓ 旧表已重命名为 characters_legacy")
        except Exception as e:  # noqa: BLE001
            print(f"7. ! 跳过重命名: {e}")
        finally:
            conn.close()

    return result
