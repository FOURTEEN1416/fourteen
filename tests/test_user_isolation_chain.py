"""用户隔离全链路回归（2026-09-21 生产串台修复）。

钉住：
1. user_key = 完整会话键（禁止剥 owner）
2. owner 会话历史不得并入裸 peer 遗留
3. retrieve_context 各层按 session/user_key 过滤
4. 跨会话尾巴 / 反思 / pending_events 会话隔离
5. 同 peer 不同 owner 的 facts 互不可见
6. 迁移：唯一 owner 的裸键可回收；多 owner 裸键保持孤儿
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

from shisi.memory.legacy.memory_pipeline import MemoryPipeline, _user_key_from_session
from shisi.memory.legacy.structured_memory import StructuredMemory


class _FakeVM:
    def __init__(self):
        self._collections: dict = {}

    def search_sync(self, query, top_k=5, filter_dict=None):
        return []

    def _search(self, coll, query, top_k=5):
        return []

    def store_chat_sync(self, *a, **k):
        return None

    def health_check(self):
        return {"ok": True}


def _sm(tmp: Path) -> StructuredMemory:
    return StructuredMemory(str(tmp / "iso.sqlite"))


def test_user_key_is_full_session_key():
    assert StructuredMemory.user_key_from_session("2:o9x@im.wechat") == "2:o9x@im.wechat"
    assert StructuredMemory.user_key_from_session("4:o9x@im.wechat") == "4:o9x@im.wechat"
    assert _user_key_from_session("1:alice@im.wechat") == "1:alice@im.wechat"
    # 同 peer 不同 owner → 不同 key
    a = StructuredMemory.user_key_from_session("1:peer@im.wechat")
    b = StructuredMemory.user_key_from_session("4:peer@im.wechat")
    assert a != b
    assert StructuredMemory.bare_peer_from_session("4:peer@im.wechat") == "peer@im.wechat"


def test_same_peer_different_owners_facts_isolated():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_fact("A 的承诺：明早叫我", category="commitment", user_key="1:peer@im.wechat")
        sm.add_fact("B 的承诺：军训六点", category="commitment", user_key="4:peer@im.wechat")
        fa = sm.get_facts(user_key="1:peer@im.wechat")
        fb = sm.get_facts(user_key="4:peer@im.wechat")
        assert any("A 的承诺" in f["fact"] for f in fa)
        assert not any("B 的承诺" in f["fact"] for f in fa)
        assert any("B 的承诺" in f["fact"] for f in fb)
        assert not any("A 的承诺" in f["fact"] for f in fb)
        # 旧剥 owner 键不得命中
        legacy = sm.get_facts(user_key="peer@im.wechat")
        assert legacy == []
    finally:
        sm.close()


def test_load_session_history_does_not_merge_bare_form():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        bare = "o9x@im.wechat"
        sm.add_chat("user", "裸历史-别人聊的", session_id=bare)
        sm.add_chat("user", "A 专属", session_id="1:o9x@im.wechat")
        sm.add_chat("user", "B 专属", session_id="4:o9x@im.wechat")
        mp = MemoryPipeline(vector_memory=_FakeVM(), structured_memory=sm, llm_gateway=None)
        ha = mp._load_session_history("1:o9x@im.wechat", limit=20)
        hb = mp._load_session_history("4:o9x@im.wechat", limit=20)
        assert any("A 专属" in m["content"] for m in ha)
        assert not any("裸历史" in m["content"] for m in ha)
        assert not any("B 专属" in m["content"] for m in ha)
        assert any("B 专属" in m["content"] for m in hb)
        assert not any("裸历史" in m["content"] for m in hb)
        assert not any("A 专属" in m["content"] for m in hb)
    finally:
        sm.close()


def test_cross_session_tail_exact_session_only():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_chat("user", "裸尾巴", session_id="peer@im.wechat")
        sm.add_chat("user", "A 尾巴", session_id="1:peer@im.wechat")
        sm.add_chat("user", "B 尾巴", session_id="4:peer@im.wechat")
        ta = sm.get_cross_session_tail("1:peer@im.wechat", limit=10)
        tb = sm.get_cross_session_tail("4:peer@im.wechat", limit=10)
        assert any("A 尾巴" in x for x in ta)
        assert not any("裸尾巴" in x for x in ta)
        assert not any("B 尾巴" in x for x in ta)
        assert any("B 尾巴" in x for x in tb)
        assert not any("A 尾巴" in x for x in tb)
    finally:
        sm.close()


def test_retrieve_context_facts_filtered_by_session():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_fact("只属于 A", user_key="1:a@im.wechat")
        sm.add_fact("只属于 B", user_key="4:a@im.wechat")
        sm.add_fact("孤儿事实", user_key="")
        mp = MemoryPipeline(vector_memory=_FakeVM(), structured_memory=sm, llm_gateway=None)
        ctx_a = mp.retrieve_context("只属于", session_id="1:a@im.wechat", top_k=10)
        facts_a = ctx_a.get("facts") or []
        assert any("只属于 A" in f for f in facts_a)
        assert not any("只属于 B" in f for f in facts_a)
        assert not any("孤儿事实" in f for f in facts_a)
        ctx_b = mp.retrieve_context("只属于", session_id="4:a@im.wechat", top_k=10)
        facts_b = ctx_b.get("facts") or []
        assert any("只属于 B" in f for f in facts_b)
        assert not any("只属于 A" in f for f in facts_b)
    finally:
        sm.close()


def test_memory_context_injects_only_own_facts_and_history():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_chat("user", "A 说了猫", session_id="1:x@im.wechat")
        sm.add_chat("user", "B 说了狗", session_id="2:x@im.wechat")
        sm.add_chat("user", "裸历史说鸟", session_id="x@im.wechat")
        sm.add_fact("A 喜欢猫", user_key="1:x@im.wechat")
        sm.add_fact("B 喜欢狗", user_key="2:x@im.wechat")
        mp = MemoryPipeline(vector_memory=_FakeVM(), structured_memory=sm, llm_gateway=None)
        ctx = mp.get_memory_context(session_id="1:x@im.wechat", affinity_level=4)
        contents = [c.get("content", "") for c in ctx["recent_chats"]]
        assert any("A 说了猫" in c for c in contents)
        assert not any("B 说了狗" in c for c in contents)
        assert not any("裸历史" in c for c in contents)
        assert "A 喜欢猫" in ctx["user_facts"]
        assert "B 喜欢狗" not in ctx["user_facts"]
        text = mp.get_formatted_context(session_id="1:x@im.wechat", affinity_level=4)
        assert "A 喜欢猫" in text
        assert "B 喜欢狗" not in text
    finally:
        sm.close()


def test_reflections_and_pending_events_session_scoped():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_reflection("A 的观察", session_id="1:r@im.wechat")
        sm.add_reflection("B 的观察", session_id="2:r@im.wechat")
        ra = sm.get_reflections(session_id="1:r@im.wechat")
        rb = sm.get_reflections(session_id="2:r@im.wechat")
        assert any("A 的观察" in r["content"] for r in ra)
        assert not any("B 的观察" in r["content"] for r in ra)
        assert any("B 的观察" in r["content"] for r in rb)
        assert not any("A 的观察" in r["content"] for r in rb)

        conn_cm = sm.get_connection()
        with conn_cm as conn:
            conn.execute(
                "INSERT INTO pending_events (event_desc, source_session_id, is_resolved) "
                "VALUES ('A 待办', '1:r@im.wechat', 0)"
            )
            conn.execute(
                "INSERT INTO pending_events (event_desc, source_session_id, is_resolved) "
                "VALUES ('B 待办', '2:r@im.wechat', 0)"
            )
            conn.commit()
        from shisi.memory.legacy.cross_session_reasoner import CrossSessionReasoner

        csr = CrossSessionReasoner(sm)
        ea = csr.get_pending_events(session_id="1:r@im.wechat")
        eb = csr.get_pending_events(session_id="2:r@im.wechat")
        assert any("A 待办" in e.get("event_desc", "") for e in ea)
        assert not any("B 待办" in e.get("event_desc", "") for e in ea)
        assert any("B 待办" in e.get("event_desc", "") for e in eb)
        assert not any("A 待办" in e.get("event_desc", "") for e in eb)
    finally:
        sm.close()


def test_migrate_legacy_isolation_keys_unique_owner():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        bare = "uniq_peer@im.wechat"
        sm.add_fact("唯一归属事实", user_key=bare)
        sm.add_chat("user", "唯一归属聊天", session_id=bare)
        sm.add_chat("user", "owner 消息", session_id=f"7:{bare}")
        stats = sm.migrate_legacy_isolation_keys()
        assert stats["facts_migrated"] >= 1
        assert sm.get_facts(user_key=f"7:{bare}")
        assert sm.get_facts(user_key=bare) == []
        hist = sm.get_chats_by_session(f"7:{bare}")
        assert any("唯一归属聊天" in (h.get("content") or "") for h in hist)
    finally:
        sm.close()


def test_migrate_legacy_isolation_keys_multi_owner_stays_orphan():
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        bare = "shared_peer@im.wechat"
        sm.add_fact("共享 peer 事实", user_key=bare)
        sm.add_chat("user", "u1", session_id=f"1:{bare}")
        sm.add_chat("user", "u4", session_id=f"4:{bare}")
        stats = sm.migrate_legacy_isolation_keys()
        assert stats["facts_orphaned"] >= 1
        # 不迁给任一 owner
        assert sm.get_facts(user_key=f"1:{bare}") == []
        assert sm.get_facts(user_key=f"4:{bare}") == []
        assert any(
            f["fact"] == "共享 peer 事实" and f["user_key"] == bare
            for f in sm.get_facts(user_key=bare)
        )
    finally:
        sm.close()


def test_retrieve_context_source_never_global_fact_search():
    """静态防护：有 session 时 semantic.search 必须带 user_key。"""
    src = inspect.getsource(MemoryPipeline.retrieve_context)
    assert "user_key=uk" in src or "user_key=uk if session_id" in src
    assert "session_id=session_id" in src or "session_id or None" in src
    # 禁止旧写法：无过滤的 working.get_recent 作为唯一来源在有 session 时
    assert "self.episodic.search(query, top_k=top_k)" not in src


def test_load_session_history_source_has_no_bare_merge():
    src = inspect.getsource(MemoryPipeline._load_session_history)
    assert "legacy.group" not in src and "forms.append(legacy" not in src


def test_semantic_search_requires_user_key_when_provided():
    from shisi.memory.legacy.semantic_memory import SemanticMemory

    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        sm.add_fact("A fact", user_key="1:s@im.wechat")
        sm.add_fact("B fact", user_key="2:s@im.wechat")
        sem = SemanticMemory(_FakeVM(), sm)
        ra = sem.search("fact", user_key="1:s@im.wechat")
        texts = [x.get("fact", "") for x in ra["structured"]]
        assert "A fact" in texts
        assert "B fact" not in texts
    finally:
        sm.close()
