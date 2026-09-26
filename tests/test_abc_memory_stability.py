"""包 Q · B 记忆同步写 / near-dup / k(level) 注入测试。"""

from __future__ import annotations

import inspect
import math
import tempfile
from pathlib import Path

from shisi.affinity.scale import points_to_level
from shisi.memory.legacy.fact_extractor import FactExtractor
from shisi.memory.legacy.structured_memory import StructuredMemory


class TestNearDupMergeB:
    def test_near_dup_updates_not_double_insert(self):
        sm = StructuredMemory(db_path=str(Path(tempfile.mkdtemp()) / "t.sqlite"))
        id1 = sm.add_fact("用户喜欢吃火锅", category="preference", confidence=0.6, user_key="u1")
        id2 = sm.add_fact("用户喜欢吃火锅", category="preference", confidence=0.9, user_key="u1")
        assert id1 == id2
        facts = sm.get_facts(user_key="u1")
        assert len(facts) == 1
        assert facts[0]["confidence"] >= 0.9
        assert facts[0]["access_count"] >= 1

    def test_near_dup_bigram_similar(self):
        sm = StructuredMemory(db_path=str(Path(tempfile.mkdtemp()) / "t.sqlite"))
        id1 = sm.add_fact("我每天十二点睡觉", user_key="u2")
        id2 = sm.add_fact("我每天12点睡觉", user_key="u2")
        facts = sm.get_facts(user_key="u2")
        assert len(facts) == 1
        assert id1 == id2

    def test_commitment_upgrades_category(self):
        sm = StructuredMemory(db_path=str(Path(tempfile.mkdtemp()) / "t.sqlite"))
        sm.add_fact("明早六点叫我起床", category="general", user_key="u3")
        sm.add_fact("明早六点叫我起床", category="commitment", user_key="u3")
        facts = sm.get_facts(user_key="u3")
        assert len(facts) == 1
        assert facts[0]["category"] == "commitment"

    def test_different_user_key_not_merged(self):
        sm = StructuredMemory(db_path=str(Path(tempfile.mkdtemp()) / "t.sqlite"))
        sm.add_fact("用户喜欢吃火锅", user_key="ua")
        sm.add_fact("用户喜欢吃火锅", user_key="ub")
        assert len(sm.get_facts(user_key="ua")) == 1
        assert len(sm.get_facts(user_key="ub")) == 1

    def test_topics_stored_and_merged(self):
        sm = StructuredMemory(db_path=str(Path(tempfile.mkdtemp()) / "t.sqlite"))
        sm.add_fact("用户喜欢火锅", topics=["美食"], user_key="u4")
        sm.add_fact("用户喜欢火锅", topics=["重庆"], user_key="u4")
        facts = sm.get_facts(user_key="u4")
        assert len(facts) == 1
        topics = str(facts[0].get("topics") or "")
        assert "美食" in topics
        assert "重庆" in topics

    def test_extractor_emits_topics_and_relationship(self):
        fe = FactExtractor(llm_func=None)
        facts = fe.extract_facts(["提醒我明早六点叫我起床"])
        cats = {f["category"] for f in facts}
        assert "commitment" in cats
        assert any(f.get("topics") for f in facts)


