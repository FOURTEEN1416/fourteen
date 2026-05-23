"""单元测试: 情感语音映射器"""
import sys
sys.path.insert(0, ".")

from shisi.voice_ext.emotion_tts import EmotionVoiceMapper, VoiceEnhancer


def test_emotion_params_mapping():
    mapper = EmotionVoiceMapper()
    params = mapper.get_params("开心")
    assert "rate" in params
    assert "volume" in params
    assert params["rate"] == "+10%"


def test_unknown_emotion_defaults():
    mapper = EmotionVoiceMapper()
    params = mapper.get_params("不存在的情感")
    assert params["rate"] == "+0%"
    assert params["volume"] == "+0%"


def test_apply_to_edge_tts():
    mapper = EmotionVoiceMapper()
    result = mapper.apply_to_edge_tts("生气")
    assert "rate" in result
    assert "volume" in result
    assert "pitch" not in result


def test_character_override():
    mapper = EmotionVoiceMapper()
    mapper.set_character_override("char1", "开心", {"rate": "+20%", "volume": "+20%"})
    params = mapper.get_params("开心", character_id="char1")
    assert params["rate"] == "+20%"
    assert params["volume"] == "+20%"


def test_voice_enhancer_still_works():
    enhancer = VoiceEnhancer()
    config = enhancer.get_tts_config("test_char", emotion="")
    assert "tts_engine" in config
    assert "speed" in config


def test_all_9_emotions():
    mapper = EmotionVoiceMapper()
    for emotion in ["开心", "撒娇", "温柔", "伤心", "生气", "害怕", "害羞", "傲娇", "平常"]:
        result = mapper.apply_to_edge_tts(emotion)
        assert "rate" in result
        assert "volume" in result


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All emotion_tts tests passed!")
