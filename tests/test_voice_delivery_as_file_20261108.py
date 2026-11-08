"""语音投递形态批（2026-11-08）：原生语音条被平台静默丢弃 → 音频文件卡片。

背景（真机实证）：微信 iLink 平台对 bot 方向的 ``item.type=3``（VOICE）
返回 HTTP 200 + ``message_id``，客户端却不渲染——三种编码形态
（silk 24k encode 6、silk 16k encode 4/6）全部无气泡；同批 ``type=4``
（FILE）文件卡片可正常到达。因此默认投递形态改为文件卡片，
``WECHAT_NATIVE_VOICE_BUBBLE=1`` 才回到原生气泡。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from wechat_direct import media_upload, wechat_connector


def _uploaded() -> media_upload.UploadedMedia:
    return media_upload.UploadedMedia(
        filekey="k" * 32,
        download_encrypted_query_param="x-encrypted-param-value",
        aeskey_hex="0f" * 16,
        file_size=1234,
        file_size_ciphertext=1248,
    )


def _conn() -> wechat_connector.WeChatConnector:
    conn = wechat_connector.WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    return conn


# ── item 构造 ────────────────────────────────────────────


def test_file_item_uses_cdn_download_param_not_upload_param():
    """回归钉：媒体引用必须是上传响应头 ``x-encrypted-param``（客户端认它）。

    2026-11-08 探针曾误判为 ``getuploadurl.upload_param``——那只对我们服务端
    GET 有效，客户端取不到（真机对照：用它的语音/文件都没有出现）。
    """
    item = media_upload.file_item(_uploaded(), "voice.mp3")
    assert item["type"] == media_upload.ITEM_TYPE_FILE == 4
    body = item["file_item"]
    assert body["media"]["encrypt_query_param"] == "x-encrypted-param-value"
    assert body["media"]["encrypt_type"] == 1
    assert body["file_name"] == "voice.mp3"
    assert body["len"] == "1234"


def test_file_item_defaults_filename():
    assert media_upload.file_item(_uploaded(), "")["file_item"]["file_name"] == "voice.mp3"


# ── 形态决策 ─────────────────────────────────────────────


def test_send_voice_clip_defaults_to_audio_file(monkeypatch):
    """默认（未设开关）必须走文件卡片，且不触发 silk 转码。"""
    monkeypatch.delenv(wechat_connector.NATIVE_VOICE_BUBBLE_ENV, raising=False)
    conn = _conn()
    conn.send_audio_file = MagicMock(return_value=True)
    conn.send_voice = MagicMock(return_value=True)

    assert conn.send_voice_clip(b"mp3-bytes", "mp3", to_user="wx_user_1") is True

    conn.send_audio_file.assert_called_once()
    assert conn.send_audio_file.call_args.kwargs["fmt"] == "mp3"
    conn.send_voice.assert_not_called()


def test_send_voice_clip_native_flag_converts_to_silk(monkeypatch):
    """``WECHAT_NATIVE_VOICE_BUBBLE=1`` 才走原生气泡（silk 转码 + send_voice）。"""
    monkeypatch.setenv(wechat_connector.NATIVE_VOICE_BUBBLE_ENV, "1")
    from voice.audio_converter import AudioFormatConverter

    monkeypatch.setattr(
        AudioFormatConverter, "to_silk", lambda self, data, fmt: b"silk-bytes",
    )
    conn = _conn()
    conn.send_audio_file = MagicMock(return_value=True)
    conn.send_voice = MagicMock(return_value=True)

    assert conn.send_voice_clip(b"mp3-bytes", "mp3", to_user="wx_user_1") is True

    conn.send_voice.assert_called_once()
    assert conn.send_voice.call_args.args[0] == b"silk-bytes"
    conn.send_audio_file.assert_not_called()


def test_send_voice_clip_empty_audio_is_noop():
    conn = _conn()
    conn.send_audio_file = MagicMock(return_value=True)
    conn.send_voice = MagicMock(return_value=True)
    assert conn.send_voice_clip(b"", "mp3", to_user="wx_user_1") is False
    conn.send_audio_file.assert_not_called()
    conn.send_voice.assert_not_called()


# ── 无实例入口（主动线 / 脚本） ──────────────────────────


def test_send_voice_clip_to_uses_file_message_by_default(monkeypatch):
    monkeypatch.delenv(wechat_connector.NATIVE_VOICE_BUBBLE_ENV, raising=False)
    file_calls, voice_calls = [], []
    monkeypatch.setattr(
        wechat_connector, "_send_audio_file_message",
        lambda **kw: file_calls.append(kw) or {"message_id": 1},
    )
    monkeypatch.setattr(
        wechat_connector, "_send_voice_message",
        lambda **kw: voice_calls.append(kw) or {"message_id": 1},
    )

    ok, err = wechat_connector.send_voice_clip_to(
        "wx_user_1", b"mp3-bytes", "mp3", context_token="ctx", token="t",
    )

    assert (ok, err) == (True, "")
    assert len(file_calls) == 1 and not voice_calls
    assert file_calls[0]["file_name"] == "voice.mp3"


def test_send_voice_clip_to_native_flag_uses_voice_message(monkeypatch):
    monkeypatch.setenv(wechat_connector.NATIVE_VOICE_BUBBLE_ENV, "1")
    from voice.audio_converter import AudioFormatConverter

    monkeypatch.setattr(
        AudioFormatConverter, "to_silk", lambda self, data, fmt: b"silk-bytes",
    )
    file_calls, voice_calls = [], []
    monkeypatch.setattr(
        wechat_connector, "_send_audio_file_message",
        lambda **kw: file_calls.append(kw) or {"message_id": 1},
    )
    monkeypatch.setattr(
        wechat_connector, "_send_voice_message",
        lambda **kw: voice_calls.append(kw) or {"message_id": 1},
    )

    ok, _ = wechat_connector.send_voice_clip_to(
        "wx_user_1", b"mp3-bytes", "mp3", context_token="ctx", token="t",
    )

    assert ok is True
    assert len(voice_calls) == 1 and not file_calls


def test_send_voice_clip_to_reports_empty_audio():
    ok, err = wechat_connector.send_voice_clip_to("wx_user_1", b"")
    assert ok is False and err == "empty_audio"


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("ON", True), ("yes", True),
    ("0", False), ("", False), ("off", False),
])
def test_native_voice_bubble_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv(wechat_connector.NATIVE_VOICE_BUBBLE_ENV, val)
    assert wechat_connector.native_voice_bubble_enabled() is expected
