"""iLink CDN 出站媒体上传（2026-10-30 出站媒体批）。

背景：iLink 新协议下，bot 方向发送语音/图片不能把字节内联进 ``voice_data`` /
``image_data``（服务端静默丢弃，见 ``LOG.md`` 2026-10-12 遗留登记），必须先走
CDN 上传，再在 ``item_list`` 里引用 ``media``。

协议真源（MIT，逐行对照，未抄代码只对齐语义）：
  zongrongjin/weixin-ilink ``src/weixin_ilink/cdn.py`` + ``media.py`` + ``client.py``

时序：
  1. ``POST ilink/bot/getuploadurl``  携带 filekey/rawsize/rawfilemd5/filesize/aeskey
  2. AES-128-ECB + PKCS7 加密明文，``POST`` 密文到 ``upload_full_url``
     （缺失时拼 ``{cdn_base}/upload?encrypted_query_param=&filekey=``）
  3. 从响应头 ``x-encrypted-param`` 取回下载用的 ``encrypt_query_param``
  4. 组装 ``item_list``：``voice_item`` / ``image_item`` 携带
     ``media={encrypt_query_param, aes_key, encrypt_type:1}``

本模块与 ``wechat_connector._aes_ecb_decrypt``（入站）互为逆运算，密钥形态保持
一致：``aes_key`` 字段 = base64(32 字符 hex 明文)，入站 ``_parse_aes_key`` 可识别。

设计约束：
- **不拥有 HTTP 客户端**：``post_api`` 由调用方注入（``wechat_connector._post_api``），
  保持「iLink API 唯一出口」不变，也避免循环 import。
- 纯函数 + 单一副作用（一次上传），可单测。
"""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

# iLink CDN 基址（与入站下载共用；入站常量在 wechat_connector，此处独立以免循环依赖）
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

UPLOAD_MAX_RETRIES = 2

# getuploadurl.media_type
MEDIA_TYPE_IMAGE = 1
MEDIA_TYPE_VIDEO = 2
MEDIA_TYPE_FILE = 3
MEDIA_TYPE_VOICE = 4

# item_list[].type
ITEM_TYPE_TEXT = 1
ITEM_TYPE_IMAGE = 2
ITEM_TYPE_VOICE = 3
ITEM_TYPE_FILE = 4
ITEM_TYPE_VIDEO = 5

# message_type（顶层）
MESSAGE_TYPE_BOT = 2

# voice_item.encode_type
VOICE_ENCODE_TYPES = {
    "pcm": 1,
    "adpcm": 2,
    "feature": 3,
    "speex": 4,
    "amr": 5,
    "silk": 6,
    "mp3": 7,
}

# 各语音格式的默认采样率（silk 走微信标准 24k）
VOICE_SAMPLE_RATES = {"silk": 24_000, "amr": 8_000, "mp3": 24_000}


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """PKCS7 补齐到 16 字节块后的密文长度（一定比原文长 1~16 字节）。"""
    return math.ceil((plaintext_size + 1) / 16) * 16


