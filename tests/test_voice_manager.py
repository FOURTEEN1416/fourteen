"""单元测试: TTSManager语音管理器"""
import sys
import asyncio
sys.path.insert(0, ".")

from voice.manager import TTSManager


def test_init_defaults():
    mgr = TTSManager()
    assert not mgr.enabled
    assert mgr.current_engine is None
    assert mgr.available_engines == []


def test_health_check_disabled():
    mgr = TTSManager()
    health = mgr.health_check()
    assert health["enabled"] is False
    assert health["current_engine"] is None
    assert health["synthesize_count"] == 0


def test_switch_engine_nonexistent():
    mgr = TTSManager()
    result = asyncio.run(mgr.switch_engine("nonexistent"))
    assert result is False


def test_synthesize_disabled():
    mgr = TTSManager()
    result = asyncio.run(mgr.synthesize("测试文本"))
    assert result is None


def test_synthesize_empty_text():
    mgr = TTSManager()
    result = asyncio.run(mgr.synthesize(""))
    assert result is None


def test_synthesize_with_emotion_param():
    mgr = TTSManager()
    result = asyncio.run(mgr.synthesize("测试", emotion="开心"))
    assert result is None


def test_get_engine_nonexistent():
    mgr = TTSManager()
    assert mgr.get_engine("edge-tts") is None


def test_available_engines_empty():
    mgr = TTSManager()
    assert mgr.available_engines == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All voice/manager tests passed!")
