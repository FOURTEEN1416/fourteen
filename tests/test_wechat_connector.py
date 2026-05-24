"""T-17: WeChatConnector 非文本消息发送单元测试"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import base64


class TestWeChatConnectorSendMethods:
    def _make_connector(self):
        from wechat_direct.connector import WeChatConnector
        mgr = MagicMock()
        conn = WeChatConnector(mgr)
        conn.token = "test_token"
        conn._last_user_id = "test_user"
        conn._context_tokens = {"test_user": "test_ctx"}
        return conn

    @patch("wechat_direct.connector._send_voice_message")
    def test_send_voice_success(self, mock_send):
        mock_send.return_value = {"ret": 0}
        conn = self._make_connector()
        result = conn.send_voice(b"fake_silk_audio", duration_ms=3000)
        assert result is True
        call_args = mock_send.call_args
        assert call_args.kwargs.get("fmt") == "silk" or call_args[1].get("fmt") == "silk"

    @patch("wechat_direct.connector._send_image_message")
    def test_send_image_success(self, mock_send):
        mock_send.return_value = {"ret": 0}
        conn = self._make_connector()
        result = conn.send_image(b"fake_image_data")
        assert result is True
        call_args = mock_send.call_args
        image_b64 = call_args.kwargs.get("image_data_b64") or call_args[1].get("image_data_b64")
        assert base64.b64decode(image_b64) == b"fake_image_data"

    @patch("wechat_direct.connector._send_emoji_message")
    def test_send_emoji_success(self, mock_send):
        mock_send.return_value = {"ret": 0}
        conn = self._make_connector()
        result = conn.send_emoji("abc123md5")
        assert result is True

    def test_send_voice_no_token(self):
        conn = self._make_connector()
        conn.token = ""
        result = conn.send_voice(b"audio")
        assert result is False

    def test_send_image_no_data(self):
        conn = self._make_connector()
        result = conn.send_image(b"")
        assert result is False


class TestMessageBodyFormat:
    @patch("wechat_direct.connector._post_api")
    def test_voice_message_type_34(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.connector import _send_voice_message
        _send_voice_message("user1", "b64audio", 3000, "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 34
        assert msg["item_list"][0]["type"] == 34
        assert "voice_item" in msg["item_list"][0]

    @patch("wechat_direct.connector._post_api")
    def test_image_message_type_3(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.connector import _send_image_message
        _send_image_message("user1", "b64image", "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 3
        assert msg["item_list"][0]["type"] == 3
        assert "image_item" in msg["item_list"][0]

    @patch("wechat_direct.connector._post_api")
    def test_emoji_message_type_47(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.connector import _send_emoji_message
        _send_emoji_message("user1", "md5hash", "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 47
        assert msg["item_list"][0]["type"] == 47
        assert "emoji_item" in msg["item_list"][0]
