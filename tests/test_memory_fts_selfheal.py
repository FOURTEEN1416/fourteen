"""记忆域 P0（2026-09-24）：user_facts_fts 虚表残缺自愈 + 写入失败可见性。

生产实证（服务器 app.log 46 条告警，2026-09-21 12:45 起）：
- `user_facts_fts` shadow 表残缺（``_config`` 0 行）→ 虚表构造失败；
- 同步触发器 ``facts_fts_insert`` 使 ``user_facts`` 主表 INSERT 整体回滚；
- ``CREATE VIRTUAL TABLE IF NOT EXISTS`` 对残缺态不作为 → 永不自愈；
- ``SemanticMemory.add_fact`` 宽 except 只 warning + ``return False``；
- ``remember_facts`` 工具把返回的 ``False`` 当 fid 写进 ``written`` →
  ``ToolResult(True)`` 假成功（第二层谎报）。

本文件五组用例分别钉住：损坏可复现（前提）／初始化自愈（根治）／
失败日志级别（可观测）／工具假成功（反馈链）／默认路径隔离钩子（防复发）。
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from shisi.memory.legacy.structured_memory import StructuredMemory

_TEST_FACT = "用户最喜欢的颜色是蓝色"


def _make_db(tmp_path) -> str:
    """建一个结构完好（FTS 健康）的临时库。"""
    db = str(tmp_path / "mem.db")
    sm = StructuredMemory(db)
    sm.close()
    return db


def _corrupt_fts(db: str) -> None:
    """模拟生产损坏态：shadow 表 ``_config`` 消失（虚表定义仍在）。"""
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE user_facts_fts_config")
    conn.commit()
    conn.close()


# ── 1. 损坏可复现（前提钉子：残缺态确实让主表写入失败） ──────────────


def test_corrupted_fts_blocks_user_facts_insert(tmp_path):
    db = _make_db(tmp_path)
    _corrupt_fts(db)
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.OperationalError, match="vtable constructor"):
        conn.execute(
            "INSERT INTO user_facts(fact, category, confidence) VALUES(?,?,?)",
            (_TEST_FACT, "t", 0.5),
        )
    conn.close()


# ── 2. 初始化自愈（核心红测：旧代码 IF NOT EXISTS 不作为） ───────────


def test_self_heal_rebuilds_corrupted_fts_on_init(tmp_path):
    db = _make_db(tmp_path)
    _corrupt_fts(db)
    # 重新实例化（触发 _init_db）→ 必须自愈
    sm = StructuredMemory(db)
    try:
        n = sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM user_facts_fts_config"
        ).fetchone()[0]
        assert n >= 1, "FTS _config 仍为空：残缺未被自愈"
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO user_facts(fact, category, confidence) VALUES(?,?,?)",
            (_TEST_FACT, "t", 0.5),
        )
        conn.commit()
        conn.close()
    finally:
        sm.close()


def test_self_heal_noop_on_healthy_db(tmp_path):
    """健康库不得被自愈误伤（虚表与数据保持原样）。"""
    db = _make_db(tmp_path)
    sm = StructuredMemory(db)
    try:
        fid = sm.add_fact(_TEST_FACT, category="t", confidence=0.5)
        assert isinstance(fid, int) and fid > 0
    finally:
        sm.close()
    # 再次实例化（自愈检查跑第二遍）→ 数据仍在
    sm2 = StructuredMemory(db)
    try:
        rows = sm2.search_facts("颜色")
        assert rows, "健康库被自愈误伤：事实丢失"
    finally:
        sm2.close()


# ── 3. 失败可见性：add_fact 失败必须 error 级（旧代码只 warning） ────


def test_semantic_add_fact_failure_is_error_level(caplog):
    from shisi.memory.legacy.semantic_memory import SemanticMemory

    class _BrokenSM:
        @staticmethod
        def add_fact(*_a, **_k):
            raise RuntimeError("vtable constructor failed: user_facts_fts")

    sem = SemanticMemory(vector_memory=None, structured_memory=_BrokenSM())
    with caplog.at_level(logging.ERROR, logger="semantic_memory"):
        ok = sem.add_fact(_TEST_FACT)
    assert ok is False
    assert any(
        r.levelno >= logging.ERROR and "Failed to add fact" in r.getMessage()
        for r in caplog.records
    ), "add_fact 失败未产生 error 级日志（谎报家族）"


# ── 4. remember_facts 不得假成功：写入失败必须反馈给 LLM ─────────────


def test_remember_facts_reports_failure_when_add_fact_returns_false():
    """SemanticMemory.add_fact 失败返回 False（bool 契约），工具不得当成功。"""
    from tools.builtin.profile_agent_tools import RememberFactsTool

    class _FalseSM:
        @staticmethod
        def add_fact(*_a, **_k):
            return False  # SemanticMemory 失败契约

    tool = RememberFactsTool()
    res = tool.execute(
        _meta={"session_key": "2:o9cq80probe@im.wechat"},
        facts=[{"fact": _TEST_FACT, "category": "preference"}],
        structured_memory=_FalseSM(),
    )
    assert res.success is False, (
        "写入失败（add_fact=False）仍返回 success=True：fid=False 被当成功条目"
    )


def test_remember_facts_success_path_unaffected():
    """健康路径不受影响：返回真 id 时照常 success=True。"""
    from tools.builtin.profile_agent_tools import RememberFactsTool

    class _OkSM:
        @staticmethod
        def add_fact(*_a, **_k):
            return 42

    tool = RememberFactsTool()
    res = tool.execute(
        _meta={"session_key": "2:o9cq80probe@im.wechat"},
        facts=[{"fact": _TEST_FACT, "category": "preference"}],
        structured_memory=_OkSM(),
    )
    assert res.success is True
    assert res.data["written"][0]["id"] == 42


# ── 5. 默认路径隔离钩子：模块级 _DB_DEFAULT 可被 conftest patch ──────


def test_structured_memory_default_path_is_patchable(monkeypatch, tmp_path):
    from shisi.memory.legacy import structured_memory as mod

    sandbox = tmp_path / "sb"
    monkeypatch.setattr(mod, "_DB_DEFAULT", str(sandbox / "sqlite.db"))
    sm = mod.StructuredMemory()  # 无参 → 必须落沙箱
    try:
        assert str(sandbox) in sm.db_path
    finally:
        sm.close()


def test_structured_memory_explicit_path_still_wins(tmp_path):
    """显式传参行为不变（向后兼容）。"""
    explicit = tmp_path / "explicit.db"
    sm = StructuredMemory(str(explicit))
    try:
        assert sm.db_path == str(explicit.resolve())
    finally:
        sm.close()
