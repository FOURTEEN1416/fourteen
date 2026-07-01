"""T-17: WeChatConnector 非文本消息发送单元测试"""
import base64
from unittest.mock import MagicMock, patch


class TestWeChatConnectorSendMethods:
    def _make_connector(self):
        from wechat_direct.wechat_connector import WeChatConnector
        mgr = MagicMock()
        conn = WeChatConnector(mgr)
        conn.token = "test_token"
        conn._last_user_id = "test_user"
        conn._context_tokens = {"test_user": "test_ctx"}
        return conn

    @patch("wechat_direct.wechat_connector._send_voice_message")
    def test_send_voice_success(self, mock_send):
        mock_send.return_value = {"ret": 0}
        conn = self._make_connector()
        result = conn.send_voice(b"fake_silk_audio", duration_ms=3000)
        assert result is True
        call_args = mock_send.call_args
        assert call_args.kwargs.get("fmt") == "silk" or call_args[1].get("fmt") == "silk"

    @patch("wechat_direct.wechat_connector._send_image_message")
    def test_send_image_success(self, mock_send):
        mock_send.return_value = {"ret": 0}
        conn = self._make_connector()
        result = conn.send_image(b"fake_image_data")
        assert result is True
        call_args = mock_send.call_args
        image_b64 = call_args.kwargs.get("image_data_b64") or call_args[1].get("image_data_b64")
        assert base64.b64decode(image_b64) == b"fake_image_data"

    @patch("wechat_direct.wechat_connector._send_emoji_message")
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
    @patch("wechat_direct.wechat_connector._post_api")
    def test_voice_message_type_34(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.wechat_connector import _send_voice_message
        _send_voice_message("user1", "b64audio", 3000, "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 34
        assert msg["item_list"][0]["type"] == 34
        assert "voice_item" in msg["item_list"][0]

    @patch("wechat_direct.wechat_connector._post_api")
    def test_image_message_type_3(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.wechat_connector import _send_image_message
        _send_image_message("user1", "b64image", "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 3
        assert msg["item_list"][0]["type"] == 3
        assert "image_item" in msg["item_list"][0]

    @patch("wechat_direct.wechat_connector._post_api")
    def test_emoji_message_type_47(self, mock_post):
        mock_post.return_value = {"ret": 0}
        from wechat_direct.wechat_connector import _send_emoji_message
        _send_emoji_message("user1", "md5hash", "ctx")
        call_args = mock_post.call_args
        body = call_args[0][1]
        msg = body["msg"]
        assert msg["message_type"] == 47
        assert msg["item_list"][0]["type"] == 47
        assert "emoji_item" in msg["item_list"][0]


class TestHandleMessage:
    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_sends_fallback_when_reply_empty(self, mock_call, mock_send):
        from wechat_direct.wechat_connector import WeChatConnector
        mock_call.return_value = {"reply": ""}
        conn = WeChatConnector(MagicMock())
        conn.token = "test_token"
        conn._last_user_id = "wx_user_1"
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}

        raw_msg = {
            "message_type": 1,
            "message_id": "m1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
        }
        conn._handle_message(raw_msg)

        assert mock_send.call_count == 1
        args = mock_send.call_args
        assert "不知道该怎么回复" in args.kwargs.get("text", "") or "不知道该怎么回复" in args[0][1]

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_sends_normal_reply(self, mock_call, mock_send):
        from wechat_direct.wechat_connector import WeChatConnector
        mock_call.return_value = {"reply": "你好呀"}
        conn = WeChatConnector(MagicMock())
        conn.token = "test_token"
        conn._last_user_id = "wx_user_1"
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}

        raw_msg = {
            "message_type": 1,
            "message_id": "m2",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "在吗"}}],
        }
        conn._handle_message(raw_msg)

        assert mock_send.call_count == 1
        args = mock_send.call_args
        text = args.kwargs.get("text", "") or args[0][1]
        assert text == "你好呀"
