"""记忆管线核心路径测试 — 使用 mock 后端隔离向量/结构化存储。"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")


class FakeVectorMemory:
    """轻量级向量内存 mock，记录调用并返回可控结果。"""

    def __init__(self):
        self.chats: list[dict] = []
        self.texts: list[dict] = []
        self.emotions: list[dict] = []
        self.search_results: list[dict] = []

    def store_chat_sync(self, user_msg: str, reply: str, metadata: dict | None = None) -> None:
        self.chats.append({"user": user_msg, "reply": reply, "metadata": metadata})

    def store_text_sync(self, text: str, metadata: dict | None = None) -> None:
        self.texts.append({"text": text, "metadata": metadata})

    def store_emotion_log_sync(self, emotion: str, intensity: float, trigger: str = "") -> None:
        self.emotions.append({"emotion": emotion, "intensity": intensity, "trigger": trigger})

    def search_sync(self, query: str, top_k: int = 5, filter_dict: dict | None = None) -> list[dict]:
        return list(self.search_results[:top_k])

    def search_chats_sync(self, query: str, top_k: int = 5) -> list[dict]:
        return list(self.search_results[:top_k])

    def health_check(self) -> dict:
        return {"available": True}


class FakeConnection:
    """SQLite 连接 mock，支持创建表/插入/查询/更新。"""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._last_sql: str | None = None
        self._last_rows: list[dict] = []

    def execute(self, sql: str, parameters=()):
        self._last_sql = sql
        sql_upper = sql.strip().upper()
        # 创建表
        if sql_upper.startswith("CREATE TABLE"):
            import re
            m = re.match(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql, re.IGNORECASE)
            if m:
                table = m.group(1)
                if table not in self._tables:
                    self._tables[table] = []
            return self

        # 插入
        if sql_upper.startswith("INSERT"):
            import re
            m = re.match(
                r"INSERT(?:\s+OR\s+REPLACE)?\s+INTO\s+(\w+)\s+\(([^)]+)\)\s+VALUES\s+\(([^)]+)\)",
                sql, re.IGNORECASE,
            )
            if m:
                table = m.group(1)
                cols = [c.strip() for c in m.group(2).split(",")]
                self._tables.setdefault(table, [])
                row = dict(zip(cols, list(parameters), strict=True))
                # 自增 id
                if "id" not in row and table in ("pending_events",):
                    row["id"] = len(self._tables[table]) + 1
                # 默认 unresolved
                if table == "pending_events" and "is_resolved" not in row:
                    row["is_resolved"] = 0
                self._tables[table].append(row)
            return self

        # 查询
        if sql_upper.startswith("SELECT"):
            import re
            m = re.match(r"SELECT\s+\*\s+FROM\s+(\w+)", sql, re.IGNORECASE)
            if m:
                table = m.group(1)
                rows = list(self._tables.get(table, []))
                if "is_resolved = 0" in sql:
                    rows = [r for r in rows if not r.get("is_resolved")]
                self._last_rows = rows
            return self

        # 更新
        if sql_upper.startswith("UPDATE"):
            import re
            m = re.match(r"UPDATE\s+(\w+)\s+SET\s+(.+)\s+WHERE\s+id\s*=\s*\?", sql, re.IGNORECASE)
            if m:
                table = m.group(1)
                set_clause = m.group(2)
                target_id = parameters[-1]
                for row in self._tables.get(table, []):
                    if row.get("id") == target_id and "is_resolved = 1" in set_clause:
                        row["is_resolved"] = 1
            return self

        return self

    def fetchall(self) -> list:
        return list(self._last_rows)

    def fetchone(self):
        return self._last_rows[0] if self._last_rows else None

    def commit(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeStructuredMemory:
    """结构化内存 mock，支持事实/聊天记录的增删查。"""

    def __init__(self):
        self.facts: list[dict] = []
        self.chats: list[dict] = []
        self.conn = FakeConnection()
        self._fact_id = 0

    def add_chat(self, role: str, content: str, **kwargs) -> None:
        self.chats.append({"role": role, "content": content, **kwargs})

    def get_recent_chats(self, n: int = 10) -> list[dict]:
        return list(self.chats[-n:])

    def get_chats_today(self) -> list[dict]:
        return list(self.chats)

    def add_fact(self, fact: str, category: str = "general", confidence: float = 0.5, source: str = "") -> bool:
        self._fact_id += 1
        self.facts.append({
            "id": self._fact_id,
            "fact": fact,
            "category": category,
            "confidence": confidence,
            "source": source,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "access_count": 0,
        })
        return True

    def search_facts(self, query: str) -> list[dict]:
        return [f for f in self.facts if query.lower() in f["fact"].lower()]

    def get_facts(self, category: str | None = None, min_confidence: float = 0.0, limit: int = 1000) -> list[dict]:
        facts = [f for f in self.facts if f["confidence"] >= min_confidence]
        if category:
            facts = [f for f in facts if f.get("category") == category]
        return facts[:limit]

    def delete_fact(self, fact_id: int) -> None:
        self.facts = [f for f in self.facts if f["id"] != fact_id]

    def add_episode(self, episode_id: str, summary: str, importance: float, metadata: dict) -> None:
        self.chats.append({
            "role": "episode",
            "episode_id": episode_id,
            "summary": summary,
            "importance": importance,
            **metadata,
        })

    def get_connection(self):
        return self.conn

    def health_check(self) -> dict:
        return {"available": True}


def _make_pipeline(forgetting_model: str = "exponential", fact_extract_interval: int = 2):
    from memory.memory_pipeline import MemoryPipeline
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    mp = MemoryPipeline(
        vector_memory=vm,
        structured_memory=sm,
        llm_gateway=None,
        working_limit=20,
        retrieval_timeout=1.0,
        forgetting_model=forgetting_model,
        fact_extract_interval=fact_extract_interval,
    )
    return mp, vm, sm


def test_mp_after_chat_records_and_returns_result_keys():
    mp, vm, sm = _make_pipeline()
    result = mp.after_chat("你好", "你好呀", emotion_tag="开心")
    assert set(result.keys()) == {
        "stored_chat", "stored_vector", "facts_extracted",
        "conflicts_detected", "archived", "emotion_updated",
    }
    assert result["stored_chat"] is True
    assert result["emotion_updated"] is True
    assert len(sm.chats) == 2
    assert len(vm.chats) == 1


def test_mp_after_chat_does_not_extract_when_interval_not_met():
    mp, vm, sm = _make_pipeline(fact_extract_interval=10)
    result = mp.after_chat("我喜欢猫", "好的记住了")
    assert result["facts_extracted"] == 0
    assert mp._chat_count_since_extract == 1


def test_mp_after_chat_triggers_fact_extraction():
    mp, vm, sm = _make_pipeline(fact_extract_interval=2)
    mp.after_chat("普通消息", "收到")
    mp.after_chat("我喜欢吃火锅", "记住了")
    # 第二次达到提取阈值，会启动后台线程；等待其完成
    time.sleep(0.3)
    assert mp._chat_count_since_extract == 0
    assert len(sm.facts) >= 1


def test_mp_should_store_as_fact_rules():
    mp, *_ = _make_pipeline()
    now = datetime.now(tz=timezone.utc)
    assert mp.should_store_as_fact("我没事", now) is False
    assert mp.should_store_as_fact("我没事但有点难过", now) is True
    assert mp.should_store_as_fact("我喜欢猫", now) is True


def test_mp_is_late_night():
    from memory.memory_pipeline import MemoryPipeline
    assert MemoryPipeline._is_late_night(datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)) is True
    assert MemoryPipeline._is_late_night(datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)) is True
    assert MemoryPipeline._is_late_night(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) is False


def test_mp_retrieve_context_returns_expected_keys():
    mp, vm, sm = _make_pipeline()
    mp.after_chat("我喜欢猫", "记住了", emotion_tag="开心")
    ctx = mp.retrieve_context("猫")
    assert set(ctx.keys()) >= {"working", "episodic", "semantic", "facts", "pending_events", "reflections"}
    assert isinstance(ctx["working"], list)
    assert isinstance(ctx["facts"], list)


def test_mp_retrieve_context_uses_structured_fallback():
    mp, vm, sm = _make_pipeline()
    sm.add_fact("用户喜欢猫", "preference", 0.9)
    ctx = mp.retrieve_context("猫")
    assert "用户喜欢猫" in ctx["facts"]


async def test_mp_retrieve_context_async_caches():
    mp, vm, sm = _make_pipeline()
    sm.add_fact("用户喜欢猫", "preference", 0.9)
    ctx1 = await mp.retrieve_context_async("猫")
    ctx2 = await mp.retrieve_context_async("猫")
    assert "用户喜欢猫" in ctx1["facts"]
    assert ctx1["facts"] == ctx2["facts"]


def test_mp_get_chat_context_delegates_to_summarizer():
    mp, vm, sm = _make_pipeline()
    mp.after_chat("你好", "你好呀")
    history, summary = mp.get_chat_context()
    assert isinstance(history, list)
    assert isinstance(summary, str)


def test_mp_get_memory_context_and_formatted():
    mp, vm, sm = _make_pipeline()
    sm.add_fact("用户喜欢猫", "preference", 0.9)
    mp.after_chat("你好", "你好呀")
    ctx = mp.get_memory_context()
    assert "user_facts" in ctx
    assert "用户喜欢猫" in ctx["user_facts"]
    formatted = mp.get_formatted_context()
    assert "我记得的你" in formatted
    assert "用户喜欢猫" in formatted


def test_mp_daily_maintenance_runs_forgetting_and_summary():
    mp, vm, sm = _make_pipeline()
    mp.after_chat("今天工作很累", "抱抱你")
    summary = mp.daily_maintenance()
    # 无 LLM 时返回模板摘要或 None（如果没有今日聊天记录会被跳过）
    assert summary is None or isinstance(summary, str)


def test_mp_apply_forgetting_exponential():
    mp, vm, sm = _make_pipeline(forgetting_model="exponential")
    sm.add_fact("低重要事实", "general", 0.1)
    # 修改 updated_at 使其很旧
    sm.facts[0]["updated_at"] = "2020-01-01T00:00:00+00:00"
    forgotten = mp._apply_forgetting()
    assert forgotten >= 1


def test_mp_apply_forgetting_threshold():
    mp, vm, sm = _make_pipeline(forgetting_model="threshold")
    sm.add_fact("低重要事实", "general", 0.1)
    sm.facts[0]["updated_at"] = "2020-01-01T00:00:00+00:00"
    forgotten = mp._apply_forgetting()
    assert forgotten >= 1


def test_mp_do_fact_extraction_stores_facts():
    # 使用较大的提取间隔，避免 after_chat 自动触发后台提取
    mp, vm, sm = _make_pipeline(fact_extract_interval=100)
    mp.after_chat("我喜欢吃火锅", "记住了")
    mp.after_chat("我下周去北京出差", "好")
    count = mp._do_fact_extraction(mp.session_id)
    assert count >= 1
    assert any("吃" in f["fact"] for f in sm.facts)


def test_mp_health_check():
    mp, vm, sm = _make_pipeline()
    health = mp.health_check()
    assert "working_count" in health
    assert "vector_memory" in health
    assert "structured_memory" in health


def test_episodic_memory_store_and_search():
    from memory.memory_pipeline import EpisodicMemory
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    em = EpisodicMemory(vm, sm)
    eid = em.store_episode([
        {"role": "user", "content": "今天很开心"},
        {"role": "assistant", "content": "那太好了"},
    ], importance=0.8)
    assert eid.startswith("ep_")
    assert len(vm.texts) == 1
    assert any(c["role"] == "episode" for c in sm.chats)


def test_semantic_memory_add_fact_dedup():
    from memory.memory_pipeline import SemanticMemory
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    sem = SemanticMemory(vm, sm)
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is True
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is False
    assert len(sm.facts) == 1


def test_conflict_detector_detects_near_duplicate():
    from memory.memory_pipeline import ConflictDetector, SemanticMemory
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    sem = SemanticMemory(vm, sm)
    # 通过 vector mock 返回一个接近重复的事实
    vm.search_results = [{"content": "我喜欢小猫", "distance": 0.1}]
    cd = ConflictDetector(sem, similarity_threshold=0.3)
    conflict = cd.check_conflict("我喜欢小猫咪", "preference")
    assert conflict is not None
    assert conflict["existing_fact"] == "我喜欢小猫"


def test_cross_session_reasoner_crud():
    from memory.memory_pipeline import CrossSessionReasoner
    sm = FakeStructuredMemory()
    csr = CrossSessionReasoner(sm)
    assert csr.extract_pending_event("我明天去上海") is not None
    assert csr.extract_pending_event("我喜欢猫") is None
    csr.store_pending_event("我明天去上海", session_id="s1")
    events = csr.get_pending_events()
    assert len(events) == 1
    # resolve_event 不应抛异常
    csr.resolve_event(events[0]["id"])


def test_fact_extractor_llm_mode():
    from memory.memory_pipeline import FactExtractor
    def llm(prompt: str) -> str:
        return '[{"fact": "用户喜欢蓝色", "category": "preference", "confidence": 0.8}]'
    fe = FactExtractor(llm_func=llm)
    facts = fe.extract_facts(["我们聊聊颜色"])
    assert len(facts) == 1
    assert facts[0]["fact"] == "用户喜欢蓝色"


def test_fact_extractor_deduplicate():
    from memory.memory_pipeline import FactExtractor
    facts = [
        {"fact": "喜欢猫", "category": "preference", "confidence": 0.9},
        {"fact": "喜欢猫", "category": "preference", "confidence": 0.5},
    ]
    deduped = FactExtractor.deduplicate(facts)
    assert len(deduped) == 1
    assert deduped[0]["confidence"] == 0.9


def test_diary_summarizer_template_summary():
    from memory.memory_pipeline import DiarySummarizer
    ds = DiarySummarizer(llm_func=None)
    chats = [
        {"role": "user", "content": "今天工作"},
        {"role": "assistant", "content": "辛苦了"},
    ]
    summary = ds.summarize_day(chats)
    assert "今日共" in summary
    assert "工作" in summary


# ═══════════════════════════════════════════════════════════════
#  边界与异常路径
# ═══════════════════════════════════════════════════════════════


def test_working_memory_session_and_clear():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=5)
    wm.start_session(session_id="s1", channel="wechat", user_id="u1")
    assert wm.session_id == "s1"
    wm.add("user", "hi")
    assert wm.count() == 1
    wm.clear()
    assert wm.count() == 0


def test_episodic_memory_store_empty_messages():
    from memory.memory_pipeline import EpisodicMemory
    em = EpisodicMemory(FakeVectorMemory(), FakeStructuredMemory())
    assert em.store_episode([]) == ""


def test_episodic_memory_search_exception_returns_empty():
    from memory.memory_pipeline import EpisodicMemory
    vm = FakeVectorMemory()
    vm.search_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("vm fail"))  # type: ignore[method-assign]
    em = EpisodicMemory(vm, FakeStructuredMemory())
    assert em.search("query") == []


def test_semantic_memory_add_fact_similar_fact_dedup():
    from memory.memory_pipeline import SemanticMemory
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    sem = SemanticMemory(vm, sm)
    # 首次添加成功
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is True
    # 缓存命中
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is False
    # 通过结构化存储模拟高相似度事实
    sm.search_facts = lambda q: [{"fact": "我喜欢小猫", "similarity": 0.95}]  # type: ignore[method-assign]
    assert sem.add_fact("我喜欢小猫咪", "preference", 0.9) is False


def test_semantic_memory_search_exception_returns_empty():
    from memory.memory_pipeline import SemanticMemory
    vm = FakeVectorMemory()
    vm.search_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("vm fail"))  # type: ignore[method-assign]
    sem = SemanticMemory(vm, FakeStructuredMemory())
    result = sem.search("query")
    assert result == {"vector": [], "structured": []}


def test_semantic_memory_extract_facts_from_message():
    from memory.memory_pipeline import SemanticMemory
    sem = SemanticMemory(FakeVectorMemory(), FakeStructuredMemory())
    facts = sem.extract_facts_from_message("我喜欢吃火锅，我是程序员")
    categories = {f["category"] for f in facts}
    assert "preference" in categories
    assert "identity" in categories


def test_importance_scorer_should_retain():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    assert scorer.should_retain(0.9, 100) is True
    assert scorer.should_retain(0.3, 0, access_count=0) is True
    assert scorer.should_retain(0.1, 365, access_count=0) is False


def test_forgetting_manager_retrieval_weight_bounds():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    assert 0.0 <= fm.retrieval_weight(0.8, 0) <= 1.0
    assert fm.retrieval_weight(0.1, 1000) < 1e-10
    assert fm.should_delete(0.1, 1000) is True


def test_fact_extractor_empty_input():
    from memory.memory_pipeline import FactExtractor
    fe = FactExtractor(llm_func=None)
    assert fe.extract_facts([]) == []


def test_fact_extractor_rule_mode():
    from memory.memory_pipeline import FactExtractor
    fe = FactExtractor(llm_func=None)
    facts = fe.extract_facts(["我喜欢吃火锅", "我明天去北京出差"])
    categories = {f["category"] for f in facts}
    assert "preference" in categories
    assert "event" in categories


def test_fact_extractor_parse_json_variants():
    from memory.memory_pipeline import FactExtractor
    parse = FactExtractor._parse_json_result
    assert parse('[{"fact": "x"}]') == [{"fact": "x"}]
    assert parse('```json\n[{"fact": "x"}]\n```') == [{"fact": "x"}]
    assert parse('some prefix [{"fact": "x"}] suffix') == [{"fact": "x"}]
    assert parse('not json') is None


def test_diary_summarizer_empty_chats():
    from memory.memory_pipeline import DiarySummarizer
    ds = DiarySummarizer(llm_func=None)
    assert ds.summarize_day([]) == "今天没有聊天记录。"


def test_diary_summarizer_weekly_no_summaries():
    from memory.memory_pipeline import DiarySummarizer
    ds = DiarySummarizer(llm_func=None)
    assert ds.summarize_week([]) == "本周没有记录。"


def test_diary_summarizer_mood_trend():
    from memory.memory_pipeline import DiarySummarizer
    ds = DiarySummarizer(llm_func=None)
    trend = ds.detect_mood_trend({
        "2026-06-18": "今天很开心",
        "2026-06-19": "有点难过",
    })
    assert "trend" in trend
    assert "avg_mood" in trend


def test_memory_pipeline_unknown_forgetting_model_defaults():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline(
        vector_memory=FakeVectorMemory(),
        structured_memory=FakeStructuredMemory(),
        forgetting_model="unknown",
    )
    assert mp._forgetting_model == "exponential"


def test_memory_pipeline_should_store_as_fact_edges():
    mp, *_ = _make_pipeline()
    assert mp.should_store_as_fact("", datetime.now(tz=timezone.utc)) is False
    assert mp.should_store_as_fact("我没事", datetime.now(tz=timezone.utc)) is False
    late = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
    assert mp.should_store_as_fact("我睡不着", late) is True


def test_memory_pipeline_retrieve_context_exception_paths():
    mp, vm, sm = _make_pipeline()
    # 让 episodic 和 semantic 都抛异常，验证降级
    vm.search_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[method-assign]
    sm.search_facts = lambda q: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[method-assign]
    sm.get_facts = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))  # type: ignore[method-assign]
    ctx = mp.retrieve_context("query")
    assert ctx["episodic"] == []
    assert ctx["semantic"] == []
    assert ctx["facts"] == []


@pytest.mark.asyncio
async def test_memory_pipeline_retrieve_context_async_cache_hit():
    mp, vm, sm = _make_pipeline()
    sm.add_fact("用户喜欢猫", "preference", 0.9)
    ctx1 = await mp.retrieve_context_async("猫")
    # 第二次应命中缓存
    ctx2 = await mp.retrieve_context_async("猫")
    assert ctx1["facts"] == ctx2["facts"]


def test_memory_pipeline_daily_maintenance_no_chats():
    mp, vm, sm = _make_pipeline()
    assert mp.daily_maintenance() is None


def test_memory_pipeline_cleanup_low_confidence_facts():
    mp, vm, sm = _make_pipeline()
    sm.add_fact("低置信", "general", 0.05)
    sm.add_fact("高置信", "general", 0.9)
    mp._cleanup_low_confidence_facts()
    remaining = [f["fact"] for f in sm.facts]
    assert "低置信" not in remaining
    assert "高置信" in remaining


def test_memory_pipeline_reset_session():
    mp, vm, sm = _make_pipeline()
    original = mp.session_id
    mp.reset_session()
    assert mp.working.session_id != original


def test_semantic_memory_add_fact_structured_exception_returns_false():
    from memory.memory_pipeline import SemanticMemory
    vm = FakeVectorMemory()
    sm = FakeStructuredMemory()
    sm.add_fact = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db fail"))  # type: ignore[method-assign]
    sem = SemanticMemory(vm, sm)
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is False


def test_semantic_memory_add_fact_vector_exception_still_returns_true():
    from memory.memory_pipeline import SemanticMemory
    vm = FakeVectorMemory()
    vm.store_text_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("vector fail"))  # type: ignore[method-assign]
    sm = FakeStructuredMemory()
    sem = SemanticMemory(vm, sm)
    assert sem.add_fact("我喜欢猫", "preference", 0.9) is True
    assert len(sm.facts) == 1


def test_episodic_memory_store_episode_vector_exception_returns_empty():
    from memory.memory_pipeline import EpisodicMemory
    vm = FakeVectorMemory()
    vm.store_text_sync = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("vector fail"))  # type: ignore[method-assign]
    em = EpisodicMemory(vm, FakeStructuredMemory())
    eid = em.store_episode([
        {"role": "user", "content": "今天很开心"},
    ], importance=0.8)
    assert eid == ""


def test_mp_after_chat_late_night_emotion_boost():
    from unittest.mock import patch
    mp, vm, sm = _make_pipeline()
    with patch("memory.memory_pipeline.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        result = mp.after_chat("我睡不着，有点难过", "抱抱你", emotion_tag="难过")
    assert result["stored_chat"] is True
    assert result["emotion_updated"] is True
    assert len(sm.chats) == 2


def test_mp_get_chat_context_empty():
    mp, vm, sm = _make_pipeline()
    history, summary = mp.get_chat_context()
    assert history == []
    assert summary == ""


def test_mp_apply_forgetting_load_facts_exception_returns_zero():
    mp, vm, sm = _make_pipeline()
    sm.get_facts = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db fail"))  # type: ignore[method-assign]
    assert mp._apply_forgetting() == 0


if __name__ == "__main__":
    import asyncio
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
    print("All memory_pipeline tests passed!")
