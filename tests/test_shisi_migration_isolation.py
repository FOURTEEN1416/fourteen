"""对抗验收 High 缺口回归（2026-11-07 v1.65.2）：shisi 默认库必须被 conftest 沙箱。

create_api_app() → setup_shisi(run_migrate=True) → run_migrations(None) 曾落到
shisi.migrations._DB_DEFAULT（真实 data/sqlite.db）执行迁移写库——对抗验收的
沙箱旅程以 md5 对照实锤（063bf109→6b918765）；conftest 此前恰好缺
shisi.migrations / shisi.character.store 两个 patch 点，pytest 全量中所有走
create_api_app 的用例同样暴露。本文件钉死防线：
1. conftest 把四个默认库常量全部重定向进 per-test 沙箱；
2. run_migrations(None) / CharacterStore() / FavoriteManager() / ForwardManager()
   落库都在沙箱内（行为级）；
3. 真实 data/sqlite.db 在整个过程中 md5 不变（最硬保证）。
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

REAL_DB = Path(__file__).resolve().parent.parent / "data" / "sqlite.db"

_GUARDED = [
    "shisi.migrations",
    "shisi.character.store",
    "shisi.memory.favorite_manager",
    "shisi.memory.forward_manager",
]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_conftest_redirects_all_shisi_default_db_paths() -> None:
    import importlib

    for mod_name in _GUARDED:
        mod = importlib.import_module(mod_name)
        value = str(mod._DB_DEFAULT)
        assert "runtime_state_" in value, f"{mod_name}._DB_DEFAULT 未进沙箱: {value}"
        assert value.endswith("shisi.db"), f"{mod_name}._DB_DEFAULT 文件名不符: {value}"


def test_run_migrations_none_lands_in_sandbox_not_real_db() -> None:
    from shisi import migrations

    before = _md5(REAL_DB) if REAL_DB.exists() else None

    migrations.run_migrations(None)  # 与 create_api_app 的无参调用同形态

    sandbox_db = Path(str(migrations._DB_DEFAULT))
    assert sandbox_db.exists(), "迁移没有落到沙箱库"
    with closing(sqlite3.connect(str(sandbox_db))) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables, "沙箱迁移库为空（迁移没有真正执行）"

    if before is not None:
        assert _md5(REAL_DB) == before, "真实 data/sqlite.db 被测试迁移改动"


def test_shisi_stores_default_to_sandbox_db() -> None:
    from shisi.character.store import CharacterStore
    from shisi.memory.favorite_manager import FavoriteManager
    from shisi.memory.forward_manager import ForwardManager

    for factory, attr in (
        (CharacterStore, "db_path"),
        (FavoriteManager, "_db_path"),
        (ForwardManager, "_db_path"),
    ):
        instance = factory()
        value = str(getattr(instance, attr))
        assert "runtime_state_" in value, f"{factory.__name__} 默认库未进沙箱: {value}"
        assert value.endswith("shisi.db"), f"{factory.__name__} 默认库文件名不符: {value}"
