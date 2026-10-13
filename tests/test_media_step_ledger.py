"""媒体处理 step 落账 event_ledger（event_type=media_step）测试（2026-10-12 多模态域批）。

契约：
- connector 在 image_direct / image_download_failed / image_unsupported /
  voice_text / voice_unavailable 五个 step 点 fail-soft 落账（复用 EventLedger.append）；
- payload 只含 step/reason/kind/bytes/text_len/msg_id 等元数据（P0：无消息原文）；
- 落账故障不得影响消息主流程。

现状红锚点：生产 agent_plane.db 同型查询（event_type='media_step'）0 行
（任务输入取证报告 ssh 实证）——本文件实现前必红，实现后 ≥1 行。
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from shisi.agent_plane.event_ledger import EventLedger, set_default_ledger

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"
_TEXT_SAMPLE = "帮我看看明天天气怎么穿"  # 隐私断言用：不得出现在任何 payload 里


@pytest.fixture()
def tmp_ledger(tmp_path):
    ledger = EventLedger(tmp_path / "agent_plane.db")
    set_default_ledger(ledger)
    yield ledger
    set_default_ledger(None)


def _make_conn():
    from wechat_direct.wechat_connector import WeChatConnector

    conn = WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    return conn


def _image_msg(msg_id="img_ledger_1"):
    return {
        "message_type": 1,
        "message_id": msg_id,
        "from_user_id": "wx_user_1",
        "context_token": "ctx",
        "item_list": [
            {
                "type": 2,
                "image_item": {
                    "media": {"encrypt_query_param": "tok"},
                    "aeskey": "000102030405060708090a0b0c0d0e0f",
                },
            }
        ],
    }


def _voice_msg(msg_id, text):
    return {
        "message_type": 1,
        "message_id": msg_id,
        "from_user_id": "wx_user_1",
        "context_token": "ctx",
        "item_list": [
            {
                "type": 3,
                "voice_item": {
                    "media": {"encrypt_query_param": "tok", "aes_key": "MDdiNjFhZTQ0ZmUwNDBjNTgxZTAyYzM0MjM0NTY3ODk="},
                    "encode_type": 6,
                    "text": text,
                },
            }
        ],
    }


class TestMediaStepLedger:
    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_direct_recorded(self, mock_call, mock_send, tmp_ledger):
        """① 图片直传成功 → media_step/image_direct 行含 bytes，且无消息原文。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_BYTES)
        conn._handle_message(_image_msg())

        rows = tmp_ledger.query(event_type="media_step")
        assert rows, "media_step 行必须落账"
        steps = [r.payload.get("step") for r in rows]
        assert "image_direct" in steps
        direct = next(r for r in rows if r.payload.get("step") == "image_direct")
        assert direct.payload.get("bytes") == len(_PNG_BYTES)
        dumped = json.dumps([r.payload for r in rows], ensure_ascii=False)
        assert _TEXT_SAMPLE not in dumped, "隐私红线：消息原文不得入账"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_ledger_failure_fail_soft(self, mock_call, mock_send, tmp_ledger):
        """② 落账确实被调用且抛异常被吞 → 消息主流程照常完成、回复照常发送。"""
        mock_call.return_value = {"reply": "一切正常"}
        tmp_ledger.append = MagicMock(side_effect=RuntimeError("db broken"))
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_BYTES)
        conn._handle_message(_image_msg("img_ledger_2"))

        assert tmp_ledger.append.call_count >= 1, "落账必须被调用（未接线则本断言红）"
        assert mock_call.call_count == 1, "落账故障不得阻断主链"
        assert mock_send.call_count == 1, "回复必须照常发送"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_text_and_unavailable_recorded(self, mock_call, mock_send, tmp_ledger):
        """③ 语音两 step 点：服务端 ASR 文本 → voice_text；ASR 未启用 → voice_unavailable。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._handle_message(_voice_msg("v_ledger_1", _TEXT_SAMPLE))

        conn._voice_asr_available = MagicMock(return_value=False)
        conn._handle_message(_voice_msg("v_ledger_2", ""))

        steps = [r.payload.get("step") for r in tmp_ledger.query(event_type="media_step")]
        assert "voice_text" in steps
        assert "voice_unavailable" in steps
        dumped = json.dumps(
            [r.payload for r in tmp_ledger.query(event_type="media_step")],
            ensure_ascii=False,
        )
        assert _TEXT_SAMPLE not in dumped, "隐私红线：转写原文不得入账"
