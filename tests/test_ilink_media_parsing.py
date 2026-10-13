"""iLink 媒体消息（图片 type=2 / 语音 type=3）入站解析与降级测试。

背景（2026-10-12 多模态 iLink 协议适配批）：生产 iLink 协议 item 枚举为
1=文本 / 2=图片 / 3=语音（weixin-ilink types.py 与 photon-hq types.ts 双源一致），
旧实现按旧微信协议读 ``image_item.image_data`` / ``voice_item.voice_data``
（base64 内联）→ 新协议媒体消息在进多模态分支前被静默丢弃。本批契约：

- 语音（item type=3）：``voice_item.text``（微信服务端 ASR，'if available'）非空
  → 直接作正文 + ``[wx][step=voice_text]``；为空 → 媒体引用下载 + 本地 ASR
  （配置驱动），仍不可得 → ``[wx][step=voice_unavailable]`` 后回 voice_unsupported
  受控话术（不拿空文本进 LLM，也不静默丢消息）；
- 图片（item type=2）：``image_item{media, aeskey}`` → CDN 下载 + AES-128-ECB/PKCS7
  解密（超时 15s）→ base64 → 复用既有 image_data 附件链；失败 → INFO 日志 +
  无文本时回 fallback_lines 固定话术；
- 旧分支兼容：type=34 语音 / 旧 type=3 图片（内联 base64）保留，仅在其后追加新分支。

隐私红线：日志断言只认 ``step=`` 与长度字段，不得出现消息原文。
"""
import base64
import logging
from unittest.mock import MagicMock, patch

import pytest

# 测试用 16 字节 key 的两种合法编码形态（weixin-ilink cdn.py parse_aes_key 同构）
_RAW_KEY = bytes(range(16))
_HEX_KEY_STR = _RAW_KEY.hex()                      # image_item.aeskey：16 字节 raw 的 hex 串
_B64_RAW_KEY = base64.b64encode(_RAW_KEY).decode()          # media.aes_key 形态一：base64(raw16)
_HEX32 = bytes(range(16, 32)).hex()
_B64_HEX_KEY = base64.b64encode(_HEX32.encode("ascii")).decode()  # 形态二：base64(hex32)，语音/文件用

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"

_TEXT_SAMPLE = "帮我看看明天天气怎么穿"  # 隐私断言用：不得出现在任何日志里


def _image_item(param="enc-query-token", aeskey=_HEX_KEY_STR, media_aes_key=None, full_url=""):
    media = {"encrypt_query_param": param}
    if media_aes_key:
        media["aes_key"] = media_aes_key
    if full_url:
        media["full_url"] = full_url
    item = {"type": 2, "image_item": {"media": media, "aeskey": aeskey}}
    if aeskey is None:
        item["image_item"].pop("aeskey", None)
    return item


def _voice_item(text="", media_aes_key=_B64_HEX_KEY, param="voice-query-token"):
    return {
        "type": 3,
        "voice_item": {
            "media": {"encrypt_query_param": param, "aes_key": media_aes_key},
            "encode_type": 6,
            "text": text,
        },
    }


def _make_conn():
    from wechat_direct.wechat_connector import WeChatConnector

    conn = WeChatConnector(MagicMock())
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    return conn


