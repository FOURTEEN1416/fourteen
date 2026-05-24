"""单元测试: VoiceTrainingManager音色训练"""
import sys
import asyncio
sys.path.insert(0, ".")

from voice.voice_training import VoiceTrainingManager


def test_init_defaults():
    mgr = VoiceTrainingManager()
    assert mgr.state["status"] == "idle"
    assert not mgr.is_training


def test_state_immutable():
    mgr = VoiceTrainingManager()
    s1 = mgr.state
    s1["status"] = "modified"
    assert mgr.state["status"] == "idle"


def test_preprocess_no_files():
    mgr = VoiceTrainingManager()
    result = asyncio.run(mgr.preprocess("nonexistent_model"))
    assert "error" in result


def test_generate_dataset_no_files():
    mgr = VoiceTrainingManager()
    result = asyncio.run(mgr.generate_dataset("nonexistent_model"))
    assert "error" in result


def test_train_already_training():
    mgr = VoiceTrainingManager()
    mgr._is_training = True
    result = asyncio.run(mgr.train("test_model"))
    assert "error" in result
    assert "已在进行中" in result["error"]
    mgr._is_training = False


def test_train_no_dataset():
    mgr = VoiceTrainingManager()
    result = asyncio.run(mgr.train("nonexistent_model"))
    assert "error" in result


def test_save_uploads():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = VoiceTrainingManager(models_dir=tmpdir)
        files = [("test.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ")]
        result = asyncio.run(mgr.save_uploads(files, "test_model"))
        assert result["saved"] == 1


def test_save_uploads_path_traversal():
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = VoiceTrainingManager(models_dir=tmpdir)
        files = [("../../etc/passwd", b"malicious")]
        result = asyncio.run(mgr.save_uploads(files, "test_model"))
        assert result["saved"] == 1


def test_safe_name_regex():
    from voice.voice_training import _SAFE_NAME_RE
    assert _SAFE_NAME_RE.sub("_", "model;rm -rf /") == "model_rm_-rf__"
    assert _SAFE_NAME_RE.sub("_", "normal-name") == "normal-name"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All voice_training tests passed!")
