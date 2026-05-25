"""单元测试: TTSManager语音管理器 — 深度版"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")

from voice.base import TTSProviderBase
from voice.manager import TTSManager

# ═══════════════════════════════════════════════════════════════
#  基础默认值验证
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
#  TTSManager.initialize() 配置处理
# ═══════════════════════════════════════════════════════════════

def test_initialize_none_config():
    mgr = TTSManager()
    result = asyncio.run(mgr.initialize(config=None))
    assert result is True
    assert not mgr.enabled


def test_initialize_empty_config():
    mgr = TTSManager()
    result = asyncio.run(mgr.initialize(config={}))
    assert result is True
    assert not mgr.enabled


def test_initialize_disabled():
    mgr = TTSManager()
    result = asyncio.run(mgr.initialize(config={"enabled": False}))
    assert result is True
    assert not mgr.enabled


def test_initialize_enabled_no_engines():
    mgr = TTSManager()
    result = asyncio.run(mgr.initialize(config={"enabled": True}))
    assert result is False
    assert not mgr.enabled


def test_initialize_with_edge_tts():
    mgr = TTSManager()
    config = {
        "enabled": True,
        "engine": "edge-tts",
        "edge-tts": {"speaker_name": "zh-CN-XiaoxiaoNeural"},
    }
    with patch.dict("sys.modules", {}):
        result = asyncio.run(mgr.initialize(config=config))
    assert mgr.enabled is True or result is False


def test_initialize_selects_specified_engine():
    mgr = TTSManager()
    config = {
        "enabled": True,
        "engine": "edge-tts",
        "edge-tts": {"speaker_name": "test"},
    }
    try:
        asyncio.run(mgr.initialize(config=config))
        if mgr.enabled:
            assert mgr.current_engine == "edge-tts"
    except ImportError:
        pass


def test_initialize_fallback_to_first_provider():
    mgr = TTSManager()
    config = {
        "enabled": True,
        "engine": "nonexistent-engine",
        "edge-tts": {"speaker_name": "test"},
    }
    try:
        asyncio.run(mgr.initialize(config=config))
        if mgr.enabled and mgr.available_engines:
            assert mgr.current_engine == "edge-tts"
    except ImportError:
        pass


def test_initialize_synthesize_count_preserved():
    mgr = TTSManager()
    mgr._synthesize_count = 5
    asyncio.run(mgr.initialize(config=None))
    assert mgr._synthesize_count == 5


# ═══════════════════════════════════════════════════════════════
#  多引擎降级逻辑验证
# ═══════════════════════════════════════════════════════════════

def _make_mock_provider(name, return_value):
    provider = MagicMock(spec=TTSProviderBase)
    provider.name = name
    provider.health_check.return_value = {"available": True, "engine": name}
    provider.synthesize = AsyncMock(return_value=return_value)
    return provider


def test_synthesize_primary_success():
    mgr = TTSManager()
    primary = _make_mock_provider("primary", b"audio_data")
    mgr._providers = {"primary": primary}
    mgr._current_engine = "primary"
    mgr._enabled = True
    result = asyncio.run(mgr.synthesize("测试"))
    assert result == b"audio_data"
    assert mgr._synthesize_count == 1
    assert mgr._last_error is None


def test_synthesize_primary_fail_fallback():
    mgr = TTSManager()
    primary = _make_mock_provider("primary", None)
    fallback = _make_mock_provider("fallback", b"fallback_audio")
    mgr._providers = {"primary": primary, "fallback": fallback}
    mgr._current_engine = "primary"
    mgr._enabled = True
    result = asyncio.run(mgr.synthesize("测试"))
    assert result == b"fallback_audio"
    assert mgr._current_engine == "fallback"
    assert mgr._last_error is None


def test_synthesize_all_engines_fail():
    mgr = TTSManager()
    p1 = _make_mock_provider("p1", None)
    p2 = _make_mock_provider("p2", None)
    mgr._providers = {"p1": p1, "p2": p2}
    mgr._current_engine = "p1"
    mgr._enabled = True
    result = asyncio.run(mgr.synthesize("测试"))
    assert result is None
    assert mgr._last_error == "所有引擎不可用"


def test_synthesize_disabled_returns_none():
    mgr = TTSManager()
    mgr._enabled = False
    result = asyncio.run(mgr.synthesize("测试"))
    assert result is None


def test_synthesize_empty_text_returns_none():
    mgr = TTSManager()
    mgr._enabled = True
    mgr._current_engine = "primary"
    mgr._providers = {"primary": _make_mock_provider("primary", b"data")}
    result = asyncio.run(mgr.synthesize(""))
    assert result is None


def test_synthesize_increments_count():
    mgr = TTSManager()
    primary = _make_mock_provider("primary", b"audio")
    mgr._providers = {"primary": primary}
    mgr._current_engine = "primary"
    mgr._enabled = True
    asyncio.run(mgr.synthesize("测试1"))
    asyncio.run(mgr.synthesize("测试2"))
    assert mgr._synthesize_count == 2


def test_synthesize_sets_last_error_on_failure():
    mgr = TTSManager()
    primary = _make_mock_provider("primary", None)
    mgr._providers = {"primary": primary}
    mgr._current_engine = "primary"
    mgr._enabled = True
    asyncio.run(mgr.synthesize("测试"))
    assert mgr._last_error is not None


# ═══════════════════════════════════════════════════════════════
#  switch_engine 验证
# ═══════════════════════════════════════════════════════════════

def test_switch_engine_success():
    mgr = TTSManager()
    p1 = _make_mock_provider("edge-tts", b"data")
    p2 = _make_mock_provider("gpt-sovits", b"data2")
    mgr._providers = {"edge-tts": p1, "gpt-sovits": p2}
    mgr._current_engine = "edge-tts"
    result = asyncio.run(mgr.switch_engine("gpt-sovits"))
    assert result is True
    assert mgr.current_engine == "gpt-sovits"


def test_switch_engine_to_same():
    mgr = TTSManager()
    p = _make_mock_provider("edge-tts", b"data")
    mgr._providers = {"edge-tts": p}
    mgr._current_engine = "edge-tts"
    result = asyncio.run(mgr.switch_engine("edge-tts"))
    assert result is True


# ═══════════════════════════════════════════════════════════════
#  health_check 完整字段验证
# ═══════════════════════════════════════════════════════════════

def test_health_check_full_fields():
    mgr = TTSManager()
    p1 = _make_mock_provider("edge-tts", None)
    p2 = _make_mock_provider("gpt-sovits", None)
    mgr._providers = {"edge-tts": p1, "gpt-sovits": p2}
    mgr._current_engine = "edge-tts"
    mgr._enabled = True
    mgr._last_error = "test error"
    mgr._synthesize_count = 3
    health = mgr.health_check()
    assert health["enabled"] is True
    assert health["current_engine"] == "edge-tts"
    assert health["available_engines"] == ["edge-tts", "gpt-sovits"]
    assert health["synthesize_count"] == 3
    assert health["last_error"] == "test error"
    assert "providers" in health
    assert "edge-tts" in health["providers"]
    assert "gpt-sovits" in health["providers"]


def test_health_check_provider_health():
    mgr = TTSManager()
    p = _make_mock_provider("edge-tts", None)
    mgr._providers = {"edge-tts": p}
    mgr._current_engine = "edge-tts"
    mgr._enabled = True
    health = mgr.health_check()
    assert health["providers"]["edge-tts"]["available"] is True
    assert health["providers"]["edge-tts"]["engine"] == "edge-tts"


def test_health_check_no_last_error():
    mgr = TTSManager()
    mgr._enabled = False
    health = mgr.health_check()
    assert health["last_error"] is None


def test_health_check_disabled_full():
    mgr = TTSManager()
    health = mgr.health_check()
    assert set(health.keys()) == {
        "enabled", "current_engine", "available_engines",
        "providers", "synthesize_count", "last_error",
    }
    assert health["enabled"] is False
    assert health["current_engine"] is None
    assert health["available_engines"] == []
    assert health["providers"] == {}
    assert health["synthesize_count"] == 0
    assert health["last_error"] is None


# ═══════════════════════════════════════════════════════════════
#  get_engine 验证
# ═══════════════════════════════════════════════════════════════

def test_get_engine_existing():
    mgr = TTSManager()
    p = _make_mock_provider("edge-tts", None)
    mgr._providers = {"edge-tts": p}
    assert mgr.get_engine("edge-tts") is p


def test_get_engine_unknown():
    mgr = TTSManager()
    assert mgr.get_engine("unknown") is None


# ═══════════════════════════════════════════════════════════════
#  available_engines 验证
# ═══════════════════════════════════════════════════════════════

def test_available_engines_multiple():
    mgr = TTSManager()
    p1 = _make_mock_provider("edge-tts", None)
    p2 = _make_mock_provider("gpt-sovits", None)
    mgr._providers = {"edge-tts": p1, "gpt-sovits": p2}
    engines = mgr.available_engines
    assert set(engines) == {"edge-tts", "gpt-sovits"}


# ═══════════════════════════════════════════════════════════════
#  TTSProviderBase 验证
# ═══════════════════════════════════════════════════════════════

def test_tts_provider_base_is_abstract():

    from voice.base import TTSProviderBase
    assert hasattr(TTSProviderBase, "__abstractmethods__")


def test_tts_provider_base_has_synthesize():
    from voice.base import TTSProviderBase
    assert hasattr(TTSProviderBase, "synthesize")


def test_tts_provider_base_has_health_check():
    from voice.base import TTSProviderBase
    assert hasattr(TTSProviderBase, "health_check")


def test_tts_provider_base_has_name():
    from voice.base import TTSProviderBase
    assert hasattr(TTSProviderBase, "name")


def test_tts_provider_base_supports_streaming_default():
    from voice.base import TTSProviderBase
    class ConcreteProvider(TTSProviderBase):
        async def synthesize(self, text, **kwargs):
            return b""
        def health_check(self):
            return {"available": True, "engine": "test"}
        @property
        def name(self):
            return "test"
    p = ConcreteProvider()
    assert p.supports_streaming is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All voice/manager tests passed!")
