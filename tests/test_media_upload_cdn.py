"""出站媒体 CDN 上传契约测试（2026-10-30 出站媒体批）。

锁死三件事：
1. AES-128-ECB + PKCS7 与入站解密互为逆运算（同一把 key）；
2. ``upload_bytes`` 的时序与参数（getuploadurl → POST 密文 → 取 x-encrypted-param）；
3. ``_send_voice_message`` 的 CDN 分支产出 item type=3 + media 引用，
   且上传失败时必须回落旧协议（不得把语音整条吞掉）。
"""

import base64

import pytest

from wechat_direct import media_upload, wechat_connector


class TestAesRoundTrip:
    def test_padded_size_is_next_block(self):
        assert media_upload.aes_ecb_padded_size(0) == 16
        assert media_upload.aes_ecb_padded_size(15) == 16
        assert media_upload.aes_ecb_padded_size(16) == 32
        assert media_upload.aes_ecb_padded_size(17) == 32

    def test_encrypt_is_inverse_of_inbound_decrypt(self):
        key = bytes(range(16))
        plaintext = b"#!SILK_V3" + b"\x00" * 100
        ciphertext = media_upload.encrypt_aes_ecb(plaintext, key)
        assert len(ciphertext) == media_upload.aes_ecb_padded_size(len(plaintext))
        # 入站解密器在 connector 里（消费者视角验证，不是自证）
        assert wechat_connector._aes_ecb_decrypt(ciphertext, key) == plaintext

    def test_rejects_wrong_key_size(self):
        with pytest.raises(ValueError):
            media_upload.encrypt_aes_ecb(b"x", b"short")


class TestUploadBytes:
    def test_full_timeline(self, monkeypatch):
        calls = []

        def fake_post_api(endpoint, body, token="", base_url=""):
            calls.append((endpoint, body))
            return {"upload_full_url": "https://cdn.example/upload?x=1"}

        posted = {}

        class FakeResponse:
            status_code = 200
            headers = {"x-encrypted-param": "DOWNLOAD_PARAM"}
            text = ""

        def fake_post(url, data=None, headers=None, timeout=None):
            posted.update(url=url, data=data, headers=headers)
            return FakeResponse()

        monkeypatch.setattr(media_upload.requests, "post", fake_post)

        uploaded = media_upload.upload_bytes(
            b"hello-voice",
            media_type=media_upload.MEDIA_TYPE_VOICE,
            to_user_id="wxid_test",
            post_api=fake_post_api,
            token="tok",
        )

        assert calls[0][0] == "ilink/bot/getuploadurl"
        body = calls[0][1]
        assert body["media_type"] == media_upload.MEDIA_TYPE_VOICE
        assert body["to_user_id"] == "wxid_test"
        assert body["rawsize"] == len(b"hello-voice")
        assert body["filesize"] == media_upload.aes_ecb_padded_size(len(b"hello-voice"))
        assert body["no_need_thumb"] is True
        assert len(bytes.fromhex(body["aeskey"])) == 16

        assert posted["url"] == "https://cdn.example/upload?x=1"
        assert posted["headers"]["Content-Type"] == "application/octet-stream"
        # 上传的是密文，不是明文
        assert posted["data"] != b"hello-voice"
        assert wechat_connector._aes_ecb_decrypt(
            posted["data"], bytes.fromhex(body["aeskey"])
        ) == b"hello-voice"

        assert uploaded.download_encrypted_query_param == "DOWNLOAD_PARAM"
        assert uploaded.file_size == len(b"hello-voice")

    def test_builds_upload_url_when_full_url_missing(self, monkeypatch):
        seen = {}

        def fake_post_api(endpoint, body, token="", base_url=""):
            return {"upload_param": "PARAM"}

        class FakeResponse:
            status_code = 200
            headers = {"x-encrypted-param": "D"}
            text = ""

        def fake_post(url, data=None, headers=None, timeout=None):
            seen["url"] = url
            return FakeResponse()

        monkeypatch.setattr(media_upload.requests, "post", fake_post)
        media_upload.upload_bytes(
            b"abc",
            media_type=media_upload.MEDIA_TYPE_VOICE,
            to_user_id="u",
            post_api=fake_post_api,
            cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        )
        assert seen["url"].startswith("https://novac2c.cdn.weixin.qq.com/c2c/upload?")
        assert "encrypted_query_param=PARAM" in seen["url"]
        assert "filekey=" in seen["url"]

    def test_raises_when_no_upload_url(self, monkeypatch):
        monkeypatch.setattr(media_upload.requests, "post", lambda *a, **k: None)
        with pytest.raises(RuntimeError):
            media_upload.upload_bytes(
                b"abc",
                media_type=media_upload.MEDIA_TYPE_VOICE,
                to_user_id="u",
                post_api=lambda *a, **k: {"ret": 0},
            )