# ═══════════════════════════════════════════════
# 一、纯函数：parse_ilink_items / _parse_aes_key
# ═══════════════════════════════════════════════
class TestParseIlinkItems:
    def test_text_item(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([{"type": 1, "text_item": {"text": _TEXT_SAMPLE}}])
        assert parsed.text == _TEXT_SAMPLE
        assert parsed.image_payload is None and parsed.voice_media is None
        assert parsed.legacy_voice_data == "" and parsed.legacy_image_data == ""

    def test_image_item_type2_key_from_item_aeskey(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([_image_item()])
        assert parsed.image_payload is not None, "新协议图片 item type=2 必须解析出媒体引用"
        assert parsed.image_payload.encrypt_query_param == "enc-query-token"
        assert parsed.image_payload.aes_key == _RAW_KEY, "item 级 aeskey（raw hex）优先"

    def test_image_item_key_fallback_to_media_aes_key_b64_raw16(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([_image_item(aeskey=None, media_aes_key=_B64_RAW_KEY)])
        assert parsed.image_payload.aes_key == _RAW_KEY

    def test_image_item_full_url_passthrough(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([_image_item(full_url="https://cdn.example.com/dl?x=1")])
        assert parsed.image_payload.full_url == "https://cdn.example.com/dl?x=1"

    def test_voice_item_type3_with_text(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([_voice_item(text=_TEXT_SAMPLE)])
        assert parsed.voice_text == _TEXT_SAMPLE
        assert parsed.voice_media is not None
        assert parsed.voice_media.encrypt_query_param == "voice-query-token"
        assert parsed.voice_media.aes_key == bytes.fromhex(_HEX32), "语音 media.aes_key 是 base64(hex32)"

    def test_voice_item_type3_without_text(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([_voice_item(text="")])
        assert parsed.voice_text == ""
        assert parsed.voice_media is not None, "text 缺省时仍要保留媒体引用供下载+本地 ASR"

    def test_legacy_type34_voice_kept(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([{"type": 34, "voice_item": {"voice_data": "ZmFrZV9zaWxr"}}])
        assert parsed.legacy_voice_data == "ZmFrZV9zaWxr"
        assert parsed.voice_media is None

    def test_legacy_type3_image_kept(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([{"type": 3, "image_item": {"image_data": "Zm9v"}}])
        assert parsed.legacy_image_data == "Zm9v", "旧协议 type=3 图片（内联 base64）兼容保留"
        assert parsed.voice_media is None

    def test_unknown_types_collected(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([{"type": 47, "emoji_item": {"md5": "x"}}, {"type": 77}])
        assert parsed.unknown_types == [47, 77]

    def test_garbage_input_no_raise(self):
        from wechat_direct.wechat_connector import parse_ilink_items

        parsed = parse_ilink_items([None, "junk", 42])
        assert parsed.text == "" and parsed.unknown_types == []


class TestParseAesKey:
    def test_raw_hex_32_chars(self):
        from wechat_direct.wechat_connector import _parse_aes_key

        assert _parse_aes_key(_HEX_KEY_STR) == _RAW_KEY

    def test_b64_raw16(self):
        from wechat_direct.wechat_connector import _parse_aes_key

        assert _parse_aes_key(_B64_RAW_KEY) == _RAW_KEY

    def test_b64_hex32(self):
        from wechat_direct.wechat_connector import _parse_aes_key

        assert _parse_aes_key(_B64_HEX_KEY) == bytes.fromhex(_HEX32)

    @pytest.mark.parametrize("bad", [None, "", "not-a-key!!", "dG9vc2hvcnQ=", _HEX_KEY_STR[:30]])
    def test_invalid_returns_none(self, bad):
        from wechat_direct.wechat_connector import _parse_aes_key

        assert _parse_aes_key(bad) is None


# ═══════════════════════════════════════════════
# 二、纯函数：AES-128-ECB + PKCS7
# ═══════════════════════════════════════════════
def _ecb_encrypt(plain: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    pad = 16 - len(plain) % 16
    padded = plain + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(padded) + enc.finalize()


class TestAesEcbDecrypt:
    def test_round_trip(self):
        from wechat_direct.wechat_connector import _aes_ecb_decrypt

        cipher = _ecb_encrypt(_PNG_BYTES, _RAW_KEY)
        assert _aes_ecb_decrypt(cipher, _RAW_KEY) == _PNG_BYTES

    def test_exact_block_padding_stripped(self):
        from wechat_direct.wechat_connector import _aes_ecb_decrypt

        plain = b"A" * 16  # 恰好整块 → 追加整块填充
        assert _aes_ecb_decrypt(_ecb_encrypt(plain, _RAW_KEY), _RAW_KEY) == plain

    def test_bad_padding_raises(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        from wechat_direct.wechat_connector import _aes_ecb_decrypt

        # 手工构造"已填充"缓冲：末字节 0x00 → PKCS7 pad 值非法（helper 会自动补合法填充，不能复用）
        enc = Cipher(algorithms.AES(_RAW_KEY), modes.ECB()).encryptor()
        cipher = enc.update(b"A" * 15 + b"\x00") + enc.finalize()
        with pytest.raises(ValueError):
            _aes_ecb_decrypt(cipher, _RAW_KEY)

    def test_incomplete_padding_raises(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        from wechat_direct.wechat_connector import _aes_ecb_decrypt

        # 末字节 0x03 但尾随两字节不是 0x03：填充不完整（16 字节整块）
        enc = Cipher(algorithms.AES(_RAW_KEY), modes.ECB()).encryptor()
        cipher = enc.update(b"A" * 13 + b"\x02\x02\x03") + enc.finalize()
        with pytest.raises(ValueError):
            _aes_ecb_decrypt(cipher, _RAW_KEY)

    def test_length_not_multiple_of_16_raises(self):
        from wechat_direct.wechat_connector import _aes_ecb_decrypt

        with pytest.raises(ValueError):
            _aes_ecb_decrypt(b"short", _RAW_KEY)


# ═══════════════════════════════════════════════
# 三、下载方法（CDN 引用 → 字节，15s 超时）
# ═══════════════════════════════════════════════
class TestDownloadMedia:
    def _ref(self, **kw):
        from wechat_direct.wechat_connector import MediaRef

        defaults = dict(encrypt_query_param="tok", full_url="", aes_key=_RAW_KEY)
        defaults.update(kw)
        return MediaRef(**defaults)

    def test_download_and_decrypt(self):
        from wechat_direct.wechat_connector import WeChatConnector

        cipher = _ecb_encrypt(_PNG_BYTES, _RAW_KEY)
        conn = WeChatConnector(MagicMock())
        with patch("wechat_direct.wechat_connector.requests.get") as mock_get:
            mock_get.return_value = MagicMock(content=cipher, raise_for_status=lambda: None)
            raw = conn._download_media(self._ref())
        assert raw == _PNG_BYTES
        url = mock_get.call_args[0][0]
        assert "encrypted_query_param=tok" in url, "缺省应拼 CDN download URL"

    def test_full_url_priority(self):
        from wechat_direct.wechat_connector import WeChatConnector

        conn = WeChatConnector(MagicMock())
        with patch("wechat_direct.wechat_connector.requests.get") as mock_get:
            mock_get.return_value = MagicMock(content=_PNG_BYTES, raise_for_status=lambda: None)
            conn._download_media(self._ref(full_url="https://cdn.example.com/dl?x=1", aes_key=None))
        assert mock_get.call_args[0][0] == "https://cdn.example.com/dl?x=1", "服务端下发 full_url 优先"

    def test_plain_download_without_key(self):
        from wechat_direct.wechat_connector import WeChatConnector

        conn = WeChatConnector(MagicMock())
        with patch("wechat_direct.wechat_connector.requests.get") as mock_get:
            mock_get.return_value = MagicMock(content=_PNG_BYTES, raise_for_status=lambda: None)
            raw = conn._download_media(self._ref(aes_key=None))
        assert raw == _PNG_BYTES, "无 aes_key 走明文下载兜底"

    def test_timeout_returns_none(self):
        import requests as requests_mod

        from wechat_direct.wechat_connector import WeChatConnector

        conn = WeChatConnector(MagicMock())
        with patch("wechat_direct.wechat_connector.requests.get",
                   side_effect=requests_mod.exceptions.Timeout()):
            assert conn._download_media(self._ref()) is None

    def test_empty_ref_returns_none(self):
        from wechat_direct.wechat_connector import WeChatConnector

        conn = WeChatConnector(MagicMock())
        assert conn._download_media(self._ref(encrypt_query_param="", full_url="")) is None


# ═══════════════════════════════════════════════
# 四、端到端：语音 item type=3（经 _handle_message）
# ═══════════════════════════════════════════════
class TestVoiceMessageNewProtocol:
    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_text_routed_as_text(self, mock_call, mock_send, caplog):
        """① type=3 + text 非空 → 转写直接作正文走主链，打 voice_text 日志。"""
        mock_call.return_value = {"reply": "好的"}
        conn = _make_conn()
        raw_msg = {
            "message_type": 1,
            "message_id": "v_new_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_voice_item(text=_TEXT_SAMPLE)],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert mock_call.call_count == 1, "服务端 ASR 文本必须进主链"
        assert mock_call.call_args[0][2] == _TEXT_SAMPLE
        assert "step=voice_text" in caplog.text, "必须打 [wx][step=voice_text] 日志"
        assert _TEXT_SAMPLE not in caplog.text, "隐私红线：转写原文不得落日志"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_no_text_asr_disabled_notice(self, mock_call, mock_send, caplog):
        """② type=3 + text 空 + 本地 ASR 未启用 → voice_unavailable 日志 +
        受控话术告知（不再静默丢），不进主链。"""
        conn = _make_conn()
        conn._voice_asr_available = MagicMock(return_value=False)
        raw_msg = {
            "message_type": 1,
            "message_id": "v_new_2",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_voice_item(text="")],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=voice_unavailable" in caplog.text
        assert mock_call.call_count == 0, "转写不可得时不得拿空文本进 LLM 主链"
        assert mock_send.call_count == 1, "语音不可得必须回受控话术，不能静默丢消息"
        args = mock_send.call_args
        text = args.kwargs.get("text", "") or args[0][1]
        from utils.fallback_lines import is_system_fallback_line

        assert is_system_fallback_line(text), "话术必须来自 fallback_lines 池"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_no_text_downloads_and_transcribes(self, mock_call, mock_send):
        """② 补：ASR 可用 + 下载成功 → 下载 SILK 字节喂本地 ASR，转写结果进主链。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._voice_asr_available = MagicMock(return_value=True)
        conn._download_media = MagicMock(return_value=b"fake-silk-bytes")
        conn._transcribe_voice = MagicMock(return_value="本地转写结果")
        raw_msg = {
            "message_type": 1,
            "message_id": "v_new_3",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_voice_item(text="")],
        }
        conn._handle_message(raw_msg)

        assert conn._download_media.call_count == 1
        conn._transcribe_voice.assert_called_once_with(
            base64.b64encode(b"fake-silk-bytes").decode("ascii")
        )
        assert mock_call.call_count == 1
        assert mock_call.call_args[0][2] == "本地转写结果"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_voice_download_failed_skipped(self, mock_call, mock_send, caplog):
        """② 补：下载失败 → voice_unavailable 日志，不炸主流程、不进主链。"""
        conn = _make_conn()
        conn._voice_asr_available = MagicMock(return_value=True)
        conn._download_media = MagicMock(return_value=None)
        raw_msg = {
            "message_type": 1,
            "message_id": "v_new_4",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_voice_item(text="")],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=voice_unavailable" in caplog.text
        assert mock_call.call_count == 0


# ═══════════════════════════════════════════════
# 五、端到端：图片 item type=2
# ═══════════════════════════════════════════════
class TestImageMessageNewProtocol:
    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_downloaded_and_attached(self, mock_call, mock_send):
        """③ type=2 → 下载成功 → base64 后走既有附件直传链。"""
        mock_call.return_value = {"reply": "图看到了"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_BYTES)
        raw_msg = {
            "message_type": 1,
            "message_id": "img_new_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        }
        conn._handle_message(raw_msg)

        assert mock_call.call_count == 1, "下载成功的图片必须进主链"
        attachments = mock_call.call_args[0][3]
        assert attachments, "多模态直传模式必须携带附件"
        url = attachments[0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == _PNG_BYTES

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_download_failed_no_text_fallback_line(self, mock_call, mock_send, caplog):
        """③ 补：下载失败 + 无文本 → INFO 日志 + fallback_lines 固定话术，不静默。"""
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=None)
        raw_msg = {
            "message_type": 1,
            "message_id": "img_new_2",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=image_download_failed" in caplog.text
        assert mock_call.call_count == 0, "图片不可用且无文本不应拿空文本进主链"
        assert mock_send.call_count == 1, "无文本时必须回固定话术，不能静默丢消息"
        args = mock_send.call_args
        text = args.kwargs.get("text", "") or args[0][1]
        from utils.fallback_lines import is_system_fallback_line

        assert is_system_fallback_line(text), "话术必须来自 fallback_lines 池（可被记忆管线识别为系统占位）"
        assert "（" not in text and "(" not in text, "沉浸式约束：不得带括号"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_mode_off_fallback_line(self, mock_call, mock_send, caplog):
        """③ 补：mode=off → image_unsupported 日志 + 固定话术（不受控降级为静默）。"""
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("off", "", 5 * 1024 * 1024))
        conn._download_media = MagicMock()
        raw_msg = {
            "message_type": 1,
            "message_id": "img_new_3",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=image_unsupported" in caplog.text
        assert conn._download_media.call_count == 0, "mode=off 不应发起下载"
        assert mock_send.call_count == 1

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_image_download_failed_with_caption_routes_text(self, mock_call, mock_send, caplog):
        """③ 补：图片 + 文本同发、下载失败 → 文本进主链，不触发图片降级话术分支。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=None)
        raw_msg = {
            "message_type": 1,
            "message_id": "img_new_4",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item(), {"type": 1, "text_item": {"text": _TEXT_SAMPLE}}],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert mock_call.call_count == 1
        assert mock_call.call_args[0][2] == _TEXT_SAMPLE
        assert "step=image_download_failed" in caplog.text
        assert "step=image_unsupported" not in caplog.text, \
            "有文本可用时不进入图片不可用降级分支（mock_call 未消费 reply_sender 时 publish 仍会发一次回复，故不断言 send_count）"


# ═══════════════════════════════════════════════
# 五·补：图片回合终局守卫（附件/正文双空不得进 LLM 主链）
# ═══════════════════════════════════════════════
_PNG_6MB = b"\x89PNG\r\n\x1a\n" + b"A" * (6 * 1024 * 1024)            # b64≈8MB，原始字节也超 5MB 上限
_PNG_4_5MB = b"\x89PNG\r\n\x1a\n" + b"A" * (4 * 1024 * 1024 + 512 * 1024)  # 原始 4.5MB 不超限，b64≈6MB 超限


class TestImageTurnFinalGuard:
    """图片回合终局守卫（2026-10-12 多模态域批）。

    旧码三处漏：① 超限判定用 base64 串长（4/3 原始大小）→ 4.5MB 图被误判超限丢附件；
    ② 超限/损坏图 + 有配文时 mode=auto 仍白烧一次 describe LLM 调用；
    ③ A/B 尝试后（损坏 base64/describe 失败残留）attachments 与 text 双空仍放行
    空正文进 LLM 主链。
    """

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_oversized_raw_bytes_no_caption_notice(self, mock_call, mock_send):
        """① mode=direct + 下载成功但原始字节超限 + 无配文 → 回降级话术，不进主链。"""
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_6MB)
        raw_msg = {
            "message_type": 1,
            "message_id": "img_guard_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        }
        conn._handle_message(raw_msg)

        assert mock_call.call_count == 0, "超限图 + 无配文不得进 LLM 主链"
        assert mock_send.call_count == 1, "必须回受控降级话术"
        args = mock_send.call_args
        text = args.kwargs.get("text", "") or args[0][1]
        from utils.fallback_lines import is_system_fallback_line

        assert is_system_fallback_line(text), "话术必须来自 fallback_lines 池"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_within_limit_judged_by_raw_bytes_not_b64_len(self, mock_call, mock_send):
        """② 4.5MB 原始图（b64≈6MB>5MB）→ 按原始字节判定不超限，直传附件
        非空且 data URL 解码回原字节；旧码按 b64 串长误判超限丢附件。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_4_5MB)
        raw_msg = {
            "message_type": 1,
            "message_id": "img_guard_2",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        }
        conn._handle_message(raw_msg)

        assert mock_call.call_count == 1
        attachments = mock_call.call_args[0][3]
        assert attachments, "4.5MB 原始图未超 5MB 上限，必须直传"
        url = attachments[0]["image_url"]["url"]
        assert base64.b64decode(url.split(",", 1)[1]) == _PNG_4_5MB

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_oversized_with_caption_skips_describe(self, mock_call, mock_send):
        """③ mode=auto + 超限图 + 有配文 → 不白烧 describe LLM 调用，配文照常进主链。"""
        mock_call.return_value = {"reply": "好"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("auto", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock(return_value=_PNG_6MB)
        conn._describe_image = MagicMock(return_value="图中描述")
        raw_msg = {
            "message_type": 1,
            "message_id": "img_guard_3",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item(), {"type": 1, "text_item": {"text": _TEXT_SAMPLE}}],
        }
        conn._handle_message(raw_msg)

        assert conn._describe_image.call_count == 0, "超限图不值得一次 describe LLM 调用"
        assert mock_call.call_count == 1
        assert mock_call.call_args[0][2] == _TEXT_SAMPLE, "配文照常进主链"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_corrupt_b64_no_caption_notice(self, mock_call, mock_send):
        """④（终局守卫）损坏 base64 + 无配文 → 旧码空正文进主链；新码回降级话术。"""
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        raw_msg = {
            "message_type": 3,
            "message_id": "img_guard_4",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 3, "image_item": {"image_data": "!!!corrupt!!!"}}],
        }
        conn._handle_message(raw_msg)

        assert mock_call.call_count == 0, "损坏图 + 无配文不得拿空正文进 LLM 主链"
        assert mock_send.call_count == 1, "必须回受控降级话术"


# ═══════════════════════════════════════════════
# 五·补：媒体不可用受控话术池（voice_unsupported）
# ═══════════════════════════════════════════════
class TestMediaUnsupportedPools:
    """voice_unsupported 池：无括号动作（沉浸式硬约束）+ 当日同 kind 去重。

    断言模式复用 tests/test_abc_fallback_lines.py。
    """

    def test_voice_unsupported_pool_immersive_and_dedup(self):
        from utils.fallback_lines import get_fallback_line, reset_daily_dedup
        from utils.reply_mode import REPLY_MODE_IMMERSIVE

        reset_daily_dedup()
        lines = [
            get_fallback_line(None, "voice_unsupported", REPLY_MODE_IMMERSIVE)
            for _ in range(2)
        ]
        for line in lines:
            assert line, "voice_unsupported 池不可为空"
            assert ("语音" in line) or ("没听清" in line), \
                "必须来自 voice_unsupported 专属池（kind 未注册会误落 empty_reply 池）"
            assert "（" not in line and "(" not in line, "沉浸式约束：不得带括号动作"
        assert lines[0] != lines[1], "当日同 kind 必须去重（池至少 2 条）"

    def test_voice_unsupported_recognized_as_system_line(self):
        from utils.fallback_lines import get_fallback_line, is_system_fallback_line

        line = get_fallback_line(None, "voice_unsupported", "immersive")
        assert ("语音" in line) or ("没听清" in line), \
            "必须来自 voice_unsupported 专属池"
        assert is_system_fallback_line(line), "池话术必须可被记忆管线识别为系统占位"


# ═══════════════════════════════════════════════
# 六、可观测性与旧分支兼容
# ═══════════════════════════════════════════════
class TestObservabilityAndLegacy:
    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_unknown_msg_type_logged(self, mock_call, mock_send, caplog):
        """④ 未知 message_type（bot 回显=2 等）→ INFO 日志（含 msg_type 值），不再静默。"""
        conn = _make_conn()
        raw_msg = {
            "message_type": 2,
            "message_id": "echo_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 1, "text_item": {"text": "自发回显"}}],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=msg_type_skipped" in caplog.text
        assert "msg_type=2" in caplog.text
        assert mock_call.call_count == 0 and mock_send.call_count == 0

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_unknown_item_type_logged(self, mock_call, mock_send, caplog):
        """④ 未知 item type（如 47 表情）→ INFO 日志，不再静默丢。"""
        conn = _make_conn()
        raw_msg = {
            "message_type": 1,
            "message_id": "emoji_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 47, "emoji_item": {"md5": "x"}}],
        }
        with caplog.at_level(logging.INFO, logger="wechat_direct"):
            conn._handle_message(raw_msg)

        assert "step=media_unknown_type" in caplog.text
        assert "47" in caplog.text

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_legacy_voice_34_still_works(self, mock_call, mock_send):
        """⑤ 旧 type=34 语音分支保留：ASR 转写后进主链，不走 CDN 下载。"""
        mock_call.return_value = {"reply": "收到"}
        conn = _make_conn()
        conn._download_media = MagicMock()
        conn._transcribe_voice = MagicMock(return_value="旧语音转写")
        raw_msg = {
            "message_type": 34,
            "message_id": "v_old_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [{"type": 34, "voice_item": {"voice_data": "ZmFrZV9zaWxr"}}],
        }
        conn._handle_message(raw_msg)

        assert conn._transcribe_voice.call_count == 1
        assert conn._download_media.call_count == 0, "旧内联 base64 分支不应触发 CDN 下载"
        assert mock_call.call_count == 1
        assert mock_call.call_args[0][2] == "旧语音转写"

    @patch("wechat_direct.wechat_connector._send_text")
    @patch("wechat_direct.wechat_connector._call_user_manager")
    def test_legacy_type3_image_still_works(self, mock_call, mock_send):
        """⑤ 旧 type=3 图片分支保留：内联 base64 走附件链，不走 CDN 下载。"""
        mock_call.return_value = {"reply": "看到图了"}
        conn = _make_conn()
        conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
        conn._download_media = MagicMock()
        raw_msg = {
            "message_type": 3,
            "message_id": "img_old_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [
                {"type": 3, "image_item": {"image_data": base64.b64encode(_PNG_BYTES).decode()}}
            ],
        }
        conn._handle_message(raw_msg)

        assert conn._download_media.call_count == 0, "旧内联 base64 分支不应触发 CDN 下载"
        assert mock_call.call_count == 1
        attachments = mock_call.call_args[0][3]
        assert attachments, "旧协议内联图片仍走附件直传"
