import base64
import io
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Security

from api.auth import verify_api_key_dep

logger = logging.getLogger("qrcode_store")

QRCODE_FILE = Path(__file__).parent.parent / "data" / "wechat_qrcode.json"
router = APIRouter(prefix="/api/wechat", tags=["wechat"])


def _read_qrcode_data() -> dict:
    if QRCODE_FILE.exists():
        try:
            with open(QRCODE_FILE) as f:
                return json.load(f)  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to read qrcode file: %s", e)
    return {"qrcode_url": "", "status": "idle", "timestamp": 0}


def save_qrcode(qrcode_url: str, status: str = "waiting"):
    QRCODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"qrcode_url": qrcode_url, "status": status, "timestamp": time.time()}
    try:
        with open(QRCODE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to save qrcode: %s", e)


QRCODE_EXPIRY_SECONDS = 600


def is_expired() -> bool:
    data = _read_qrcode_data()
    return (time.time() - data.get("timestamp", 0)) >= QRCODE_EXPIRY_SECONDS  # type: ignore[no-any-return]


def _generate_qr_image(url: str) -> str | None:
    try:
        import qrcode as qr_lib
        qr = qr_lib.QRCode(error_correction=qr_lib.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{b64}"
    except ImportError:
        logger.warning("qrcode package not installed, cannot generate image")
        return None


@router.get("/qrcode")
def get_qrcode(_auth: bool = Security(verify_api_key_dep)):
    data = _read_qrcode_data()
    url = data.get("qrcode_url", "")
    has_new_qr = url and (time.time() - data.get("timestamp", 0)) < QRCODE_EXPIRY_SECONDS

    qr_image = None
    if has_new_qr:
        qr_image = _generate_qr_image(url)

    return {
        "status": data.get("status", "idle"),
        "qrcode_url": url if has_new_qr else "",
        "qr_image": qr_image or "",
        "timestamp": data.get("timestamp", 0),
        "is_expired": is_expired(),
        "message": "请使用微信扫描二维码登录" if has_new_qr else "等待二维码生成...",
    }
