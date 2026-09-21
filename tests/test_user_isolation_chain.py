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


def test_reflections_session_scoped():
    """反思洞察按会话隔离（pending_events 死链已拆除，原半段用例随删）。"""
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

        # pending_events 表已随死链拆除（StructructMemory 初始化即 DROP）
        with sm.get_connection() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "pending_events" not in tables
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


def test_search_facts_filter_pushdown():
    """他人事实占满检索窗口时本人事实仍可召回（2026-09-22 过滤下推 SQL：
    旧实现先全库 LIMIT 20 再 Python 过滤，多用户下静默漏检本人事实）。"""
    tmp = Path(tempfile.mkdtemp())
    sm = _sm(tmp)
    try:
        # SQL 直插 25 条 A 事实塞满 LIMIT 20 窗口（绕过 add_fact 的 near-dup
        # 合并——本用例目标是检索过滤语义，不是写入去重）。
        with sm.get_connection(write=True) as conn:
            for i in range(25):
                conn.execute(
                    "INSERT INTO user_facts (fact, category, confidence, user_key, status) "
                    "VALUES (?, 'general', 0.5, '1:a@im.wechat', 'active')",
                    (f"篮球档案第{i}期：用户A的第{i}条独立记录",),
                )
            conn.execute(
                "INSERT INTO user_facts (fact, category, confidence, user_key, status) "
                "VALUES ('B 喜欢篮球鞋收藏', 'general', 0.5, '2:b@im.wechat', 'active')"
            )
            conn.commit()
        assert len(sm.search_facts("篮球", user_key="1:a@im.wechat")) >= 20, (
            "A 的事实必须塞满窗口（>=20 行），否则本用例失去构造前提"
        )
        hits = sm.search_facts("篮球", user_key="2:b@im.wechat")
        assert any("篮球鞋" in h["fact"] for h in hits), "本人事实被他人挤出窗口"
        assert all(h.get("user_key") == "2:b@im.wechat" for h in hits)
        # 全库视角（user_key=None 管理路径）不受影响
        assert sm.search_facts("篮球")
    finally:
        sm.close()


def test_dead_tables_dropped_on_init():
    """五张死表（pending_events/affinity_log/emotion_trajectory/working_memory/
    sessions）在 StructuredMemory 初始化时幂等清除（2026-09-22 清理锁定）。"""
    import sqlite3
    import tempfile
    from pathlib import Path

    from shisi.memory.legacy.structured_memory import StructuredMemory

    dead = {
        "pending_events", "affinity_log", "emotion_trajectory",
        "working_memory", "sessions",
    }
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "legacy.db"
    conn = sqlite3.connect(str(db))
    for t in dead:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY)")  # noqa: S608
    conn.commit()
    conn.close()
    sm = StructuredMemory(db_path=str(db))
    try:
        with sm.get_connection() as conn:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert not (dead & names), f"死表未被清除: {dead & names}"
    finally:
        sm.close()
