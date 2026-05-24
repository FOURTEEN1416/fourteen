"""单元测试: RAG引擎"""
import sys
sys.path.insert(0, ".")


def test_rag_engine_import():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert RAGEngineV2 is not None


def test_rag_engine_class_exists():
    from rag_engine.rag_engine_v2 import RAGEngineV2
    assert hasattr(RAGEngineV2, "__init__")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All rag_engine tests passed!")
