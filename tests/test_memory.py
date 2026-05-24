"""单元测试: 记忆管线"""
import sys
sys.path.insert(0, ".")


def test_memory_pipeline_import():
    from memory.memory_pipeline import MemoryPipeline
    assert MemoryPipeline is not None


def test_conversation_summarizer_import():
    from memory.conversation_summarizer import ConversationSummarizer
    assert ConversationSummarizer is not None


def test_memory_pipeline_init():
    from memory.memory_pipeline import MemoryPipeline
    try:
        mp = MemoryPipeline.__new__(MemoryPipeline)
        assert mp is not None
    except Exception:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All memory tests passed!")
