"""T-IMG: ImageGenTool 配置与调用单元测试"""
from unittest.mock import MagicMock, patch

import httpx

from tools.builtin.extra_tools import ImageGenTool


class TestImageGenTool:
    def test_health_unconfigured(self):
        with patch.dict("os.environ", {}, clear=True):
            tool = ImageGenTool()
            health = tool.health_check()
        assert health["available"] is False
        assert "IMAGE_GEN_PROVIDER" in health["config_hint"]

    def test_health_configured(self):
        env = {
            "IMAGE_GEN_PROVIDER": "agnes",
            "IMAGE_GEN_API_KEY": "sk-test",
        }
        with patch.dict("os.environ", env, clear=True):
            tool = ImageGenTool()
            health = tool.health_check()
        assert health["available"] is True
        assert health["model"] == "agnes-image-2.1-flash"

    def test_execute_without_prompt_fails(self):
        with patch.dict("os.environ", {"IMAGE_GEN_PROVIDER": "agnes", "IMAGE_GEN_API_KEY": "sk-test"}, clear=True):
            tool = ImageGenTool()
            result = tool.execute(prompt="")
        assert result.success is False
        assert "prompt" in result.error

    def test_execute_success(self):
        env = {
            "IMAGE_GEN_PROVIDER": "agnes",
            "IMAGE_GEN_API_KEY": "sk-test",
        }
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"data": [{"url": "https://example.com/img.png"}]}
        fake_resp.raise_for_status.return_value = None

        with patch.dict("os.environ", env, clear=True):
            tool = ImageGenTool()
            with patch.object(httpx, "post", return_value=fake_resp) as mock_post:
                result = tool.execute(prompt="a cat")

        assert result.success is True
        assert result.data["url"] == "https://example.com/img.png"
        assert result.data["model"] == "agnes-image-2.1-flash"
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["prompt"] == "a cat"
        assert kwargs["json"].get("response_format") is None
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"

    def test_execute_http_error(self):
        env = {
            "IMAGE_GEN_PROVIDER": "agnes",
            "IMAGE_GEN_API_KEY": "sk-test",
        }
        fake_resp = MagicMock()
        fake_resp.text = "invalid key"
        fake_resp.status_code = 401
        fake_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=fake_resp
        )

        with patch.dict("os.environ", env, clear=True):
            tool = ImageGenTool()
            with patch.object(httpx, "post", return_value=fake_resp):
                result = tool.execute(prompt="a cat")

        assert result.success is False
        assert "401" in result.error
