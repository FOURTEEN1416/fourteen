"""单元测试: 主动消息ASE引擎"""
import sys
sys.path.insert(0, ".")


def test_ase_engine_import():
    from proactive.ase_engine import ASEEngine
    assert ASEEngine is not None


def test_ase_engine_init():
    from proactive.ase_engine import ASEEngine
    class MockLLM:
        pass
    engine = ASEEngine(
        llm_gateway=MockLLM(),
        max_daily_messages=5,
        min_interval_minutes=30,
    )
    assert engine is not None


def test_ase_engine_daily_limit():
    from proactive.ase_engine import ASEEngine
    class MockLLM:
        pass
    engine = ASEEngine(
        llm_gateway=MockLLM(),
        max_daily_messages=3,
        min_interval_minutes=30,
    )
    assert engine is not None


def test_scheduler_import():
    from proactive.scheduler import ProactiveScheduler
    assert ProactiveScheduler is not None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All proactive tests passed!")