def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7（``cryptography`` 实现，与入站解密同族）。"""
    if len(key) != 16:
        raise ValueError(f"aes key 必须是 16 字节，得到 {len(key)}")
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_key_field(aeskey_hex: str) -> str:
    """``media.aes_key`` 官方形态 = base64(32 字符 hex 明文)。"""
    return base64.b64encode(aeskey_hex.encode("ascii")).decode("ascii")


def build_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    from urllib.parse import quote

    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


@dataclass(frozen=True)
class UploadedMedia:
    """一次上传的结果，直接喂给 item 构造器。"""

    filekey: str
    download_encrypted_query_param: str
    aeskey_hex: str
    file_size: int
    file_size_ciphertext: int


def _pick(resp: dict, key: str) -> str:
    """getuploadurl 的返回可能是扁平结构，也可能包一层 data，两种都认。"""
    value = resp.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    data = resp.get("data")
    if isinstance(data, dict):
        nested = data.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def _upload_ciphertext(
    url: str,
    ciphertext: bytes,
    *,
    timeout: float,
) -> str:
    """把密文 POST 到 CDN，返回 ``x-encrypted-param``。4xx 不重试，5xx/网络错误重试。"""
    last_error: Exception | None = None
    for _ in range(UPLOAD_MAX_RETRIES):
        try:
            response = requests.post(
                url,
                data=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
                timeout=timeout,
            )
            if 400 <= response.status_code < 500:
                message = response.headers.get("x-error-message") or response.text
                raise RuntimeError(f"CDN 上传被拒 {response.status_code}: {message}")
            if response.status_code != 200:
                last_error = RuntimeError(
                    f"CDN 上传失败 status={response.status_code}: "
                    f"{response.headers.get('x-error-message') or ''}"
                )
                continue
            download_param = response.headers.get("x-encrypted-param")
            if not download_param:
                last_error = RuntimeError("CDN 上传响应缺少 x-encrypted-param 头")
                continue
            return download_param
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001 - 网络层异常统一重试
            last_error = error
    raise last_error or RuntimeError("CDN 上传失败（无更多信息）")


def upload_bytes(
    data: bytes,
    *,
    media_type: int,
    to_user_id: str,
    post_api: Callable[..., dict],
    token: str = "",
    base_url: str = "",
    cdn_base_url: str = DEFAULT_CDN_BASE_URL,
    timeout: float = 60.0,
) -> UploadedMedia:
    """完整上传时序：getuploadurl → 加密 → POST 密文 → 返回引用信息。

    ``post_api`` 形如 ``wechat_connector._post_api(endpoint, body, token=, base_url=)``。
    """
    if not data:
        raise ValueError("上传内容为空")
    raw_size = len(data)
    filekey = secrets.token_hex(16)
    aeskey = secrets.token_bytes(16)

    response = post_api(
        "ilink/bot/getuploadurl",
        {
            "filekey": filekey,
            "media_type": int(media_type),
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": hashlib.md5(data).hexdigest(),
            "filesize": aes_ecb_padded_size(raw_size),
            "no_need_thumb": True,
            "aeskey": aeskey.hex(),
        },
        token=token,
        base_url=base_url,
    )
    if not isinstance(response, dict):
        raise RuntimeError(f"getuploadurl 返回非预期类型: {type(response).__name__}")

    upload_full_url = _pick(response, "upload_full_url")
    upload_param = _pick(response, "upload_param")
    if upload_full_url:
        url = upload_full_url
    elif upload_param:
        url = build_upload_url(cdn_base_url, upload_param, filekey)
    else:
        raise RuntimeError(f"getuploadurl 未返回上传地址: {response}")

    download_param = _upload_ciphertext(
        url, encrypt_aes_ecb(data, aeskey), timeout=timeout
    )
    return UploadedMedia(
        filekey=filekey,
        download_encrypted_query_param=download_param,
        aeskey_hex=aeskey.hex(),
        file_size=raw_size,
        file_size_ciphertext=aes_ecb_padded_size(raw_size),
    )


def voice_item(
    uploaded: UploadedMedia,
    *,
    voice_format: str = "silk",
    playtime_ms: int = 0,
    sample_rate: int = 0,
    bits_per_sample: int = 16,
) -> dict[str, Any]:
    """构造 ``item_list`` 里的 voice_item（type=3 + media 引用）。"""
    fmt = (voice_format or "silk").lower()
    encode_type = VOICE_ENCODE_TYPES.get(fmt, VOICE_ENCODE_TYPES["silk"])
    rate = sample_rate or VOICE_SAMPLE_RATES.get(fmt, 24_000)
    return {
        "type": ITEM_TYPE_VOICE,
        "voice_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": _aes_key_field(uploaded.aeskey_hex),
                "encrypt_type": 1,
            },
            "encode_type": encode_type,
            "bits_per_sample": bits_per_sample,
            "sample_rate": rate,
            "playtime": max(0, int(playtime_ms)),
        },
    }


def image_item(uploaded: UploadedMedia) -> dict[str, Any]:
    """构造 ``item_list`` 里的 image_item（type=2 + media 引用）。"""
    return {
        "type": ITEM_TYPE_IMAGE,
        "image_item": {
            "media": {
                "encrypt_query_param": uploaded.download_encrypted_query_param,
                "aes_key": _aes_key_field(uploaded.aeskey_hex),
                "encrypt_type": 1,
            },
            "mid_size": uploaded.file_size_ciphertext,
        },
    }


def build_message(
    *,
    to_user_id: str,
    item: dict[str, Any],
    context_token: str,
    client_id: str,
) -> dict[str, Any]:
    """iLink sendmessage 的 msg 信封（media 类共用 message_type=2/BOT）。"""
    return {
        "from_user_id": "",
        "to_user_id": to_user_id,
        "client_id": client_id,
        "message_type": MESSAGE_TYPE_BOT,
        "message_state": 2,
        "item_list": [item],
        "context_token": context_token,
    }
