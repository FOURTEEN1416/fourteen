"""单元测试: 记忆管线 — 深度版"""
import sys

sys.path.insert(0, ".")


# ═══════════════════════════════════════════════════════════════
#  MemoryPipeline 核心方法存在性验证
# ═══════════════════════════════════════════════════════════════

def test_memory_pipeline_import():
    from memory.memory_pipeline import MemoryPipeline
    assert MemoryPipeline is not None


def test_memory_pipeline_has_after_chat():
    from memory.memory_pipeline import MemoryPipeline
    assert hasattr(MemoryPipeline, "after_chat")
    assert callable(MemoryPipeline.after_chat)


def test_memory_pipeline_has_retrieve_context():
    from memory.memory_pipeline import MemoryPipeline
    assert hasattr(MemoryPipeline, "retrieve_context")
    assert callable(MemoryPipeline.retrieve_context)


def test_memory_pipeline_has_start_session_on_working():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline()
    assert hasattr(mp.working, "start_session")


def test_memory_pipeline_has_health_check():
    from memory.memory_pipeline import MemoryPipeline
    assert hasattr(MemoryPipeline, "health_check")
    assert callable(MemoryPipeline.health_check)


def test_memory_pipeline_has_session_id_property():
    from memory.memory_pipeline import MemoryPipeline
    assert isinstance(getattr(MemoryPipeline, "session_id", None), property)


def test_memory_pipeline_init_default():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline()
    assert mp is not None
    assert hasattr(mp, "working")
    assert hasattr(mp, "episodic")
    assert hasattr(mp, "semantic")
    assert hasattr(mp, "scorer")
    assert hasattr(mp, "forgetting")
    assert hasattr(mp, "conflict_detector")
    assert hasattr(mp, "cross_session")
    assert hasattr(mp, "summarizer")


def test_memory_pipeline_init_custom_params():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline(
        working_limit=50,
        retrieval_timeout=2.0,
        forgetting_model="threshold",
        lambda_low=0.2,
        lambda_high=0.02,
    )
    assert mp._config.working_limit == 50
    assert mp._config.retrieval_timeout == 2.0
    assert mp._forgetting_model == "threshold"


def test_memory_pipeline_forgetting_model_invalid_fallback():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline(forgetting_model="invalid_model")
    assert mp._forgetting_model == "exponential"


def test_memory_pipeline_after_chat_result_keys():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline()
    result = mp.after_chat("你好", "你好呀", emotion_tag="开心")
    expected_keys = {"stored_chat", "stored_vector", "facts_extracted",
                     "conflicts_detected", "archived", "emotion_updated"}
    assert set(result.keys()) == expected_keys


def test_memory_pipeline_retrieve_context_keys():
    from memory.memory_pipeline import MemoryPipeline
    mp = MemoryPipeline()
    ctx = mp.retrieve_context("你好")
    expected_keys = {"working", "episodic", "semantic", "facts", "pending_events"}
    assert set(ctx.keys()) == expected_keys


# ═══════════════════════════════════════════════════════════════
#  WorkingMemory 验证
# ═══════════════════════════════════════════════════════════════

def test_working_memory_add_and_get_recent():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    wm.add("user", "hello", "happy", 0.5)
    wm.add("assistant", "hi", "happy", 0.5)
    recent = wm.get_recent(2)
    assert len(recent) == 2
    assert recent[0]["role"] == "user"
    assert recent[1]["role"] == "assistant"


def test_working_memory_get_recent_limit():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    for i in range(10):
        wm.add("user", f"msg_{i}")
    recent = wm.get_recent(3)
    assert len(recent) == 3
    assert recent[0]["content"] == "msg_7"


def test_working_memory_should_archive():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    for i in range(20):
        wm.add("user", f"msg_{i}")
    assert wm.should_archive(trigger_count=20) is True
    assert wm.should_archive(trigger_count=25) is False


def test_working_memory_clear():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    wm.add("user", "hello")
    wm.clear()
    assert wm.count() == 0


