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

    # ──────────────────────────────────────────────────────────────────
    # V5 缺口（包 T · 媒体消息**收包**）—— TDD 红
    #
    # 现状（`wechat_direct/wechat_connector.py:747-749`）：
    #     msg_type = raw_msg.get("message_type", 0)
    #     if msg_type != 1:      # 只看用户消息
    #         return
    # 该守卫会把 `message_type ∈ {3, 34}` 的图片/语音消息在**入口直接丢弃**，
    # 使下方 item 级 `type ∈ {1, 34, 3}` 的解析（同文件 770-780 行）对媒体消息
    # 成为**死代码**（对应任务包 V2 缺口：`image_data` 全仓无消费者）。
    #
    # 以下用例断言"媒体消息必须与文本消息一样进入处理链路"，
    # W3 完成 V2 接线后应转绿；在此之前**预期红**。
    # ──────────────────────────────────────────────────────────────────

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_message_type3_is_routed(self, mock_call, mock_send):
        """type:3 图片消息不得在入口被丢弃，必须进入处理链路并产生回复。"""
        from wechat_direct.wechat_connector import WeChatConnector
        mock_call.return_value = {"reply": "这张图我看到了"}
        conn = WeChatConnector(MagicMock())
        conn.token = "test_token"
        conn._last_user_id = "wx_user_1"
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}

        raw_msg = {
            "message_type": 3,
            "message_id": "img_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 3, "image_item": {"image_data": "ZmFrZV9pbWFnZV9ieXRlcw=="}}],
        }
        conn._handle_message(raw_msg)

        assert mock_call.call_count == 1, (
            "图片消息(message_type=3) 应在入口被放行并路由到 user_manager，"
            "而非被 `msg_type != 1` 守卫直接 return"
        )
        assert mock_send.call_count == 1, "图片消息应产生一次文本回复"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_message_type34_is_transcribed_then_routed(self, mock_call, mock_send):
        """type:34 语音消息不得在入口被丢弃，应先 ASR 转写、再以转写文本路由。"""
        from wechat_direct.wechat_connector import WeChatConnector
        mock_call.return_value = {"reply": "收到你的语音"}
        conn = WeChatConnector(MagicMock())
        conn.token = "test_token"
        conn._last_user_id = "wx_user_1"
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
        conn._transcribe_voice = MagicMock(return_value="今天天气怎么样")

        raw_msg = {
            "message_type": 34,
            "message_id": "voice_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 34, "voice_item": {"voice_data": "ZmFrZV9zaWxr"}}],
        }
        conn._handle_message(raw_msg)

        assert conn._transcribe_voice.call_count == 1, (
            "语音消息(message_type=34) 应触发 ASR 转写（ASRHandler 通路）"
        )
        assert mock_call.call_count == 1, "语音消息应被路由到 user_manager"
        assert mock_call.call_args[0][2] == "今天天气怎么样", "路由文本应为 ASR 转写结果"
        assert mock_send.call_count == 1

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_message_type34_falls_back_when_asr_unavailable(self, mock_call, mock_send):
        """ASR 未启用（返回空串）时，语音消息仍须进入链路，不得被入口守卫丢弃。"""
        from wechat_direct.wechat_connector import WeChatConnector
        mock_call.return_value = {"reply": "（我暂时不知道该怎么回复，可以再说一次吗？）"}
        conn = WeChatConnector(MagicMock())
        conn.token = "test_token"
        conn._last_user_id = "wx_user_1"
        conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
        conn._transcribe_voice = MagicMock(return_value="")  # 模拟 ASR disabled

        raw_msg = {
            "message_type": 34,
            "message_id": "voice_2",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 34, "voice_item": {"voice_data": "ZmFrZV9zaWxr"}}],
        }
        conn._handle_message(raw_msg)

        assert conn._transcribe_voice.call_count == 1, "ASR 未启用时仍应尝试转写（配置驱动）"
        assert mock_call.call_count == 1, "ASR 不可用时语音消息仍须被路由，不得静默丢弃"
        assert mock_send.call_count == 1