class TestKLevelInjectionC:
    def test_k_formula(self):
        # k = min(4 + ceil(level/2), 10)
        assert min(4 + math.ceil(0 / 2), 10) == 4
        assert min(4 + math.ceil(3 / 2), 10) == 6
        assert min(4 + math.ceil(8 / 2), 10) == 8
        assert min(4 + math.ceil(20 / 2), 10) == 10
        assert points_to_level(500) == 8
        assert points_to_level(0) == 0

    def test_get_memory_context_respects_k_and_titles(self):
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        tmp = Path(tempfile.mkdtemp())
        sm = StructuredMemory(db_path=str(tmp / "sm.sqlite"))
        # 插入 12 条事实
        for i in range(12):
            sm.add_fact(f"事实编号{i}用户相关细节", category="preference", confidence=0.5, user_key="")
        class _DS:
            def get_all_summaries(self):
                return {}
            def detect_mood_trend(self, s):
                return {}
            def load_summaries_from_db(self):
                return None
        class _W:
            def add(self, *a, **k):
                pass
        # 仅验证 get_memory_context 的 k 选择逻辑：直接调用方法绑定
        pipe = object.__new__(MemoryPipeline)
        pipe.sm = sm
        pipe.ds = _DS()
        pipe.working = _W()
        pipe._session_id = "N:test_k"
        # user_key 派生：完整会话键（2026-09-21 隔离修复，禁止剥 owner）
        from shisi.memory.legacy.memory_pipeline import _user_key_from_session
        uk = _user_key_from_session("N:test_k")
        assert uk == "N:test_k"
        for i in range(12):
            sm.add_fact(f"关于用户的事{i}", category="preference", user_key=uk)
        ctx0 = pipe.get_memory_context(5, affinity_level=0)
        ctx8 = pipe.get_memory_context(5, affinity_level=8)
        assert ctx0["injection_k"] == 4
        assert ctx8["injection_k"] == 8
        assert len(ctx0["user_facts"]) <= 4
        assert len(ctx8["user_facts"]) <= 8
        assert len(ctx8["user_facts"]) >= len(ctx0["user_facts"])

        text = pipe.get_formatted_context(5, affinity_level=2)
        assert "# 关于用户" in text

    def test_relationship_titles_when_present(self):
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        tmp = Path(tempfile.mkdtemp())
        sm = StructuredMemory(db_path=str(tmp / "sm2.sqlite"))
        uk = "rel_user"
        sm.add_fact("你答应过陪我看电影", category="commitment", user_key=uk, topics=["电影","约定"])
        sm.add_fact("我们第一次见面是在书店", category="relationship", user_key=uk)

        class _DS:
            def get_all_summaries(self):
                return {}
            def detect_mood_trend(self, s):
                return {}
        class _W:
            def add(self, *a, **k):
                pass
        pipe = object.__new__(MemoryPipeline)
        pipe.sm = sm
        pipe.ds = _DS()
        pipe.working = _W()
        # monkeypatch user key
        import shisi.memory.legacy.memory_pipeline as mod
        old = mod._user_key_from_session
        mod._user_key_from_session = lambda s: uk
        try:
            pipe._session_id = "N:rel"
            text = pipe.get_formatted_context(5, affinity_level=4)
        finally:
            mod._user_key_from_session = old
        assert "# 关于用户" in text
        assert "# 我们之间" in text
        assert "# 最近话题" in text
        assert "电影" in text or "约定" in text


class TestSyncHistoryBa:
    def test_write_chat_history_sync_and_skip_in_after_chat(self):
        from shisi.memory.legacy.memory_pipeline import MemoryPipeline

        tmp = Path(tempfile.mkdtemp())
        sm = StructuredMemory(db_path=str(tmp / "sm3.sqlite"))

        class _DS:
            def get_all_summaries(self):
                return {}
            def detect_mood_trend(self, s):
                return {}
            def save_summary(self, *a, **k):
                pass
        class _W:
            def __init__(self):
                self.items = []
            def add(self, *a, **k):
                self.items.append(a)
            def should_archive(self, n=20, session_id="", character_id=""):
                return False
        class _VM:
            def store_chat_sync(self, *a, **k):
                pass
            def store_emotion_log_sync(self, *a, **k):
                pass
        pipe = object.__new__(MemoryPipeline)
        pipe.sm = sm
        pipe.ds = _DS()
        pipe.working = _W()
        pipe.vm = _VM()
        pipe._session_id = "N:ba"
        pipe._chat_count_lock = __import__("threading").Lock()
        pipe._chat_count_since_extract = {}
        pipe._config = type("C", (), {"fact_extract_interval": 999, "episodic_archive_trigger": 999, "extraction_enabled": False})()
        pipe._executor = __import__("concurrent").futures.ThreadPoolExecutor(max_workers=1)
        pipe.scorer = type("S", (), {"score": staticmethod(lambda *a, **k: 0.5)})()
        pipe.fe = FactExtractor()
        pipe._forgetting_model = "exponential"
        pipe.conflict_detector = type("C", (), {"check_conflict": staticmethod(lambda *a, **k: None)})()
        pipe.semantic = type("Sem", (), {"add_fact": staticmethod(lambda *a, **k: True), "get_facts": staticmethod(lambda **k: [])})()
        pipe.episodic = type("E", (), {"search": staticmethod(lambda *a, **k: [])})()
        pipe.reflection = type("R", (), {"maybe_reflect": staticmethod(lambda *a, **k: None)})()

        ok = pipe.write_chat_history_sync("你好呀", "在的", emotion_tag="开心", session_id="N:ba")
        assert ok
        roles = [(c["role"], c["content"]) for c in sm.get_recent_chats(10)]
        assert ("user", "你好呀") in roles
        assert ("assistant", "在的") in roles
        n_before = len(sm.get_recent_chats(10))

        # history_already_written=True 时 after_chat 不得双插
        pipe.after_chat("你好呀", "在的", emotion_tag="开心", session_id="N:ba", history_already_written=True)
        n_after = len(sm.get_recent_chats(10))
        assert n_after == n_before

    def test_orchestrator_calls_sync_history_before_bg(self):
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator
        src = inspect.getsource(OptimizedOrchestrator._after_process)
        assert "write_chat_history_sync" in src
        assert src.index("write_chat_history_sync") < src.index("submit(copy_context().run, _safe_after_chat")
        assert "history_already_written" in src
