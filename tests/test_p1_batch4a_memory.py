"""P1 批4a 回归 —— 审查报告 2026-09-21 memory items 12-17。

- P1-12：semantic_memory 事实向量通道真执行 + 写侧带 user_key
- P1-13：冲突检测按 user_key 隔离（禁止跨用户判冲突）
- P1-14：pending_intents UPDATE 分支必须 commit（否则跨连接丢更新）
- P1-15：web 流式路径 `_prepare_context` 透传 user_id（工具 _meta 归属）
- P1-16：memory_service 壳转发 history_already_written / write_chat_history_sync
- P1-17：legacy 包入口唯一指向现役实现，importance_scorer 旧副本已删；
  pipeline 上下文缓存有界 LRU
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from contextlib import closing
from pathlib import Path

import shisi.memory.legacy as legacy
import shisi.memory.legacy.importance_scorer as isc
from shisi.application.memory_service import ShisiMemoryService
from shisi.memory.legacy.conflict_detector import ConflictDetector
from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.semantic_memory import SemanticMemory
from shisi.memory.legacy.structured_memory import StructuredMemory

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── P1-12 ─────────────────────────────────────────────────────────


class _RecordingSM:
    def __init__(self):
        self.calls: list = []

    def add_fact(self, fact, category, confidence, source, **kw):
        self.calls.append((fact, kw.get("user_key", "")))
        return len(self.calls)


class _AsyncVM:
    """真异步 store_fact（生产形态）：协程不被 await 则记录恒空。"""

    def __init__(self):
        self.calls: list = []

    async def store_fact(self, fact, category="general", confidence=0.5, user_key=""):
        self.calls.append({"fact": fact, "user_key": user_key})


class _LegacyVM:
    """旧签名 store_fact 无 user_key，不得因 TypeError 静默丢写。"""

    def __init__(self):
        self.calls: list = []

    async def store_fact(self, fact, category="general", confidence=0.5):
        self.calls.append(fact)


def test_add_fact_actually_awaits_vector_store_with_user_key():
    vm, sm = _AsyncVM(), _RecordingSM()
    sem = SemanticMemory(vm, sm)
    assert sem.add_fact("我在准备考研", category="general", user_key="4:peer")
    assert vm.calls == [{"fact": "我在准备考研", "user_key": "4:peer"}], (
        "P1-12：旧实现丢弃协程 → 向量通道恒空"
    )


def test_add_fact_tolerates_legacy_store_signature():
    vm, sm = _LegacyVM(), _RecordingSM()
    sem = SemanticMemory(vm, sm)
    assert sem.add_fact("喜欢吃辣", category="general", user_key="7:peer")
    assert vm.calls == ["喜欢吃辣"]


def test_vector_store_fact_signature_carries_user_key():
    from shisi.memory.legacy.vector_memory import VectorMemory

    for fn in (VectorMemory.store_fact, VectorMemory.store_fact_sync):
        assert "user_key" in inspect.signature(fn).parameters


# ── P1-13 ─────────────────────────────────────────────────────────


class _RecordingSem:
    def __init__(self):
        self.kwargs: list = []

    def search(self, query, top_k=5, **kw):
        self.kwargs.append({"query": query, "top_k": top_k, **kw})
        return {"vector": []}


def test_conflict_detector_passes_user_key_to_search():
    sem = _RecordingSem()
    cd = ConflictDetector(sem)
    assert cd.check_conflict("新事实", "general", user_key="4:peer") is None
    assert sem.kwargs[0]["user_key"] == "4:peer", "P1-13：检索必须按本人隔离"


def test_conflict_detector_signature_has_user_key_param():
    assert "user_key" in inspect.signature(ConflictDetector.check_conflict).parameters


# ── P1-14 ─────────────────────────────────────────────────────────


def test_pending_intent_update_persists_across_connections(tmp_path):
    db = str(tmp_path / "sqlite.db")
    sm = StructuredMemory(db_path=db)
    sm.upsert_pending_intent("4:peer", "set_reminder", {"content": "叫醒"}, 1)
    sm.upsert_pending_intent("4:peer", "set_reminder", {"content": "叫醒"}, 2)
    # 新连接读取：UPDATE 分支漏 commit 时这里只会看到 ask_count=1
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT ask_count, slots_json FROM pending_intents "
            "WHERE session_key='4:peer' AND status='active'"
        ).fetchone()
    assert row is not None and row[0] == 2, "P1-14：UPDATE 必须随 conn.commit() 落库"


# ── P1-15 ─────────────────────────────────────────────────────────


def _stream_prepare_call() -> ast.Call:
    src = (REPO_ROOT / "orchestrator" / "_stream_mixin.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_prepare_context"
        ):
            return node
    raise AssertionError("流式路径未找到 _prepare_context 调用（结构性变更需同步本用例）")


def test_stream_path_forwards_user_id_to_prepare_context():
    call = _stream_prepare_call()
    kw = {k.arg for k in call.keywords}
    assert "user_id" in kw, "P1-15：web 流式缺 user_id → 工具 _meta 归属为 None"
    assert "emotion_engine" in kw


# ── P1-16 ─────────────────────────────────────────────────────────


class _RecordingPipeline:
    def __init__(self):
        self.calls: list = []

    def after_chat(self, **kw):
        self.calls.append(("after_chat", kw))
        return {}

    def write_chat_history_sync(self, **kw):
        self.calls.append(("sync", kw))
        return True


def _bare_service() -> ShisiMemoryService:
    svc = ShisiMemoryService.__new__(ShisiMemoryService)
    svc._pipeline = _RecordingPipeline()
    return svc


def test_service_forwards_history_already_written():
    svc = _bare_service()
    svc.after_chat("u", "r", emotion_tag="happy", session_id="4:p",
                   history_already_written=True)
    _name, kw = svc._pipeline.calls[0]
    assert kw["history_already_written"] is True, "P1-16：壳层不得吞掉 B-a 同步轻写标志"
    # pipeline 本体必须真有该参数（否则转发是假的）
    assert "history_already_written" in inspect.signature(
        MemoryPipeline.after_chat
    ).parameters


def test_service_exposes_write_chat_history_sync():
    svc = _bare_service()
    assert svc.write_chat_history_sync("u", "r", session_id="4:p") is True
    assert svc._pipeline.calls[0][0] == "sync"


# ── P1-17 ─────────────────────────────────────────────────────────


def test_legacy_exports_point_to_live_modules():
    from shisi.memory.legacy.conflict_detector import ConflictDetector as LiveCD
    from shisi.memory.legacy.forgetting_manager import ForgettingManager as LiveFM

    assert legacy.ConflictDetector is LiveCD
    assert legacy.ForgettingManager is LiveFM
    # 2026-09-22：CrossSessionReasoner（pending_events 死链）已拆除出库，
    # 反向钉住防复活（import 与 __all__ 双查）。
    assert not hasattr(legacy, "CrossSessionReasoner")
    assert "CrossSessionReasoner" not in legacy.__all__


def test_importance_scorer_old_copies_removed():
    for name in ("ForgettingManager", "ConflictDetector", "CrossSessionReasoner"):
        assert not hasattr(isc, name), f"无隔离旧副本 {name} 应已删除（见 DELETION_LOG）"
    assert hasattr(isc, "ImportanceScorer")


def test_pipeline_dead_async_context_cache_removed():
    """批6b 项8：retrieve_context_async 生产零调用，连同其 _context_cache
    （曾为无界 dict，P1-17 收敛为 LRU 后仍无人消费）一并删除；反向钉住防复活。"""
    assert not hasattr(MemoryPipeline, "retrieve_context_async")
    body = inspect.getsource(MemoryPipeline)
    assert "_context_cache" not in body, "死检索路径的缓存面不得复活（生产只走同步 retrieve_context）"
    init = inspect.getsource(MemoryPipeline.__init__)
    assert "_cache_lock" not in init