def test_working_memory_start_session():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    wm.add("user", "hello")
    wm.start_session(session_id="test_session")
    assert wm.count() == 0
    assert wm.session_id == "test_session"


def test_working_memory_count():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    assert wm.count() == 0
    wm.add("user", "a")
    wm.add("assistant", "b")
    assert wm.count() == 2


def test_working_memory_deque_maxlen():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=5)
    for i in range(10):
        wm.add("user", f"msg_{i}")
    assert wm.count() == 5


def test_working_memory_session_id_auto():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory()
    sid = wm.session_id
    assert sid.startswith("session_")


def test_working_memory_message_structure():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    wm.add("user", "hello", "happy", 0.8)
    msg = wm.get_recent(1)[0]
    assert "role" in msg
    assert "content" in msg
    assert "emotion" in msg
    assert "importance" in msg
    assert "timestamp" in msg
    assert msg["role"] == "user"
    assert msg["content"] == "hello"
    assert msg["emotion"] == "happy"
    assert msg["importance"] == 0.8


def test_working_memory_get_for_archive():
    from memory.memory_pipeline import WorkingMemory
    wm = WorkingMemory(limit=100)
    wm.add("user", "a")
    wm.add("assistant", "b")
    archive = wm.get_for_archive()
    assert len(archive) == 2


# ═══════════════════════════════════════════════════════════════
#  MemoryConfig 数据结构验证
# ═══════════════════════════════════════════════════════════════

def test_memory_config_defaults():
    from memory.memory_pipeline import MemoryConfig
    cfg = MemoryConfig()
    assert cfg.working_limit == 20
    assert cfg.episodic_archive_trigger == 20
    assert cfg.retrieval_timeout == 1.0
    assert cfg.importance_threshold == 0.3
    assert cfg.forgetting_days == 30
    assert cfg.cache_size == 100
    assert cfg.fact_extract_interval == 5
    assert cfg.fact_min_confidence == 0.2
    assert cfg.conflict_similarity_threshold == 0.3


# ═══════════════════════════════════════════════════════════════
#  ImportanceScorer 验证
# ═══════════════════════════════════════════════════════════════

def test_importance_scorer_basic():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    score = scorer.score("我喜欢你", "")
    assert score > 0.3
    assert score <= 1.0


def test_importance_scorer_keyword_boost():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    s1 = scorer.score("普通消息", "")
    s2 = scorer.score("记住这个生日", "")
    assert s2 > s1


def test_importance_scorer_emotion_weight():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    s1 = scorer.score("消息", "开心")
    s2 = scorer.score("消息", "生气")
    assert s2 > s1


def test_importance_scorer_should_retain_high_importance():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    assert scorer.should_retain(0.9, 100) is True


def test_importance_scorer_should_retain_old_low():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    assert scorer.should_retain(0.2, 60) is False


def test_importance_scorer_should_retain_fresh():
    from memory.memory_pipeline import ImportanceScorer
    scorer = ImportanceScorer()
    assert scorer.should_retain(0.5, 0) is True


# ═══════════════════════════════════════════════════════════════
#  ForgettingManager 验证
# ═══════════════════════════════════════════════════════════════

def test_forgetting_manager_retrieval_weight_fresh():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    w = fm.retrieval_weight(0.5, 0)
    assert abs(w - 0.5) < 1e-6


def test_forgetting_manager_retrieval_weight_decay():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    w1 = fm.retrieval_weight(0.5, 1)
    w2 = fm.retrieval_weight(0.5, 10)
    assert w1 > w2


def test_forgetting_manager_high_importance_slow_decay():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    w = fm.retrieval_weight(0.8, 30)
    assert w > 0.01


def test_forgetting_manager_should_delete():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    assert fm.should_delete(0.1, 100) is True
    assert fm.should_delete(0.9, 0) is False


def test_forgetting_manager_weight_bounded():
    from memory.memory_pipeline import ForgettingManager
    fm = ForgettingManager()
    for imp in [0.0, 0.5, 1.0]:
        for days in [0, 10, 100, 1000]:
            w = fm.retrieval_weight(imp, days)
            assert 0.0 <= w <= 1.0


