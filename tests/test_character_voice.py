"""单元测试: 角色专属音色管理"""
import os
import sys
import tempfile

sys.path.insert(0, ".")

from shisi.voice_ext.character_voice import CharacterVoiceManager


def test_bind_and_get():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mgr = CharacterVoiceManager(config_path=path)
        mgr.bind_voice("char1", "edge-tts", speaker_name="zh-CN-XiaoyiNeural")
        config = mgr.get_voice_config("char1")
        assert config is not None
        assert config["engine"] == "edge-tts"
        assert config["speaker_name"] == "zh-CN-XiaoyiNeural"
    finally:
        os.unlink(path)


def test_unbind():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mgr = CharacterVoiceManager(config_path=path)
        mgr.bind_voice("char2", "gpt-sovits")
        assert mgr.unbind_voice("char2") is True
        assert mgr.get_voice_config("char2") is None
        assert mgr.unbind_voice("nonexist") is False
    finally:
        os.unlink(path)


def test_list_bindings():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mgr = CharacterVoiceManager(config_path=path)
        mgr.bind_voice("a", "edge-tts")
        mgr.bind_voice("b", "gpt-sovits")
        bindings = mgr.list_bindings()
        assert "a" in bindings
        assert "b" in bindings
    finally:
        os.unlink(path)


def test_get_nonexistent():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        mgr = CharacterVoiceManager(config_path=path)
        assert mgr.get_voice_config("nonexist") is None
    finally:
        os.unlink(path)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All character_voice tests passed!")
