"""W8 缺陷 J：成就的日记指标跨角色 / 跨用户 / 跨进程计数。

`api/achievement_engine._count_diary()` 的实现是 `len(ds.get_all_summaries())`，
而 `collect_metrics(character_id)` 其余五项（记忆事实 / 知识块 / 重要日期 / 音色 /
收藏）全部按角色取数 —— 唯独日记是**全库总数**，于是：

1. 无角色维度：角色 A 攒够 7 篇日记，角色 B 的「相伴七日」同样显示达成，
   B 永远无法靠**自己**的相处达成，也无法证明它没达成；
2. 无归属维度：任意用户、任意会话的日记都计入同一读数；
3. 取数源是**每进程内存缓存** `DiarySummarizer._daily_summaries`（只在启动时
   `load_summaries_from_db()` 一次）—— 调度器进程落库的新日记对处理 HTTP 请求的
   worker 不可见，同一指标在 GET 接口与每日维护之间读成两个数；
4. 按**行数**计：同一天的两种会话键形态（`N:peer` 与裸 `peer`）各生成一条摘要，
   一天被算成两天。

修复后口径：日记 = 该角色可归属的、**去重后的日期数**，取数源为
`daily_summaries` 表（跨进程真源）；无角色段的旧条目/手动 seed 条目不归给任何角色。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

import api.achievement_engine as engine

MI = "miCai"
LIN = "linWanXia"


class _FakeDS:
    """进程内存视图（旧实现的取数源）。"""

    def __init__(self, keys):
        self._keys = keys

    def get_all_summaries(self):
        return {k: "摘要" for k in self._keys}


class _FakeSM:
    def __init__(self, db: Path):
        self._db = db

    @contextmanager
    def get_connection(self, write: bool = False):
        conn = sqlite3.connect(str(self._db))
        try:
            yield conn
        finally:
            conn.close()


class _FakeMem:
    def __init__(self, sm, ds):
        self.structured_memory = sm
        self.ds = ds


class _FakeOrch:
    def __init__(self, mem):
        self.components = {"memory": mem}


class _FakeDeps:
    def __init__(self, mem):
        self.orch = _FakeOrch(mem)


def _summary_db(tmp_path, keys, *, with_table: bool = True) -> Path:
    db = tmp_path / "sqlite.db"
    if with_table:
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_summaries ("
            " date TEXT PRIMARY KEY, summary TEXT NOT NULL,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO daily_summaries (date, summary) VALUES (?, ?)",
            [(k, "摘要") for k in keys],
        )
        conn.commit()
        conn.close()
    else:  # 表尚未由日记器创建
        db.write_bytes(b"")
    return db


@pytest.fixture()
def hermetic(monkeypatch):
    """把与本案无关的按角色事实源钉成 0，避免碰真实 Chroma / 真实日期文件。"""
    for name in ("_count_memory_facts", "_count_knowledge", "_count_dates",
                 "_count_favorites", "_voice_bound"):
        monkeypatch.setattr(engine, name, lambda *a, **k: 0)


def _wire(monkeypatch, tmp_path, *, db_keys, cache_keys, with_table=True):
    db = _summary_db(tmp_path, db_keys, with_table=with_table)
    mem = _FakeMem(_FakeSM(db), _FakeDS(cache_keys))
    monkeypatch.setattr(engine, "deps", _FakeDeps(mem))
    return mem


# ═══════════════════════════════════════════════════════════
# 1. 角色维度
# ═══════════════════════════════════════════════════════════


def test_diary_metric_is_character_scoped(monkeypatch, tmp_path):
    """A 的 7 篇不得替 B 达成「相伴七日」。"""
    keys = [f"4:peerA|{MI}|2026-09-{d:02d}" for d in range(1, 8)]
    keys += [f"4:peerA|{LIN}|2026-09-{d:02d}" for d in range(1, 3)]
    _wire(monkeypatch, tmp_path, db_keys=keys, cache_keys=keys)

    assert engine._count_diary(MI) == 7
    assert engine._count_diary(LIN) == 2, "日记指标全局计数：B 借了 A 的日记"


def test_unscoped_entries_belong_to_nobody(monkeypatch, tmp_path):
    """旧条目/手动 seed 的键没有角色段 —— 不得算到任何角色头上。"""
    keys = ["2026-09-01", "2026-09-02", "4:peerA|2026-09-03"]
    _wire(monkeypatch, tmp_path, db_keys=keys, cache_keys=keys)
    assert engine._count_diary(MI) == 0


# ═══════════════════════════════════════════════════════════
# 2. 跨进程真源：GET 与每日维护读同一个数
# ═══════════════════════════════════════════════════════════


def test_diary_metric_reads_db_not_process_cache(monkeypatch, tmp_path):
    """本进程缓存陈旧（只看到启动时的 1 篇），真源表已有 7 篇。"""
    db_keys = [f"4:peerA|{MI}|2026-09-{d:02d}" for d in range(1, 8)]
    _wire(monkeypatch, tmp_path, db_keys=db_keys, cache_keys=db_keys[:1])

    assert engine._count_diary(MI) == 7, "读内存缓存 → 调度器进程新写的日记永不计入"


def test_two_processes_agree_on_progress(monkeypatch, tmp_path, hermetic):
    """两个进程视图（worker 的 GET / 调度器的每日维护）必须给同一进度。"""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_keys = [f"4:peerA|{MI}|2026-09-{d:02d}" for d in range(1, 8)]
    mem = _FakeMem(_FakeSM(_summary_db(tmp_path, db_keys)), _FakeDS([]))
    monkeypatch.setattr(engine, "deps", _FakeDeps(mem))

    async def _recalc():
        engine_url = f"sqlite+aiosqlite:///{tmp_path / 'ach.db'}"
        aeng = create_async_engine(engine_url)
        from api.database import Base

        async with aeng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(aeng, expire_on_commit=False)
        async with factory() as session:
            first = await engine.recalculate_achievements(session, MI)
        mem.ds = _FakeDS(db_keys[:1])  # 模拟另一进程：缓存陈旧但表相同
        async with factory() as session:
            second = await engine.recalculate_achievements(session, MI)
        async with factory() as session:
            other = await engine.recalculate_achievements(session, LIN)
        await aeng.dispose()
        return first, second, other

    first, second, other = asyncio.run(_recalc())
    week = lambda items: next(i for i in items if i["achievement_id"] == "companion_week")  # noqa: E731

    assert week(first)["progress"] == 7
    assert week(second)["progress"] == 7, "同库两进程读数不一致（其一读了陈旧内存缓存）"
    assert week(first)["unlocked"] is True
    assert week(other)["progress"] == 0, "他角色的日记替本角色达成"
    assert week(other)["unlocked"] is False


# ═══════════════════════════════════════════════════════════
# 3. 同一天只算一天
# ═══════════════════════════════════════════════════════════


def test_same_day_two_session_forms_count_once(monkeypatch, tmp_path):
    """`N:peer` 与裸 `peer` 是同一天，不得按行数算成两天。"""
    keys = [f"4:peerA|{MI}|2026-09-{d:02d}" for d in range(1, 7)]
    keys += [f"4:peerA|{MI}|2026-09-07", f"peerA|{MI}|2026-09-07"]
    _wire(monkeypatch, tmp_path, db_keys=keys, cache_keys=keys)
    assert engine._count_diary(MI) == 7


# ═══════════════════════════════════════════════════════════
# 4. 降级：表不存在时不抛，退回进程视图
# ═══════════════════════════════════════════════════════════


def test_missing_table_degrades_to_process_view(monkeypatch, tmp_path):
    cache_keys = [f"4:peerA|{MI}|2026-09-0{d}" for d in range(1, 4)]
    _wire(monkeypatch, tmp_path, db_keys=[], cache_keys=cache_keys, with_table=False)
    assert engine._count_diary(MI) == 3


def test_no_orchestrator_returns_zero(monkeypatch):
    monkeypatch.setattr(engine, "deps", _FakeDeps(None))
    assert engine._count_diary(MI) == 0
