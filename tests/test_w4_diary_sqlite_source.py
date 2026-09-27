"""W4 · 日记跨 worker 读 SQLite 真源（缺陷 H / 包任务 8）。

钉住：
1. `DiarySummarizer.get_all_summaries` 优先读 `daily_summaries` 表（跨进程真源），
   不是只读本实例 `_daily_summaries` 启动缓存；
2. worker A `save_summary` 落库后，worker B 新建 summarizer 不经
   `load_summaries_from_db` 也能读到（模拟多 worker）；
3. 可信账号范围过滤仍由调用方前缀完成，真源读取不吞掉键格式
   （`user_key|character|date` 原样返回）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shisi.memory.legacy._legacy_diary_summarizer import DiarySummarizer


class _SM:
    def __init__(self, db: Path):
        self._db = db

    def get_connection(self):
        conn = sqlite3.connect(str(self._db))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # contextmanager 兼容：上面是生成器，包一层
    def __enter__(self):
        raise NotImplementedError


class _SMCtx:
    """与 StructuredMemory.get_connection 同用法的最小替身。"""

    def __init__(self, db: Path):
        self._db = db

    def get_connection(self):
        return _ConnCtx(self._db)


class _ConnCtx:
    def __init__(self, db: Path):
        self._db = db
        self._conn = None

    def __enter__(self):
        self._conn = sqlite3.connect(str(self._db))
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def __exit__(self, *exc):
        if self._conn is not None:
            self._conn.close()
        return False


def test_get_all_summaries_reads_sqlite_not_process_cache(tmp_path):
    db = tmp_path / "sqlite.db"
    sm = _SMCtx(db)
    # 另一 worker 写库（不经过本实例）
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_summaries ("
        " date TEXT PRIMARY KEY, summary TEXT NOT NULL,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO daily_summaries (date, summary) VALUES (?,?)",
        ("1:peer-a@im.wechat|c1|2026-09-26", "昨天聊了咖啡"),
    )
    conn.commit()
    conn.close()

    # 本实例：空缓存、未 load
    ds = DiarySummarizer(llm_func=None, structured_memory=sm)
    assert ds._daily_summaries == {}
    got = ds.get_all_summaries()
    assert "1:peer-a@im.wechat|c1|2026-09-26" in got
    assert got["1:peer-a@im.wechat|c1|2026-09-26"] == "昨天聊了咖啡"


def test_save_summary_visible_to_other_worker_without_load(tmp_path):
    db = tmp_path / "sqlite.db"
    sm = _SMCtx(db)
    a = DiarySummarizer(llm_func=None, structured_memory=sm)
    a.save_summary("2:peer-b@im.wechat|c2|2026-09-26", "今天一起看展")
    b = DiarySummarizer(llm_func=None, structured_memory=sm)
    got = b.get_all_summaries()
    assert got.get("2:peer-b@im.wechat|c2|2026-09-26") == "今天一起看展"


def test_scope_prefix_filter_still_works_on_real_source(tmp_path):
    """API 归属前缀过滤依赖键原样返回，真源读取不得改键。"""
    db = tmp_path / "sqlite.db"
    sm = _SMCtx(db)
    ds = DiarySummarizer(llm_func=None, structured_memory=sm)
    ds.save_summary("7:peer@im.wechat|c1|2026-09-26", "七号的日记")
    ds.save_summary("8:other@im.wechat|c1|2026-09-26", "八号的日记")
    got = ds.get_all_summaries()
    scoped = {k: v for k, v in got.items() if k.startswith("7:")}
    assert list(scoped) == ["7:peer@im.wechat|c1|2026-09-26"]
    assert "8:other@im.wechat|c1|2026-09-26" in got
