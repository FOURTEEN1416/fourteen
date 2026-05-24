"""T-16: AudioFormatConverter 单元测试"""
from unittest.mock import MagicMock, patch

from voice.audio_converter import AudioFormatConverter


class TestAudioFormatConverter:
    def setup_method(self):
        self.converter = AudioFormatConverter()

    def test_health_check_structure(self):
        result = self.converter.health_check()
        assert "ffmpeg" in result
        assert "silk_encoder" in result
        assert isinstance(result["ffmpeg"], bool)
        assert isinstance(result["silk_encoder"], bool)

    def test_to_silk_empty_input(self):
        result = self.converter.to_silk(b"", "mp3")
        assert result is None

    def test_to_amr_empty_input(self):
        result = self.converter.to_amr(b"", "mp3")
        assert result is None

    @patch("subprocess.run")
    def test_to_amr_with_mock_ffmpeg(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"fake_amr_data")
        result = self.converter.to_amr(b"fake_mp3_data", "mp3")
        assert result == b"fake_amr_data"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_ffmpeg_convert_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
        result = self.converter._ffmpeg_convert(b"data", "mp3", "amr", 8000, 1)
        assert result is None

    @patch("shutil.which", return_value="/usr/bin/ffmpeg")
    def test_health_check_ffmpeg_available(self, mock_which):
        self.converter._has_ffmpeg = None
        result = self.converter.health_check()
        assert result["ffmpeg"] is True