class TestItemShape:
    def _uploaded(self):
        return media_upload.UploadedMedia(
            filekey="fk",
            download_encrypted_query_param="DL",
            aeskey_hex="ab" * 16,
            file_size=100,
            file_size_ciphertext=112,
        )

    def test_voice_item(self):
        item = media_upload.voice_item(self._uploaded(), voice_format="silk", playtime_ms=3000)
        assert item["type"] == media_upload.ITEM_TYPE_VOICE == 3
        voice = item["voice_item"]
        assert voice["media"]["encrypt_query_param"] == "DL"
        assert voice["media"]["encrypt_type"] == 1
        # aes_key 必须是 base64(hex 明文)，入站 _parse_aes_key 能识别
        assert wechat_connector._parse_aes_key(voice["media"]["aes_key"]) == bytes.fromhex("ab" * 16)
        assert voice["encode_type"] == 6  # SILK
        assert voice["sample_rate"] == 24000
        assert voice["playtime"] == 3000
        assert voice["bits_per_sample"] == 16

    def test_voice_item_amr(self):
        item = media_upload.voice_item(self._uploaded(), voice_format="amr", playtime_ms=1000)
        assert item["voice_item"]["encode_type"] == 5
        assert item["voice_item"]["sample_rate"] == 8000

    def test_image_item(self):
        item = media_upload.image_item(self._uploaded())
        assert item["type"] == media_upload.ITEM_TYPE_IMAGE
        assert item["image_item"]["mid_size"] == 112

    def test_build_message(self):
        msg = media_upload.build_message(
            to_user_id="u", item={"type": 3}, context_token="ctx", client_id="cid"
        )
        assert msg["message_type"] == 2
        assert msg["to_user_id"] == "u"
        assert msg["context_token"] == "ctx"
        assert msg["item_list"] == [{"type": 3}]


class TestSendVoiceMessage:
    def test_cdn_path_produces_media_item(self, monkeypatch):
        uploaded = media_upload.UploadedMedia(
            filekey="fk",
            download_encrypted_query_param="DL",
            aeskey_hex="cd" * 16,
            file_size=8,
            file_size_ciphertext=16,
        )
        monkeypatch.setattr(media_upload, "upload_bytes", lambda *a, **k: uploaded)

        seen = {}

        def fake_post_api(endpoint, body, token="", base_url=""):
            seen["endpoint"] = endpoint
            seen["body"] = body
            return {"ret": 0}

        monkeypatch.setattr(wechat_connector, "_post_api", fake_post_api)

        result = wechat_connector._send_voice_message(
            "wxid_u", base64.b64encode(b"silkdata").decode(), 3000, "ctx", token="t"
        )

        assert result == {"ret": 0}
        assert seen["endpoint"] == "ilink/bot/sendmessage"
        msg = seen["body"]["msg"]
        assert msg["message_type"] == 2
        assert msg["item_list"][0]["type"] == 3
        assert msg["item_list"][0]["voice_item"]["media"]["encrypt_query_param"] == "DL"

    def test_upload_failure_falls_back_to_legacy(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("cdn down")

        monkeypatch.setattr(media_upload, "upload_bytes", boom)

        seen = {}

        def fake_post_api(endpoint, body, token="", base_url=""):
            seen["body"] = body
            return {"ret": 0}

        monkeypatch.setattr(wechat_connector, "_post_api", fake_post_api)

        wechat_connector._send_voice_message(
            "wxid_u", base64.b64encode(b"silkdata").decode(), 3000, "ctx"
        )

        msg = seen["body"]["msg"]
        # 回落旧协议：不能因为上传失败就把语音整条吞掉
        assert msg["message_type"] == 34
        assert msg["item_list"][0]["type"] == 34
        assert msg["item_list"][0]["voice_item"]["voice_data"]

    def test_send_failure_does_not_fall_back_to_legacy(self, monkeypatch):
        """上传成功但 sendmessage 抛错时，必须向上抛，不能再发一遍内联（重复投递）。"""
        uploaded = media_upload.UploadedMedia(
            filekey="fk",
            download_encrypted_query_param="DL",
            aeskey_hex="cd" * 16,
            file_size=8,
            file_size_ciphertext=16,
        )
        monkeypatch.setattr(media_upload, "upload_bytes", lambda *a, **k: uploaded)

        sent = []

        def fake_post_api(endpoint, body, token="", base_url=""):
            sent.append(body["msg"]["message_type"])
            raise RuntimeError("send failed")

        monkeypatch.setattr(wechat_connector, "_post_api", fake_post_api)

        with pytest.raises(RuntimeError):
            wechat_connector._send_voice_message(
                "wxid_u", base64.b64encode(b"silkdata").decode(), 3000, "ctx"
            )
        assert sent == [2], "只允许尝试一次 CDN 发送，不得回落成第二次投递"

    def test_upload_budget_is_bounded(self, monkeypatch):
        """本方法跑在 2 worker 的消息池里，上传预算必须显式收紧到 20s。"""
        uploaded = media_upload.UploadedMedia(
            filekey="fk",
            download_encrypted_query_param="DL",
            aeskey_hex="cd" * 16,
            file_size=8,
            file_size_ciphertext=16,
        )
        seen_kwargs = {}

        def fake_upload(*a, **k):
            seen_kwargs.update(k)
            return uploaded

        monkeypatch.setattr(media_upload, "upload_bytes", fake_upload)
        monkeypatch.setattr(wechat_connector, "_post_api", lambda *a, **k: {"ret": 0})

        wechat_connector._send_voice_message(
            "wxid_u", base64.b64encode(b"silkdata").decode(), 3000, "ctx"
        )
        assert seen_kwargs.get("timeout") == 20.0
        assert media_upload.UPLOAD_MAX_RETRIES == 2