# ═══════════════════════════════════════════════════════════════
#  ConversationSummarizer 方法验证
# ═══════════════════════════════════════════════════════════════

def test_conversation_summarizer_import():
    from memory.conversation_summarizer import ConversationSummarizer
    assert ConversationSummarizer is not None


def test_conversation_summarizer_has_get_chat_context():
    from memory.conversation_summarizer import ConversationSummarizer
    assert hasattr(ConversationSummarizer, "get_chat_context")


def test_conversation_summarizer_has_clear_cache():
    from memory.conversation_summarizer import ConversationSummarizer
    assert hasattr(ConversationSummarizer, "clear_cache")


def test_conversation_summarizer_empty_messages():
    from memory.conversation_summarizer import ConversationSummarizer
    cs = ConversationSummarizer(llm_gateway=None)
    history, summary = cs.get_chat_context([])
    assert history == []
    assert summary == ""


def test_conversation_summarizer_few_messages():
    from memory.conversation_summarizer import ConversationSummarizer
    cs = ConversationSummarizer(llm_gateway=None)
    msgs = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]
    history, summary = cs.get_chat_context(msgs)
    assert len(history) == 2
    assert summary == ""


def test_conversation_summarizer_init_attributes():
    from memory.conversation_summarizer import ConversationSummarizer
    cs = ConversationSummarizer(llm_gateway=None)
    assert hasattr(cs, "_cache")
    assert hasattr(cs, "_cache_boundary")
    assert isinstance(cs._cache, dict)
    assert isinstance(cs._cache_boundary, dict)


def test_conversation_summarizer_clear_cache_all():
    from memory.conversation_summarizer import ConversationSummarizer
    cs = ConversationSummarizer(llm_gateway=None)
    cs._cache["test:1"] = "summary"
    cs.clear_cache()
    assert len(cs._cache) == 0


def test_conversation_summarizer_clear_cache_by_session():
    from memory.conversation_summarizer import ConversationSummarizer
    cs = ConversationSummarizer(llm_gateway=None)
    cs._cache["sess1:1"] = "a"
    cs._cache["sess2:1"] = "b"
    cs.clear_cache(session_id="sess1")
    assert "sess1:1" not in cs._cache
    assert "sess2:1" in cs._cache


# ═══════════════════════════════════════════════════════════════
#  SemanticMemory 验证
# ═══════════════════════════════════════════════════════════════

def test_semantic_memory_extract_facts():
    from memory.memory_pipeline import SemanticMemory
    sm = SemanticMemory(None, None)
    facts = sm.extract_facts_from_message("我喜欢猫")
    assert len(facts) >= 1
    assert facts[0]["category"] == "preference"


def test_semantic_memory_extract_facts_identity():
    from memory.memory_pipeline import SemanticMemory
    sm = SemanticMemory(None, None)
    facts = sm.extract_facts_from_message("我是学生")
    assert len(facts) >= 1
    assert any(f["category"] == "identity" for f in facts)


def test_semantic_memory_extract_facts_no_match():
    from memory.memory_pipeline import SemanticMemory
    sm = SemanticMemory(None, None)
    facts = sm.extract_facts_from_message("今天天气不错")
    assert facts == []


def test_semantic_memory_extract_facts_attribute():
    from memory.memory_pipeline import SemanticMemory
    sm = SemanticMemory(None, None)
    facts = sm.extract_facts_from_message("我的名字是小明")
    assert any(f["category"] == "attribute" for f in facts)


# ═══════════════════════════════════════════════════════════════
#  Orchestrator 导入验证
# ═══════════════════════════════════════════════════════════════

def test_orchestrator_import():
    from orchestrator import Orchestrator
    assert Orchestrator is not None


def test_orchestrator_has_process_message():
    from orchestrator import Orchestrator
    assert hasattr(Orchestrator, "process_message")


def test_orchestrator_init_no_deps():
    from orchestrator import Orchestrator
    o = Orchestrator()
    assert o is not None
    assert o._llm is None
    assert o._emotion is None
    assert o._memory is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All memory tests passed!")
